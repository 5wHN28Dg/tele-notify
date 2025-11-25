from telethon import TelegramClient, events, errors
import logging
import re
from urlextract import URLExtract
import asyncio
import io
import json
import pytesseract
import tempfile
import os
import requests
from bs4 import BeautifulSoup
from PIL import Image
from tenacity import (
    stop_after_attempt,
    wait_random_exponential,
    retry_if_exception_type,
    retry,
)
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# logging for easier debugging
logging.basicConfig(
    format="[%(levelname)s %(asctime)s] %(name)s: %(message)s", level=logging.INFO
)

with open("config.json", "r") as f:
    data = json.load(f)

# Use your own values from my.telegram.org and KEEP THEM PRIVATE!
api_id = data["api_id"]
api_hash = data["api_hash"]
client = TelegramClient(
    "test",
    api_id,
    api_hash,
    request_retries=5,
    connection_retries=5,
    retry_delay=5,
    auto_reconnect=True,
)

# protecting recent messages from concurrent access
recent_lock = asyncio.Lock()

# chats to monitor
chats = data["chats"]
target_chat = data["target_chat"]
special_chat = data["special_chat"]

# keywords
level_keywords_en = data["level_keywords_en"]
level_keywords_ar = data["level_keywords_ar"]
entry_level_role_en = data["entry_level_role_en"]
entry_level_role_ar = data["entry_level_role_ar"]
mid_level_role_en = data["mid_level_general_role_en"]
mid_level_role_ar = data["mid_level_general_role_ar"]
location_keywords_en = data["location_keywords_en"]
location_keywords_ar = data["location_keywords_ar"]
certifications = [re.escape(x) for x in data["certifications"]]
responsibility_keywords = data["responsibility_keywords"]

# last 10 forwarded messages
recent_messages = data["recent_messages"]


# Function to build hybrid regex: \b for Latin, no \b for Arabic
def build_hybrid_regex(en_list, ar_list, special_patterns=None, mid_level=None):
    parts = []
    if en_list:
        processed_en_list = [
            r"[\s_-]+".join(re.escape(p) for p in k.split(" ")) for k in en_list
        ]
        en_pattern = (
            r"(?<![a-zA-Z0-9])(?:" + "|".join(processed_en_list) + r")(?![a-zA-Z0-9])"
        )
        parts.append(en_pattern)
    if ar_list:
        parts.append(r"(?:" + "|".join(re.escape(k) for k in ar_list) + r")")
        if special_patterns:
            parts.append(r"(?:" + "|".join(special_patterns) + r")")
    return (
        re.compile(
            r"(?<!senior[\s_-])" + "|".join(parts) if mid_level else "|".join(parts),
            re.IGNORECASE,
        )
        if parts
        else re.compile(r"$.")
    )


# Function to build experience regex pattern
def build_experience_regex():
    pattern = r"((?:experience:?\s?minimum\s\d+(?:(-\d+)?|\+)? years(?:’)?)|(?:minimum\s\d+(?:(-\d+)?|\+)? years(?:’)?\sin)|(\d+(?:(-\d+)?|\+)? years(?:’)? (?:of )?(?:relevant |proven |related |total )?(?:professional )?experience|experience required))|(?:خبرة(?: عملية)?(?:\sالمطلوبة:?\s?)? لا ?تقل عن|(?:يشترط|يجب) تواجد خبرة|خبر(?:ه|ة) (≥ )?(?:سنة|سنتين|(?:(?:ثلاث|اربع|خمس|ست|سبع|ثمان|تسع|عشر)\sسنوات)|[\d٠-٩]+)|(?:خبرة في مجال العمل|بالعمل (?:لا تقل (?:عن )?)?(?:سن(?:ه|ة)|سنتين|[\d٠-٩]+)))"
    return re.compile(pattern, re.IGNORECASE)


def build_responsibilities_regex(responsibility_keywords):
    keywords = "|".join(responsibility_keywords)
    pattern = f"(?:\\d+\\. |- |\\d+- |• |\n)({keywords})\\b"
    return re.compile(pattern, re.IGNORECASE)


def build_certifications_regex(certifications):
    cert_options = "(?:" + "|".join(certifications) + ")"
    patterns = [
        f"(must (have( one of the following: )?|possess|be|hold|obtain|demonstrate)|essential|require(s|d)|mandatory|meet).{{0,20}}({cert_options})(?! within|.{{0,20}}are a plus)",
        f"({cert_options}).{{0,20}}((is)? (required|mandatory)(?! or must be obtained|.{{0,20}}are a plus))",
        "(At least one of the following certifications required)|(Any of the following certifications accepted)",
        "Must possess one or more of the following",
        f"degree and {cert_options} or equivalent combination",
        f"The successful candidate will hold {cert_options}",
        f"Any relevant certifications .{{0,20}}{cert_options}?(?!.{{0,30}}are a plus)",
        f"(ينبغي ان يكون المرشح الناجح (حاصلا على|يمتلك) {cert_options})",
        f"(درجة (علمية)? و{cert_options} او ما يعادلها)",
        f"({cert_options}).{{0,20}}((مطلوب|إلزامي|ضروري)(?! أو يجب الحصول عليه))",
        "(على الأقل واحدة من الشهادات التالية مطلوبة)|(أي من الشهادات التالية مقبولة)",
        "(يجب أن يمتلك واحدة أو أكثر من الشهادات التالية)",
        f"حاصل على شهادة {cert_options}",
    ]

    combined_pattern = "|".join(patterns)
    return re.compile(combined_pattern, re.IGNORECASE)


apply_anyway_patterns = [
    r"(even\s+if\s+(?:you|they)(?:\s+)?(?:are|do|(?:'|’)re)(?:n'?t|\s+not)\s+(?:an\sexact\s)?(?:meet|match|fulfil|check)\s+(?:to\s)?(?:every|all)\s+(?:listed\s)?(?:requirement|criteria|qualification|statement|(?:bullet\s)?point))",
    r"((?:encourage|welcome|invite)\s+(?:you|applicants?|applications)\s+(?:(?:(?:(?:(?:to\s+)?(?:take\sthe\sleap\sand\s)?apply)|(?:with varied backgrounds and experiences)))|(?:.{0,44} who (?:may|do) not meet (?:every|all) listed requirement)))",
    r"(if\s+(?:you'?re|you\s+are)\s+(?:excited|interested|passionate|enthusiastic).{0,40}(?:apply|consider\s+applying))",
    r"((?:no candidate will|don(?:'|’)?t)\s+(?:tick|check|meet)\s+(?:every|all)(?:\ssingle)?\s+(?:box|requirement|qualification))",
]
combined_apply_anyway_pattern = "|".join(apply_anyway_patterns)


# Compile patterns
level_pattern = build_hybrid_regex(level_keywords_en, level_keywords_ar)
entry_level_role_pattern = build_hybrid_regex(entry_level_role_en, entry_level_role_ar)
mid_level_special_pattern = [
    r"(?<!senior )(?<!cost )(?<!estimation and )control engineer"
]
mid_level_role_pattern = build_hybrid_regex(
    mid_level_role_en, mid_level_role_ar, mid_level_special_pattern, mid_level=True
)
location_special_patterns = [r"(?<!شارع )الجمهورية"]
location_pattern = build_hybrid_regex(
    location_keywords_en, location_keywords_ar, location_special_patterns
)
experience_pattern = build_experience_regex()
certification_pattern = build_certifications_regex(certifications)
responsibilities_pattern = build_responsibilities_regex(responsibility_keywords)
is_job_seeker_pattern = re.compile(
    "(?:محتاج(?:ه|ة)?|(?:(?:(?:ا|أ)بحث|باحث) عن)|ادور(?: على)?) (?:فرصة )?(?:عمل|وظيفة|مهنة|شغل)",
    re.IGNORECASE,
)
is_tuition_pattern = re.compile(
    r"(?:ال)?قسط السنوي|(?:ال)?كادر (?:ال)?تدريسي", re.IGNORECASE
)
is_trivial_pattern = re.compile(r"تطوير مهارات (?:الحاسوب|الكمبيوتر)", re.IGNORECASE)
is_apply_anyway_pattern = re.compile(combined_apply_anyway_pattern)
extractor = URLExtract()

# retry policy in case something goes wrong
retry_transient = retry(
    reraise=True,
    stop=stop_after_attempt(5),  # give up after 5 tries
    wait=wait_random_exponential(
        multiplier=1, max=60
    ),  # exponential + full jitter, up to 60s
    retry=retry_if_exception_type(
        (asyncio.TimeoutError, OSError, errors.RpcCallFailError)
    ),
)


# -------- helpers --------
# check if the message passes the filters
async def check_for_a_match(message, chat_id):
    full_message = await message_processor(message)

    logging.info(f"checked message: {message.id}")
    entry_level = entry_level_role_pattern.search(full_message)
    mid_level = mid_level_role_pattern.search(full_message)
    if all(
        [
            (entry_level or mid_level),
            location_pattern.search(full_message),
            not is_job_seeker_pattern.search(full_message),
            not is_tuition_pattern.search(full_message),
        ]
    ):
        if level_pattern.search(full_message):  # this is stage 1
            return True, full_message, entry_level, mid_level

        if chat_id == special_chat:
            full_message = await scrape_full_job(full_message)

        if is_apply_anyway_pattern.search(full_message):
            experience, certification = False, False
        else:
            experience = experience_pattern.search(full_message)
            certification = certification_pattern.search(full_message)

        responsibilities = responsibilities_pattern.search(full_message)

        if entry_level and not any([experience, certification]):
            return True, full_message, entry_level, mid_level

        if mid_level and not any([experience, certification, responsibilities]):
            return True, full_message, entry_level, mid_level

        if not message.chat.noforwards:
            await message.forward_to("me")
        else:
            await client.send_message(
                "me",
                f"match: {entry_level.group() if entry_level else mid_level.group()}\npost link: https://t.me/{message.chat.username}/{message.id}",
            )
        logging.info("forwarded the message to you, check it out!")
    return False, full_message, entry_level, mid_level


# checks the message type then makes it ready for processing
async def message_processor(msg):
    image_text = ""
    if hasattr(msg, "photo") and msg.photo:
        try:
            image_text = await image_processor(msg)
        except Exception as e:
            logging.error(f"OCR failed: {e}")

    text_content = msg.raw_text or ""
    full_message = image_text + " " + text_content
    logging.info(f"Processed message: {msg.id}")
    return full_message


# downloads then extracts the image text
@retry_transient
async def image_processor(msg):
    image_bytes = await msg.download_media(file=bytes, progress_callback=callback)
    with Image.open(io.BytesIO(image_bytes)) as image:
        image_text = pytesseract.image_to_string(image, lang="ara+eng")
        logging.info(f"Extracted text from image in message: {msg.id}")
    return image_text


# Printing download progress
def callback(current, total):
    print(
        "Downloaded", current, "out of", total, "bytes: {:.2%}".format(current / total)
    )


@retry_transient
async def scrape_full_job(full_message):
    link_pattern = re.compile(r"https:\/\/\S+jobs\/\S+")
    link = link_pattern.search(full_message)
    headers = {"user-agent": "tele-notify (+https://github.com/5wHN28Dg/tele-notify)"}
    r = requests.get(link.group(), headers=headers)
    soup_alpha = BeautifulSoup(r.text, "lxml")
    job_description = soup_alpha.find("div", class_="wprt-container").get_text()
    full_description = job_description + full_message

    return full_description


# compares the message to be sent with the last 10 sent messages
async def check_for_duplicates(message_text):
    text = recent_messages + [message_text]
    vectorizer = TfidfVectorizer(stop_words=None, token_pattern=r"(?u)\b\w+\b")
    vectors = vectorizer.fit_transform(text)

    link_in_message = extractor.find_urls(message_text)
    # Compare new message with each recent message
    for i in range(len(recent_messages)):
        similarity = cosine_similarity(vectors[i : i + 1], vectors[-1:])[0, 0]
        if similarity > 0.8:
            return True  # Duplicate found

        link_in_recent = extractor.find_urls(recent_messages[i])

        if link_in_message and link_in_recent:
            if any(link in link_in_message for link in link_in_recent):
                return True  # Duplicate found
    return False


@retry_transient
async def message_forwarder(msg, full_message, entry_level, mid_level):
    if not msg.chat.noforwards:
        await msg.forward_to(target_chat)
    else:
        await client.send_message(
            target_chat,
            f"match: {entry_level.group() if entry_level else mid_level.group()}\npost link: https://t.me/{msg.chat.username}/{msg.id}",
        )
    await add_to_recent(full_message)
    await asyncio.sleep(1)


# update recent messages
async def add_to_recent(full_message):
    global recent_messages, data

    async with recent_lock:
        recent_messages.append(full_message)
        if len(recent_messages) > 10:
            recent_messages = recent_messages[-10:]

        data["recent_messages"] = recent_messages
        await asyncio.to_thread(write_config_atomic, data, "config.json")


def write_config_atomic(data, path):
    dir_name = os.path.dirname(path) or "."
    with tempfile.NamedTemporaryFile(
        "w", dir=dir_name, delete=False, encoding="utf-8"
    ) as tmp:
        json.dump(data, tmp, indent=4, ensure_ascii=False)
        tmp.flush()
        os.fsync(tmp.fileno())
        temp_name = tmp.name
    os.replace(temp_name, path)


# -------- unread bootstrap --------
@retry_transient
async def unread_messages_retriever():
    dialogs = await client.get_dialogs()
    for dialog in dialogs:
        try:
            if dialog.id in chats and dialog.unread_count > 0 and dialog.message:
                last_read_id = dialog.message.id - dialog.unread_count
                async for msg in client.iter_messages(dialog.id, min_id=last_read_id):
                    (
                        is_match,
                        full_message,
                        entry_level,
                        mid_level,
                    ) = await check_for_a_match(msg, dialog.id)
                    logging.info(
                        f"message {msg.id} from chat: {dialog.id} is {is_match}"
                    )
                    if is_match and not await check_for_duplicates(full_message):
                        await message_forwarder(
                            msg, full_message, entry_level, mid_level
                        )
                await client.send_read_acknowledge(dialog.id)
                logging.info(
                    f"completed processing unread messages for chat: {dialog.id} with name {dialog.title}"
                )
        except Exception:
            logging.exception(
                f"Failed to process unread messages for chat with id: {dialog.id}, title: {dialog.title}"
            )


# -------- live handler --------
@client.on(events.NewMessage(chats=chats))
@retry_transient
async def new_message_handler(event):
    msg = event.message
    is_match, full_message, entry_level, mid_level = await check_for_a_match(
        msg, msg.chat_id
    )
    logging.info(f"new message {msg.id} from chat: {msg.chat_id} is {is_match}")

    if is_match and not await check_for_duplicates(full_message):
        await message_forwarder(msg, full_message, entry_level, mid_level)
    await msg.mark_read()


async def main():
    await unread_messages_retriever()
    print("Unread messages processed, now listening for new messages...")


with client:
    client.loop.run_until_complete(main())
    client.run_until_disconnected()
