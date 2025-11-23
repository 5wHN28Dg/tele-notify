<div align="center">

[🇮🇶 العربية](README.ar.md)

</div>

# tele-notify

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python](https://img.shields.io/badge/Python-3.8%2B-blue)](https://www.python.org/)
[![Telethon](https://img.shields.io/badge/Library-Telethon-green)](https://docs.telethon.dev/)

[![asciicast](https://asciinema.org/a/h8qYjhK6LsRqMchP6L93N1qu5.svg)](https://asciinema.org/a/h8qYjhK6LsRqMchP6L93N1qu5)

A Python script that monitors specified Telegram chats and forwards messages matching your custom filters to another chat.
It also supports OCR on attached images and avoids forwarding duplicate messages.

> ### Elevator pitch:
> 
> **Me**: "Here is my phone. You see these 31 Telegram channels? I want you to find me a job. It must be for fresh grads, in my city, specifically for a B.Sc. in Control & Computer Engineering. Oh, and check every single one of them every day."
> 
> **Friend**: "Bro, are you serious?!"
> 
> **Me**: "Exactly. That is why I wrote this code."

## ✨ Features

* **Keyword-based filtering**: Matches at least one keyword from each of three keyword categories.
* **Image text recognition**: Downloads attached images and extracts text using OCR (`pytesseract`).
* **Duplicate prevention**: Checks your last 10 forwarded messages before sending a new one.
* **Customizable filters**: Store your keywords and Telegram API keys in a JSON file.
* **Multi-chat monitoring**: Watch multiple Telegram chats at once.

## 🎯 Version Guide

On Oct 7th, 2025, this project split into two paths:

| Feature | v1 (Simple) `main_simple.py` | v2 (Specialized) `main.py` |
|---------|-------------|------------------|
| Best For | 📢 General Monitoring | 💼 Job Hunting |
| Logic | Simple Keywords (A + B + C) | Smart Inference (Experience, Role, Level) |
| Complexity | Low (Easy to customize) | High (Harder to customize) |
| Use Case | "Find me any message with 'Apartment' and 'Rent'" | "Find me a Mid-Level Python job" |

## 📦 Requirements

* Python 3.8+
* Telegram API credentials (API ID & API Hash, check [Telethon documentation](https://docs.telethon.dev/en/stable/basic/signing-in.html#signing-in) for detailed instructions)
* `tesseract-ocr` for OCR functionality

## ⚙️ Installation

1. clone the repository:

   ```bash
   git clone https://github.com/5wHN28Dg/tele-notify.git
   cd tele-notify
   ```
2. create a virtual environment:

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
     sudo apt install tesseract-ocr tesseract-ocr-eng tesseract-ocr-ara
     ```
   * **Windows**: [Download installer](https://github.com/UB-Mannheim/tesseract/wiki)
   * **MacOS**:

     ```bash
     brew install tesseract tesseract-lang
     ```
   * **Note**: If you encounter any issues or difficulties with Tesseract installation, refer to the [official documentation](https://tesseract-ocr.github.io/tessdoc/Installation.html) or community forums.

> 📱 **Android Users:** check the [wiki](https://github.com/5wHN28Dg/tele-notify/wiki/Android-Instructions) for detailed instructions.

## 🛠 Configuration

1. Run `get_chat_ids.py` to get a list of your chat list with their names and **IDs** (after you fill in your API ID and API Hash):

   ```bash
   python get_chat_ids.py
   ```
2. Open `config.json` file in the project directory and fill it with the necessary information:

  * Your API ID and API Hash.
  * the IDs of the chats you want to watch.
  * the ID of the chat you want to forward messages to.
  * the keywords you want to filter messages based on.

  **Note**: do not touch `recent_messages`.

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
* if you have any questions, you may want to check the [FAQ](https://github.com/5wHN28Dg/tele-notify/wiki/FAQ)

## 🗺️ Roadmap

Development is organized by milestones.

See full progress → [GitHub Milestones](https://github.com/5wHN28Dg/tele-notify/milestones)

See what's being worked on → [Project Board](https://github.com/users/5wHN28Dg/projects/1)

**⚠️ Disclaimer**: This project is a custom-built solution for a specific problem I encountered, designed solely to meet my personal needs, read the full article [here](https://medium.com/@TogataMirio/a-needle-in-a-haystack-and-the-deadly-pursuit-to-prove-your-worth-88c34f1df79f). It is not intended for high-volume use or scenarios that might approach API rate limits. Features outside my requirements have not been implemented, so you may need to adapt or modify the code to fit your own use case.

## 📜 License

This project is licensed under the AGPL License.
