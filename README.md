# tele-notify

A Python script that monitors specified Telegram chats and forwards messages matching your custom filters to another chat.
It also supports OCR on attached images and avoids forwarding duplicate messages.

## ✨ Features

* **Keyword-based filtering**: Matches at least one keyword from each of three keyword categories.
* **Image text recognition**: Downloads attached images and extracts text using OCR (`pytesseract`).
* **Duplicate prevention**: Checks your last 10 forwarded messages before sending a new one.
* **Customizable filters**: Store your keywords and Telegram API keys in a JSON file.
* **Multi-channel monitoring**: Watch multiple Telegram channels at once.

## 📦 Requirements

* Python 3.8+
* Telegram API credentials (API ID & API Hash)
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

1. Download main.py, requirments.txt or clone the repository:

   ```bash
   git clone https://github.com/5wHN28Dg/tele-notify.git
   cd tele-notify
   ```
2. create a virutal environment:

  ```bash
  cd path/to/main.py_and_requirements.txt
  python3 -m venv venv
  ```

3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```
4. Install Tesseract OCR:

   * **Ubuntu/Debian**:

     ```bash
     sudo apt install tesseract-ocr
     sudo apt install tesseract-ocr-eng #or any language you want
     sudo apt install tesseract-ocr-ara
     ```
   * **Windows**: [Download installer](https://github.com/UB-Mannheim/tesseract/wiki)
   * **MacOS**:

     ```bash
     brew install tesseract
     ```

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

1. login as the user (after you enter your phone number and code...)
2. starts watching the specified Telegram chats.
3. starts processing unread messages 1st.
4. For each new message:

   * Extract text from the message body and image (if present).
   * Check for required keywords.
   * Skip if it’s a duplicate of one of your last 10 messages.
   * Forward it to your target channel.

## 📜 License

This project is licensed under the AGPL License.
