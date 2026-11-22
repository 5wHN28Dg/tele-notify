## parseMessage on TDLib JSON written after td_api.tl (TDLib 1.8.70).

import std/[unittest, json]
import telenotify/telegram

suite "parseMessage":
  test "text with a hidden link and URL buttons":
    let m = parseMessage(%*{"@type": "message", "id": 77, "chat_id": -1001, "date": 1700000000,
      "content": {"@type": "messageText", "text": {"@type": "formattedText",
        "text": "مطلوب مهندس كهرباء، التفاصيل هنا",
        "entities": [{"@type": "textEntity", "offset": 25, "length": 3,
                      "type": {"@type": "textEntityTypeTextUrl", "url": "https://khana.jobs/jobs/9915"}}]}},
      "reply_markup": {"@type": "replyMarkupInlineKeyboard", "rows": [[
        {"@type": "inlineKeyboardButton", "text": "Apply",
         "type": {"@type": "inlineKeyboardButtonTypeUrl", "url": "https://taeen.iq/ar/jobs/x"}},
        {"@type": "inlineKeyboardButton", "text": "Like",
         "type": {"@type": "inlineKeyboardButtonTypeCallback", "data": "AA=="}}]]}})
    check m.id == 77 and m.chatId == -1001
    check m.text == "مطلوب مهندس كهرباء، التفاصيل هنا\nhttps://khana.jobs/jobs/9915"
    check m.buttons == @["https://taeen.iq/ar/jobs/x"]   # the callback button is ignored
    check m.photoFileId == 0

  test "the post's date and its last edit's (#74)":
    let m = parseMessage(%*{"id": 9, "chat_id": 1, "date": 1700000000, "edit_date": 1700003600,
      "content": {"@type": "messageText", "text": {"text": "x"}}})
    check m.date == 1700000000 and m.editDate == 1700003600
    check parseMessage(%*{"id": 9, "chat_id": 1, "date": 5}).editDate == 0   # never edited

  test "photo: caption and the largest reasonable size":
    let m = parseMessage(%*{"id": 5, "chat_id": 1, "content": {"@type": "messagePhoto",
      "caption": {"text": "#Basrah", "entities": []},
      "photo": {"sizes": [{"type": "m", "width": 320, "photo": {"id": 11}},
                          {"type": "x", "width": 1280, "photo": {"id": 12}},
                          {"type": "w", "width": 5000, "photo": {"id": 13}}]}}})
    check m.text == "#Basrah"
    check m.photoFileId == 12   # 5000 px would be refused by the OCR size check

  test "other media keep their caption; empty messages don't crash":
    check parseMessage(%*{"content": {"@type": "messageDocument", "caption": {"text": "CV"}}}).text == "CV"
    check parseMessage(%*{"content": {"@type": "messageSticker"}}).text == ""
    check parseMessage(%*{}).id == 0

  test "album id (TDLib sends 64-bit ids as strings)":
    let m = parseMessage(%*{"chat_id": -1, "id": 5, "media_album_id": "13867035834384742",
                            "content": {"@type": "messagePhoto", "caption": {"text": ""}}})
    check m.albumId == 13867035834384742'i64
    check parseMessage(%*{"id": 6, "content": {"@type": "messageText", "text": {"text": "x"}}}).albumId == 0

suite "chat access (#16)":
  test "updateSupergroup: a channel that banned you, or that you left":
    let (chat, a) = parseAccessUpdate(%*{"@type": "updateSupergroup", "supergroup": {
      "@type": "supergroup", "id": 1234567890, "is_channel": true,
      "status": {"@type": "chatMemberStatusBanned", "banned_until_date": 0}}})
    check chat == -1001234567890'i64 and a.status == "banned"
    check parseAccessUpdate(%*{"@type": "updateSupergroup", "supergroup": {"id": 5,
      "status": {"@type": "chatMemberStatusLeft"}}}).access.status == "left"
    check parseAccessUpdate(%*{"@type": "updateSupergroup", "supergroup": {"id": 5,
      "status": {"@type": "chatMemberStatusMember"}}}).access.status == "ok"

  test "restricted: still reading unless not a member":
    check memberAccess(%*{"@type": "chatMemberStatusRestricted", "is_member": true}) == "ok"
    check memberAccess(%*{"@type": "chatMemberStatusRestricted", "is_member": false}) == "left"
    check memberAccess(%*{"@type": "chatMemberStatusAdministrator"}) == "ok"
    check memberAccess(%*{"@type": "chatMemberStatusCreator"}) == "ok"
    check memberAccess(newJNull()) == ""

  test "updateBasicGroup: kicked, and upgraded to a supergroup":
    let (chat, a) = parseAccessUpdate(%*{"@type": "updateBasicGroup", "basic_group": {
      "@type": "basicGroup", "id": 77, "is_active": true,
      "status": {"@type": "chatMemberStatusBanned"}}})
    check chat == -77 and a.status == "banned"
    let (chat2, b) = parseAccessUpdate(%*{"@type": "updateBasicGroup", "basic_group": {
      "id": 78, "is_active": false, "upgraded_to_supergroup_id": 9,
      "status": {"@type": "chatMemberStatusMember"}}})
    check chat2 == -78 and b.status == "upgraded" and b.newChat == -1000000000009'i64
    check parseAccessUpdate(%*{"@type": "updateBasicGroup", "basic_group": {
      "id": 79, "is_active": false, "status": {"@type": "chatMemberStatusMember"}}}).access.status ==
      "unavailable"

  test "other updates are about no chat":
    check parseAccessUpdate(%*{"@type": "updateNewMessage"}).chat == 0
    check parseAccessUpdate(%*{"@type": "updateSupergroup"}).chat == 0

  test "access errors vs passing problems":
    check isAccessError("CHANNEL_PRIVATE (400)")
    check isAccessError("Chat not found (400)")
    check isAccessError("Can't access the chat (400)")
    check not isAccessError("no answer from TDLib after 300 s")
    check not isAccessError("TDLib closed")
    check not isAccessError("Too Many Requests: retry after 5 (429)")

suite "public channels (#16 review)":
  test "a supergroup with an active username is public":
    let (chat, a) = parseAccessUpdate(%*{"@type": "updateSupergroup", "supergroup": {"id": 5,
      "status": {"@type": "chatMemberStatusLeft"}, "usernames": {"active_usernames": ["examplejobs"]}}})
    check chat == -1000000000005 and a.status == "left" and a.public
    let (_, b) = parseAccessUpdate(%*{"@type": "updateSupergroup", "supergroup": {"id": 5,
      "status": {"@type": "chatMemberStatusLeft"}, "usernames": {"active_usernames": []}}})
    check not b.public

  test "no username list at all is not public, and not a crash":
    let (_, a) = parseAccessUpdate(%*{"@type": "updateSupergroup", "supergroup": {"id": 5,
      "status": {"@type": "chatMemberStatusLeft"}}})
    check not a.public
