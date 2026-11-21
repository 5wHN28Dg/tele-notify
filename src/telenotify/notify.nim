## Notifications through your own Telegram bot (#122).
##
## Messages in Saved Messages never notify you (you wrote them yourself), so
## the bot can also tell you through a bot of your own: make one with
## @BotFather, press Start in its chat, and put its token in config.json as
## `notify_bot_token`. You then get a normal Telegram notification, on your
## phone and your laptop, when a post goes to the review queue, when one is
## published to your channel, and for every alert.
##
## The message goes to your own user id (your private chat with the bot),
## through the Bot API's `sendMessage`. The token is part of the URL, so it
## is kept out of every error and log line.

import std/[asyncdispatch, strutils, uri, unicode]
import fetch

const maxText = 4096   ## Telegram's limit for one message

type
  Notifier* = proc (text: string): Future[string] {.closure, gcsafe.}
    ## Send `text` to you; "" when sent, else why not (without the token).
  ChatId* = proc (): Future[int64] {.closure, gcsafe.}
    ## Your own user id: the chat with your bot.

proc clip(text: string): string =
  if text.runeLen <= maxText: return text
  text.runeSubStr(0, maxText - 1) & "…"

proc hide(s, token: string): string =
  if token == "": s else: s.replace(token, "<token>")

proc botNotifier*(token: string, chatId: ChatId, fetcher: Fetcher): Notifier =
  result = proc (text: string): Future[string] {.async, closure, gcsafe.} =
    try:
      let chat = await chatId()
      let url = "https://api.telegram.org/bot" & token & "/sendMessage?" &
        encodeQuery({"chat_id": $chat, "text": clip(text), "disable_web_page_preview": "true"})
      let r = await fetcher(url)
      if r.ok: return ""
      return hide(if r.error != "": r.error else: "HTTP " & $r.status, token)
    except CatchableError as e:
      return hide(e.msg, token)
