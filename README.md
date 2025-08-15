# tele-notify

A Python script that monitors specified Telegram chats and forwards messages matching your custom filters to another chat.
It also supports OCR on attached images and avoids forwarding duplicate messages.

## ✨ Features

* **Keyword-based filtering**: Matches at least one keyword from each of three keyword categories.
* **Image text recognition**: Downloads attached images and extracts text using OCR (`pytesseract`).
* **Duplicate prevention**: Checks your last 10 forwarded messages before sending a new one.
* **Customizable filters**: Store your keywords and Telegram API keys in a JSON file.
* **Multi-chat monitoring**: Watch multiple Telegram chats at once.

## 📦 Requirements

* Python 3.8+
* Telegram API credentials (API ID & API Hash, check [Telethon documentation](https://docs.telethon.dev/en/stable/basic/signing-in.html#signing-in) for detailed instructions)
* Installed `tesseract-ocr` on your system (for OCR functionality)

## 📚 Dependencies

Python libraries used:

```txt
telethon
pillow
pytesseract
scikit-learn
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
     brew install tesseract
     ```
   * **Note**: If you encounter any issues or difficulties with Tesseract installation, refer to the [official documentation](https://tesseract-ocr.github.io/tessdoc/Installation.html) or community forums.

## 🛠 Configuration

1. Create a `config.json` file in the project directory with the following structure:

   ```json
   {
     "api_id": "YOUR_API_ID",
     "api_hash": "YOUR_API_HASH",
     "keywords_category_1": ["keyword1", "keyword2"],
     "keywords_category_2": ["keyword3", "keyword4"],
     "keywords_category_3": ["keyword5", "keyword6"],
     "target_chat": "username",
     "chats": ["username", "username", ...]
   }
   ```

## 🚀 Usage

Run the script:

```bash
python main.py
```

The script will:

1. login as the user (after entering your phone number and code...)
2. starts watching the specified Telegram chats.
3. starts processing unread messages 1st.
4. For each new message:

   * Extract text from the message body and image (if present).
   * Check for required keywords.
   * Skip if it’s a duplicate of one of your last 10 messages.
   * Forward it to your target chat.

## 📜 License

This project is licensed under the AGPL License.
