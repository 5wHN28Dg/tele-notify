<div align="center">
   
[🇮🇶 العربية](README.ar.md) | [🇬🇧 English](README.md)

</div>

# tele-notify

A Python script that monitors specified Telegram chats and forwards messages matching your custom filters to another chat.
It also supports OCR on attached images and avoids forwarding duplicate messages.

**⚠️ Disclaimer**: This project is a custom-built solution for a specific problem I encountered, designed solely to meet my personal needs. It is not intended for high-volume use or scenarios that might approach API rate limits. Features outside my requirements have not been implemented, so you may need to adapt or modify the code to fit your own use case.

## ✨ Features

* **Keyword-based filtering**: Matches at least one keyword from each of three keyword categories.
* **Image text recognition**: Downloads attached images and extracts text using OCR (`pytesseract`).
* **Duplicate prevention**: Checks your last 10 forwarded messages before sending a new one.
* **Customizable filters**: Store your keywords and Telegram API keys in a JSON file.
* **Multi-chat monitoring**: Watch multiple Telegram chats at once.

## 📦 Requirements

* Python 3.8+
* Telegram API credentials (API ID & API Hash, check [Telethon documentation](https://docs.telethon.dev/en/stable/basic/signing-in.html#signing-in) for detailed instructions)
* `tesseract-ocr` for OCR functionality

## 📚 Dependencies

Python libraries used:

```txt
telethon
pillow
pytesseract
scikit-learn
cryptg
tenacity
```

## ⚙️ Installation

1. clone the repository:

   ```bash
   git clone https://github.com/5wHN28Dg/tele-notify.git
   cd tele-notify
   ```
2. create a virutal environment:

   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```
4. Install Tesseract OCR:

   * **Ubuntu/Debian**:

     ```bash
     sudo apt install tesseract-ocr tesseract-ocr-eng tesseract-ocr-ara # or any language you want
     ```
   * **Windows**: [Download installer](https://github.com/UB-Mannheim/tesseract/wiki)
   * **MacOS**:

     ```bash
     brew install tesseract tesseract-lang
     ```
   * **Note**: If you encounter any issues or difficulties with Tesseract installation, refer to the [official documentation](https://tesseract-ocr.github.io/tessdoc/Installation.html) or community forums.

## 🛠 Configuration

1. Run this code (after you fill in your API ID and API Hash) to get a list of your chat list with their names and **IDs**:
   ```python
   from telethon import TelegramClient

   api_id = YOUR_API_ID
   api_hash = 'YOUR_API_HASH'

   client = TelegramClient('session_name', api_id, api_hash)

   async def main():
       async for dialog in client.iter_dialogs():
           print('{:>14}: {}'.format(dialog.id, dialog.title))

   with client:
    client.loop.run_until_complete(main())
   ```
2. Create a `config.json` file in the project directory with the following structure:

   ```json
   {
     "api_id": YOUR_API_ID,
     "api_hash": "YOUR_API_HASH",
     "target_chat": chat_id,
     "chats": [chat_id, chat_id],
     "level_keywords_en": ["keyword1", "keyword2"],
     "level_keywords_ar": ["keyword3", "keyword4"],
     "role_keywords_en": ["keyword1", "keyword2"],
     "role_keywords_ar": ["keyword3", "keyword4"],
     "location_keywords_en": ["keyword1", "keyword2"],
     "location_keywords_ar": ["keyword3", "keyword4"],
     "recent_messages": []
   }
   ```

## 🚀 Usage

Run the script:

```bash
python main.py
```

The script will:

1. ask you to login as the user by entering your phone number and code.
2. starts watching the specified Telegram chats.
3. starts processing unread messages if there are any and watch for new messages:
   * Extract text from the message body and image (if present).
   * Check for required keywords.
   * Skip if it’s a duplicate of one of your last 10 messages.
   * Forward it to your target chat.

## 📝 To Do List:
- [ ] Fix race conditions when updating recent_messages / writing to config.json. 🔄
- [ ] Special processing for messages with info formatted like this: `#Basrah www.example.com/electrical-engineering-intern/`.
- [ ] Set up a way for you to get notified if the script carshes.
- [ ] A beautiful CLI with real-time statistics instead of this infinite stream of text.
- [ ] analyze the code for a possible 2nd refactoring.

## 📜 License

This project is licensed under the AGPL License.
