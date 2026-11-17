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
