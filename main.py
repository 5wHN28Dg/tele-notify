from telethon import TelegramClient, events, errors
import logging, re, asyncio, io, json
from PIL import Image
import pytesseract
from tenacity import retry, stop_after_attempt, wait_random_exponential, retry_if_exception_type, asyncretry
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# logging for easier debugging
logging.basicConfig(format='[%(levelname)s %(asctime)s] %(name)s: %(message)s',
                    level=logging.INFO)

with open('config.json', 'r') as f:
    data = json.load(f)

# Use your own values from my.telegram.org and KEEP THEM PRIVATE!
api_id = data['api_id']
api_hash = data['api_hash']
client = TelegramClient('test', api_id, api_hash,
                        request_retries=5, connection_retries=5, retry_delay=5, auto_reconnect=True)

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

# last 10 forwarded messages
recent_messages = []

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

retry_transient = retry(
    reraise=True,
    stop=stop_after_attempt(5),  # give up after 5 tries
    wait=wait_random_exponential(multiplier=1, max=60),  # exponential + full jitter, up to 60s
    retry=retry_if_exception_type((asyncio.TimeoutError, OSError, errors.RpcCallFailError))
)

# -------- helpers --------
# check if the message passes the filters
async def check_for_a_match(message):
    full_message = await message_processor(message)

    logging.info(f"checked message: {message.id}")
    return all([
            level_pattern.search(full_message),
            role_pattern.search(full_message),
            location_pattern.search(full_message)
        ]), full_message

# ckecks the message type then makes it ready for processing
async def message_processor(msg):
    image_text = ""
    if hasattr(msg, 'photo') and msg.photo:
        try:
            image_text = await image_processor(msg)
        except Exception as e:
            logging.error(f"OCR failed: {e}")

    text_content = msg.raw_text or ""
    full_message = image_text + " " + text_content
    logging.info(f"Processed message: {msg.id}")
    return full_message

# downloads then extracts the image text
async def image_processor(msg):
    image_bytes = await msg.download_media(file=bytes, progress_callback=callback)
    with Image.open(io.BytesIO(image_bytes)) as image:
        image_text = pytesseract.image_to_string(image, lang='ara+eng')
        logging.info(f"Extracted text from image in message: {msg.id}")
    return image_text

# Printing download progress
def callback(current, total):
    print('Downloaded', current, 'out of', total,
          'bytes: {:.2%}'.format(current / total))

# compares the message to be sent with the last 10 sent messages
async def check_for_duplicates(message_text):
    try:
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
        logging.error(f"Error checking for duplicates: {e}")
        return False

@retry_transient
async def message_forwarder(msg, full_message):
    await msg.forward_to(target_chat)
    await add_to_recent(full_message)
    await asyncio.sleep(1)

async def add_to_recent(full_message):
    if len(recent_messages) >= 10:
        recent_messages.pop(0)
        recent_messages.append(full_message)
    else:
        recent_messages.append(full_message)

# -------- unread bootstrap --------
async def unread_messages_retriever():
    try:
        dialogs = await client.get_dialogs()
        for dialog in dialogs:
            try:
                if dialog.id in chats and dialog.unread_count > 0 and dialog.message:
                    last_read_id = dialog.message.id - dialog.unread_count
                    async for msg in client.iter_messages(dialog.id, min_id=last_read_id):
                        is_match, full_message = await check_for_a_match(msg)
                        logging.info(f'message {msg.id} from chat: {dialog.id} is {is_match}')
                        if is_match and not await check_for_duplicates(full_message):
                            await message_forwarder(msg, full_message)
                    await client.send_read_acknowledge(dialog.id)
                    logging.info(f'completed processing unread messages for chat: {dialog.id} with name {dialog.title}')
            except:
                logging.exception(f'Failed to process unread messages for chat with id: {dialog.id}, title: {dialog.title}')

    except Exception as e:
        logging.exception(f'Failed to process unread messages: {e}')
        raise

# -------- live handler --------
@client.on(events.NewMessage(chats=chats))
async def new_message_handler(event):
    msg = event.message
    is_match, full_message = await check_for_a_match(msg)
    logging.info(f'new message {msg.id} from chat: {msg.chat_id} is {is_match}')

    if is_match and not await check_for_duplicates(full_message):
        await message_forwarder(msg, full_message)
    await msg.mark_read()

async def main():
    await unread_messages_retriever()
    print("Unread messages processed, now listening for new messages...")

with client:
    client.loop.run_until_complete(main())
    client.run_until_disconnected()
