from telethon import TelegramClient, events, errors
import logging, re, asyncio, io, json
from PIL import Image
import pytesseract
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# logging for easier debugging
logging.basicConfig(format='[%(levelname) %(asctime)s] %(name)s: %(message)s',
                    level=logging.INFO)

with open('config.json', 'r') as f:
    data = json.load(f)

# Use your own values from my.telegram.org and KEEP THEM PRIVATE!
api_id = data['api_id']
api_hash = data['api_hash']
client = TelegramClient('test', api_id, api_hash)

# chats to monitor
chats = data['chats']
target_chat = data['target_chat']

# keywords
level_keywords_en = data['level_keywords_en']
level_keywords_ar = data['level_keywords_ar']
role_keywords_en = data['role_keywords_en']
role_keywords_ar = data['role_keywords_ar']
location_keywords_en = data['location_keywords_en']
location_keywords_ar = data['location_keywords_ar']

# Function to build hybrid regex: \b for Latin, no \b for Arabic
def build_hybrid_regex(en_list, ar_list):
    parts = []
    if en_list:
        parts.append(r'\b(?:' + '|'.join(re.escape(k) for k in en_list) + r')\b')
    if ar_list:
        parts.append(r'(?:' + '|'.join(re.escape(k) for k in ar_list) + r')')
    return re.compile('|'.join(parts), re.IGNORECASE) if parts else re.compile(r'$.')

# Compile patterns
level_pattern = build_hybrid_regex(level_keywords_en, level_keywords_ar)
role_pattern = build_hybrid_regex(role_keywords_en, role_keywords_ar)
location_pattern = build_hybrid_regex(location_keywords_en, location_keywords_ar)

# -------- helpers --------
# check if the message passes the filters
async def check_for_a_match(message):
    full_message = await message_processor(message)

    return all([
            level_pattern.search(full_message),
            role_pattern.search(full_message),
            location_pattern.search(full_message)
        ]), full_message

# ckecks the message type then makes it ready for processing
async def message_processor(msg):
    message_text = ""
    if hasattr(msg, 'photo') and msg.photo:
        try:
            message_text = await image_processor(msg)
        except Exception as e:
            logging.error(f"OCR failed: {e}")

    text_content = msg.text or ""
    full_message = message_text + " " + text_content
    return full_message

# downloads then extracts the image text
async def image_processor(msg):
    image_bytes = await msg.download_media(file=bytes)
    with Image.open(io.BytesIO(image_bytes)) as image:
        image_text = pytesseract.image_to_string(image, lang='ara+eng')
    return image_text

# compares the message to be sent with the last 10 sent messages
async def check_for_duplicates(message_text):
    try:
        recent_messages = []
        async for sent_message in client.iter_messages(target_chat, limit = 10):
            if sent_message:
                full_message = await message_processor(sent_message)
                recent_messages.append(full_message)

            if not recent_messages:
                return False

        text = recent_messages + [message_text]
        vectorizer = TfidfVectorizer(stop_words=None, token_pattern=r"(?u)\b\w+\b")
        vectors = vectorizer.fit_transform(text)

         # Compare new message with each recent message
        for i in range(len(recent_messages)):
                    similarity = cosine_similarity(vectors[i:i+1], vectors[-1:])[0, 0]
                    if similarity > 0.8:
                        return True  # Duplicate found
        return False

    except Exception as e:
        print(f"Error checking for duplicates: {e}")
        return False

async def message_forwarder(msg):
    try:
        await msg.forward_to(target_chat)
        await asyncio.sleep(1)
    except errors.FloodWaitError as e:
        # e.seconds is how many seconds you have
        # to wait before making the request again.
        print('Flood for', e.seconds)
        await asyncio.sleep(e.seconds)
        await msg.forward_to(target_chat)

# -------- unread bootstrap --------
async def unread_messages_retriever():
    try:
        dialogs = await client.get_dialogs()
        for dialog in dialogs:
            if dialog.entity.username in chats:
                if dialog.unread_count > 0:
                    last_read_id = dialog.message.id - dialog.unread_count
                    async for msg in client.iter_messages(dialog.id, min_id=last_read_id):
                        is_match, full_message = await check_for_a_match(msg)
                        if is_match:
                            if not await check_for_duplicates(full_message):
                                await message_forwarder(msg)
                    print('completed processing unread messages')
    except Exception as e:
        print('Unexpected error, failed to process unread messages:', e)
        raise

# -------- live handler --------
@client.on(events.NewMessage(chats=chats))
async def new_message_handler(event):
    msg = event.message
    is_match, full_message = await check_for_a_match(msg)
    if is_match:
        if not await check_for_duplicates(full_message):
            await message_forwarder(msg)

async def main():
    await unread_messages_retriever()
    print("Unread messages processed. Now listening for new messages...")

with client:
    client.loop.run_until_complete(main())
    client.run_until_disconnected()
