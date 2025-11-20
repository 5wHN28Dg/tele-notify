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

**⚠️ Disclaimer**: This project is a custom-built solution for a specific problem I encountered, designed solely to meet my personal needs, read the full article [here](https://medium.com/@TogataMirio/a-needle-in-a-haystack-and-the-deadly-pursuit-to-prove-your-worth-88c34f1df79f). It is not intended for high-volume use or scenarios that might approach API rate limits. Features outside my requirements have not been implemented, so you may need to adapt or modify the code to fit your own use case.

## ✨ Features

* **Keyword-based filtering**: Matches at least one keyword from each of three keyword categories.
* **Image text recognition**: Downloads attached images and extracts text using OCR (`pytesseract`).
* **Duplicate prevention**: Checks your last 10 forwarded messages before sending a new one.
* **Customizable filters**: Store your keywords and Telegram API keys in a JSON file.
* **Multi-chat monitoring**: Watch multiple Telegram chats at once.

## 🎯 Version Guide

On October 7th, 2025, the project reached a divergence point — a moment where I had to choose between two paths:
specificity, at the expense of ease of repurposing, or generality, at the expense of reliability for my current use case.

In the end, I chose both. I wanted the strengths of each approach, so now this project provides two distinct versions, each tailored to different needs:

### **v2 (Current)** - Specialized Job Filter
**Best for:** Filtering job postings with intelligent level detection
- Two-stage filtering with inference logic
- Entry-level vs. mid-level classification
- Experience, certification, and responsibility pattern matching
- Optimized for English/Arabic job market terminology
- **Trade-off:** Highly tailored for my very specific application; requires significant modification for other use cases but only some adjustments to be used as a specialized job filtering

### **v1 (Legacy)** - General-ish Message Filter
**Best for:** Simple keyword-based filtering for any content type
- Straightforward AND logic (level + role + location)
- Easy to repurpose for different domains (e.g., real estate, events, products)
- Minimal configuration required
- **Trade-off:** Less intelligent; may miss nuanced matches

📁 **Files:**
- `main.py` - Current specialized version (v2)
- `main_simple.py` - Original general-purpose version (v1)

💡 **Which should you use?**
- Filtering job postings specifically? → Use v2
- Need a simple keyword filter for other content? → Use v1
- Want to build something custom? → Start with v1 as a template

---

## 🔧 Customization Difficulty

| Feature | v1 (Simple) | v2 (Specialized) |
|---------|-------------|------------------|
| Add new keywords | Easy | Easy |
| Change filter logic | Moderate | Complex |
| Repurpose for different domain | Moderate | Very Complex |
| Add new languages | Moderate | Challenging |

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
Beautiful Soup
lxml
```

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

## 📜 License

This project is licensed under the AGPL License.
