## What the listener needs from Telegram, and how TDLib provides it.
##
## `Telegram` is a small set of operations (get unread messages, mark read,
## forward, ...). The listener only ever uses this object, never TDLib
## directly, so tests can pass a fake Telegram (see tests/tlistener.nim)
## and run the whole bot logic without an account or a network.
##
## `tdTelegram` builds the real one on top of a TdClient, and
## `parseMessage` turns TDLib's message JSON into a `TgMessage`. That
## function is pure and tested against hand-written TDLib JSON.

import std/[asyncdispatch, json, strutils, algorithm]
import td

type
  TgMessage* = object
    chatId*, id*: int64
    date*: int64
    text*: string        ## text or caption, plus hidden link targets
    buttons*: seq[string] ## URLs of inline buttons
    photoFileId*: int    ## TDLib file id of the largest photo size; 0 = none
    chatTitle*: string
    albumId*: int64      ## same non-zero id for every picture of one album
    edited*: bool        ## comes from an edit, not a new message
    outgoing*: bool      ## sent by this account (e.g. by the Python bot)
    originChat*, originMsg*: int64  ## for a forward from a channel: where it came from

  Unread* = object
    messages*: seq[TgMessage]  ## oldest first
    cut*: bool                 ## more unread messages, older than these, were left out

  Telegram* = ref object
    ## Every operation the listener uses. All of them may raise (network,
    ## TDLib errors); the listener catches per message.
    unread*: proc (chat: int64): Future[Unread] {.closure, gcsafe.}
      ## unread messages of `chat` (at most `maxBacklog`)
    markRead*: proc (chat: int64, ids: seq[int64]): Future[void] {.closure, gcsafe.}
    downloadPhoto*: proc (fileId: int): Future[string] {.closure, gcsafe.}
      ## the photo's bytes
    forward*: proc (target, chat: int64, ids: seq[int64]): Future[bool] {.closure, gcsafe.}
      ## forward one message or a whole album; false when the chat forbids
      ## forwarding (or Telegram refused)
    getMessage*: proc (chat, id: int64): Future[TgMessage] {.closure, gcsafe.}
      ## one message as it is now (used for edits)
    linkTarget*: proc (url: string): Future[(int64, int64)] {.closure, gcsafe.}
      ## the chat and message a t.me link points to
    link*: proc (chat, id: int64): Future[string] {.closure, gcsafe.}
    sendText*: proc (chat: int64, text: string): Future[void] {.closure, gcsafe.}
    selfChat*: proc (): Future[int64] {.closure, gcsafe.}
      ## the chat id of Saved Messages, for alerts

# ------------------------------------------------------------ parsing

proc textWithLinks(ft: JsonNode): string =
  ## A formattedText's text, plus the URLs hidden behind words
  ## ("apply [here](https://...)"): the old bot never saw those.
  result = ft{"text"}.getStr
  if ft == nil or ft{"entities"} == nil: return
  for e in ft["entities"]:
    if e{"type", "@type"}.getStr == "textEntityTypeTextUrl":
      result.add "\n" & e{"type", "url"}.getStr

proc parseMessage*(m: JsonNode): TgMessage =
  result.chatId = m{"chat_id"}.getBiggestInt
  result.id = m{"id"}.getBiggestInt
  result.date = m{"date"}.getBiggestInt
  result.outgoing = m{"is_outgoing"}.getBool
  let origin = m{"forward_info", "origin"}
  if origin{"@type"}.getStr == "messageOriginChannel":
    result.originChat = origin{"chat_id"}.getBiggestInt
    result.originMsg = origin{"message_id"}.getBiggestInt
  # TDLib writes 64-bit ids as strings in JSON
  let album = m{"media_album_id"}
  if album != nil:
    result.albumId = (if album.kind == JString: (try: parseBiggestInt(album.getStr) except ValueError: 0'i64)
                      else: album.getBiggestInt)
  let content = m{"content"}
  case content{"@type"}.getStr
  of "messageText":
    result.text = content{"text"}.textWithLinks
  of "messagePhoto":
    result.text = content{"caption"}.textWithLinks
    var best = 0
    for s in content{"photo", "sizes"}.getElems:
      let w = s{"width"}.getInt
      if w >= best and w <= 2560:
        best = w
        result.photoFileId = s{"photo", "id"}.getInt
  else:
    # videos, documents, animations...: only their caption is useful
    result.text = content{"caption"}.textWithLinks
  let markup = m{"reply_markup"}
  if markup{"@type"}.getStr == "replyMarkupInlineKeyboard":
    for row in markup{"rows"}.getElems:
      for b in row.getElems:
        if b{"type", "@type"}.getStr == "inlineKeyboardButtonTypeUrl":
          result.buttons.add b{"type", "url"}.getStr

# ------------------------------------------------------------ TDLib

const maxBacklog* = 10_000   ## per chat; job posts older than that are stale anyway

proc tdTelegram*(c: TdClient): Telegram =
  result = Telegram()

  result.unread = proc (chat: int64): Future[Unread] {.async, closure, gcsafe.} =
    let info = await c.call(%*{"@type": "getChat", "chat_id": chat})
    # The real read marker (the old bot computed "last id - unread count",
    # which skips messages whenever a channel deletes posts).
    let lastRead = info{"last_read_inbox_message_id"}.getBiggestInt
    if info{"unread_count"}.getInt == 0: return Unread()
    let title = info{"title"}.getStr
    var collected: seq[TgMessage]
    var fromId = 0'i64
    var complete = false
    while collected.len < maxBacklog:
      let batch = await c.call(%*{"@type": "getChatHistory", "chat_id": chat,
        "from_message_id": fromId, "offset": 0, "limit": 100, "only_local": false})
      let msgs = batch{"messages"}.getElems
      if msgs.len == 0:
        complete = true
        break
      var reachedRead = false
      for m in msgs:                # newest first
        let id = m{"id"}.getBiggestInt
        # with offset 0 a page starts *at* from_message_id, which the
        # previous page already had
        if id == fromId: continue
        if id <= lastRead:
          reachedRead = true
          break
        var tm = parseMessage(m)
        tm.chatTitle = title
        collected.add tm
      let oldest = msgs[^1]{"id"}.getBiggestInt
      if reachedRead or oldest == fromId:   # read marker or start of the chat
        complete = true
        break
      fromId = oldest
    collected.sort(proc (a, b: TgMessage): int = cmp(a.id, b.id))   # oldest first
    return Unread(messages: collected, cut: not complete)

  result.markRead = proc (chat: int64, ids: seq[int64]): Future[void] {.async, closure, gcsafe.} =
    if ids.len == 0: return
    discard await c.call(%*{"@type": "viewMessages", "chat_id": chat, "message_ids": ids,
                            "source": nil, "force_read": true})

  result.downloadPhoto = proc (fileId: int): Future[string] {.async, closure, gcsafe.} =
    let f = await c.call(%*{"@type": "downloadFile", "file_id": fileId, "priority": 1,
                            "offset": 0, "limit": 0, "synchronous": true})
    let path = f{"local", "path"}.getStr
    if path == "": return ""
    result = readFile(path)
    # we have the bytes; don't let a 24/7 bot fill the disk with photos
    try: discard await c.call(%*{"@type": "deleteFile", "file_id": fileId})
    except CatchableError: discard

  result.forward = proc (target, chat: int64, ids: seq[int64]): Future[bool] {.async, closure, gcsafe.} =
    if ids.len == 0: return false
    let props = await c.call(%*{"@type": "getMessageProperties", "chat_id": chat, "message_id": ids[0]})
    if not props{"can_be_forwarded"}.getBool(false): return false
    let sent = await c.call(%*{"@type": "forwardMessages", "chat_id": target, "from_chat_id": chat,
                               "message_ids": ids, "send_copy": false, "remove_caption": false})
    # TDLib puts null in place of a message it could not forward
    let msgs = sent{"messages"}.getElems
    if msgs.len != ids.len: return false
    for m in msgs:
      if m.kind != JObject: return false
    return true

  result.linkTarget = proc (url: string): Future[(int64, int64)] {.async, closure, gcsafe.} =
    let info = await c.call(%*{"@type": "getMessageLinkInfo", "url": url})
    return (info{"chat_id"}.getBiggestInt, info{"message", "id"}.getBiggestInt)

  result.getMessage = proc (chat, id: int64): Future[TgMessage] {.async, closure, gcsafe.} =
    return parseMessage(await c.call(%*{"@type": "getMessage", "chat_id": chat, "message_id": id}))

  result.link = proc (chat, id: int64): Future[string] {.async, closure, gcsafe.} =
    let l = await c.call(%*{"@type": "getMessageLink", "chat_id": chat, "message_id": id})
    return l{"link"}.getStr

  result.sendText = proc (chat: int64, text: string): Future[void] {.async, closure, gcsafe.} =
    discard await c.call(%*{"@type": "sendMessage", "chat_id": chat,
      "input_message_content": {"@type": "inputMessageText",
        "text": {"@type": "formattedText", "text": text, "entities": []}}})

  result.selfChat = proc (): Future[int64] {.async, closure, gcsafe.} =
    let me = await c.call(%*{"@type": "getMe"})
    let chat = await c.call(%*{"@type": "createPrivateChat", "user_id": me{"id"}.getBiggestInt,
                               "force": false})
    return chat{"id"}.getBiggestInt
