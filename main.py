from telethon import TelegramClient, events, errors
import logging
import re
import asyncio
from PIL import Image
import pytesseract
import io

# logging for easier debugging
logging.basicConfig(format='[%(levelname) %(asctime)s] %(name)s: %(message)s',
                    level=logging.INFO)

# Use your own values from my.telegram.org and KEEP THEM PRIVATE!
api_id = 0
api_hash = '0'
client = TelegramClient('test', api_id, api_hash)

# chats to monitor
chats = ['jobsalbasra', 'basrajobs', 'basrajobs3', 'jobbbs', 'basrajobs_ads', 'basrahvacancies',
        'vacancies_training', 'jobs_basra1', 'engineers_jobs_basra', 'basra_job', 'alisaedhsn',
        'Jobs_Advices', 'engahmad88', 'jobs_iraq1', 'Muhannad_job', 'Iraq_careers', 'YSPjobs',
        'jobs_for_us', 'iraq_jobs2', 'eng7600', 'iqjscout', 'Eng_job25', 'iraqjobs1995', 'iraqi_HR',
        'jobs_free'
]

# keywords
level_keywords_en = [
    "intern", "internship", "trainee", "junior", "graduate",
    "entry level", "fresh graduate", "co-op",
    "summer training", "training program", "apprentice", "apprenticeship",
    "volunteer", "probation", "on-the-job training"
]

level_keywords_ar = [
    "متدرب", "تدريب", "تدريب صيفي", "تدريب عملي",
    "خريج جديد", "خريجون جدد", "حديث التخرج", "خريجة جديدة",
    "متدربة", "متدربين", "برنامج تدريبي", "متدرب هندسة", "فرصة تدريب",
    "فترة تجربة", "متطوع", "وظيفة مؤقتة"
]

role_keywords_en = [
    # IT support roles
    "help desk", "it support", "technical support", "support technician",
    "desktop support", "pc technician", "it technician",
    "network support", "network technician", "noc technician",
    "system admin", "systems administrator", "systems analyst",
    "data center technician",

    # Software & dev
    "junior developer", "junior programmer", "software",
    "software engineer", "web developer",
    "mobile app developer", "python developer",

    # Cybersecurity
    "cybersecurity", "security analyst", "security technician",

    # Embedded / control / automation
    "control systems engineer", "automation engineer",
    "embedded systems engineer", "embedded systems developer",
    "control engineer", "ics engineer", "icss engineer",
    "instrumentation engineer", "instrumentation technician",
    "plc programmer", "scada engineer", "dcs engineer",
    "industrial automation", "process control engineer",

    # Academic background
    "computer science", "control engineering", "computer engineering",
    "electrical and computer engineering", "electrical engineering",
    "control systems engineering", "automation engineering"
]

role_keywords_ar = [
    # IT support
    "دعم تقني", "دعم فني", "فني حاسوب", "فني دعم", "فني شبكات", "فني كمبيوتر",
    "فني صيانة حاسوب", "مشغل كمبيوتر", "فني تقنية معلومات", "فني برمجيات",
    "مسؤول نظم", "مسؤول انظمة", "محلل نظم",

    # Software
    "مبرمج مبتدئ", "مبرمج جافا", "مبرمج بايثون", "مبرمج مواقع", "مبرمج تطبيقات",
    "مبرمج أندرويد", "مبرمج كيوت", "مبرمج نظم", "مبرمج قواعد بيانات",

    # Cybersecurity
    "أمن سيبراني", "مختبر اختراق", "محلل أمن", "محلل أمني",

    # Control / automation / engineering
    "مهندس نظم تحكم", "مهندس تحكم", "مهندس اوتوميشن", "مهندس أتمتة",
    "مهندس أجهزة دقيقة", "فني أجهزة دقيقة", "مهندس plc", "مبرمج plc",
    "مهندس scada", "مهندس dcs", "تحكم صناعي", "أتمتة صناعية", "تحكم العمليات",
    "مهندس أنظمة صناعية", "مهندس الكترونيات صناعية", "مهندس كهرباء وتحكم",
    "مهندس ميكاترونكس", "مهندس نظم صناعية"
]


location_keywords_en = [
    # General
    "basra", "basrah", "al basra", "al-basra",

    # Remote
    "remote", "work from home", "wfh", "from home",

    # Major cities/districts
    "um qasr", "um al qasr", "umm qasr", "umm al qasr",
    "zubeir", "zubair", "az zubayr", "al zubair", "az-zubayr",
    "shatt al arab", "shatt-al-arab",
    "qurna", "al qurna", "al-qurnah", "qurnah",

    # Towns / subdistricts
    "abu al khasib", "abu al-khasib", "abu al khasib",
    "mdaina", "al medina", "al madinah", "medinah", "madina",
    "faw", "al faw", "al-fao", "fao",

    # Ports / oil fields / industrial zones
    "north rumaila", "south rumaila", "rumaila",
    "majnoon oil field", "majnoon", "west qurna", "west qurna 1", "west qurna 2",
    "nahr umar", "luhais", "rafidain oil field",
    "khor al zubair", "khor az zubair", "khor al-zubair",
    "basra refinery", "shuaiba industrial zone", "shuaiba", "shuaibah",
    "al baradiyah", "tanuma", "jubaish", "gharma", "karma ali", "karma-ali"
]

location_keywords_ar = [
    # General
    "البصرة", "بصرة",

    # Remote
    "عن بعد", "من المنزل", "العمل من المنزل",

    # Major cities/districts
    "أم قصر", "ام قصر", "الزبير", "الزُبير",
    "شط العرب", "القرنة", "القرنه",

    # Towns / subdistricts
    "أبو الخصيب", "ابو الخصيب", "المدينة", "المدينه", "الفاو",

    # Ports / oil fields / industrial zones
    "حقل الرميلة", "الرميلة", "رميلة", "حقل مجنون", "مجنون",
    "غرب القرنة", "غرب القرنه", "نهر عمر", "لحيس", "اللحيس", "حقل الرافدين",
    "خور الزبير", "الزبير الصناعي", "مصفى البصرة", "الشناشيل",
    "الشعيبة", "الشعيبه", "البراضعية", "التنومة", "كَرمة علي", "كرمة علي"
]

# Function to build hybrid regex: \b for Latin, no \b for Arabic
def build_hybrid_regex(en_list, ar_list):
    parts = []
    if en_list:
        parts.append(r'\b(?:' + '|'.join(re.escape(k) for k in en_list) + r')\b')
    if ar_list:
        parts.append(r'(?:' + '|'.join(re.escape(k) for k in ar_list) + r')')
    return re.compile('|'.join(parts), re.IGNORECASE)

# Compile patterns
level_pattern = build_hybrid_regex(level_keywords_en, level_keywords_ar)
role_pattern = build_hybrid_regex(role_keywords_en, role_keywords_ar)
location_pattern = build_hybrid_regex(location_keywords_en, location_keywords_ar)

# check unread messages 1st if there is any
async def process_unread_messages():
    try:
        dialogs = await client.get_dialogs()
        for dialog in dialogs:
            if dialog.entity.username in chats:
                if dialog.unread_count > 0:
                    last_read_id = dialog.message.id - dialog.unread_count
                    async for message in client.iter_messages(dialog.id, min_id=last_read_id):
                        if message.text is not None:
                            if level_pattern.search(message.text) and \
                                role_pattern.search(message.text) and \
                                location_pattern.search(message.text):
                                    try:
                                        await message.forward_to('BQTechJobs')
                                        await asyncio.sleep(1)
                                    except errors.FloodWaitError as e:
                                        # e.seconds is how many seconds you have
                                        # to wait before making the request again.
                                        print('Flood for', e.seconds)
                                        await asyncio.sleep(e.seconds)
                                        await message.forward_to('BQTechJobs')
                        elif message.text is None and message.is_image:
                            try:
                                image_bytes = await message.download_media(file=bytes)
                                image = Image.open(io.BytesIO(image_bytes))
                                image_text = pytesseract.image_to_string(image, lang='ara+eng')
                                if level_pattern.search(image_text) and \
                                    role_pattern.search(image_text) and \
                                    location_pattern.search(image_text):
                                    try:
                                        await message.forward_to('BQTechJobs')
                                        await asyncio.sleep(1)
                                    except errors.FloodWaitError as e:
                                        print('Flood for', e.seconds)
                                        await asyncio.sleep(e.seconds)
                                        await message.forward_to('BQTechJobs')
                            except Exception as e:
                                logging.error(f'Error processing image: {e}')
                                # Continue processing other messages instead of crashing
                        elif message.text is not None and message.is_image:
                            try:
                                image_bytes = await message.download_media(file=bytes)
                                image = Image.open(io.BytesIO(image_bytes))
                                image_text = pytesseract.image_to_string(image, lang='ara+eng')
                                whole_text = image_text + '\n' + message.text
                                if level_pattern.search(whole_text) and \
                                    role_pattern.search(whole_text) and \
                                    location_pattern.search(whole_text):
                                    try:
                                        await message.forward_to('BQTechJobs')
                                        await asyncio.sleep(1)
                                    except errors.FloodWaitError as e:
                                        print('Flood for', e.seconds)
                                        await asyncio.sleep(e.seconds)
                                        await message.forward_to('BQTechJobs')
                            except Exception as e:
                                logging.error(f'Error processing message: {e}')
                                # Continue processing other messages instead of crashing
    except Exception as e:
        print('Unexpected error:', e)
        raise

# Listen for new messages, look for matches and forward
@client.on(events.NewMessage(chats=chats))
async def new_message_handler(event):
    if event.raw_text is not None:
        text = event.raw_text
        if level_pattern.search(text) and \
            role_pattern.search(text) and \
            location_pattern.search(text):
            try:
                await event.forward_to('BQTechJobs')
                await asyncio.sleep(1)
            except errors.FloodWaitError as e:
                print('Flood for', e.seconds)
                await asyncio.sleep(e.seconds)
                await event.forward_to('BQTechJobs')
    elif event.raw_text is None and event.is_image:
        try:
            image_bytes = await event.download_media(file=bytes)
            image = Image.open(io.BytesIO(image_bytes))
            image_text = pytesseract.image_to_string(image, lang='ara+eng')
            if level_pattern.search(image_text) and \
                role_pattern.search(image_text) and \
                location_pattern.search(image_text):
                try:
                    await event.forward_to('BQTechJobs')
                    await asyncio.sleep(1)
                except errors.FloodWaitError as e:
                    print('Flood for', e.seconds)
                    await asyncio.sleep(e.seconds)
                    await event.forward_to('BQTechJobs')
        except Exception as e:
            logging.error(f'Error processing image: {e}')
            # Continue processing other messages instead of crashing
    elif event.text is not None and event.is_image:
        try:
            image_bytes = await event.download_media(file=bytes)
            image = Image.open(io.BytesIO(image_bytes))
            image_text = pytesseract.image_to_string(image, lang='ara+eng')
            whole_text = image_text + '\n' + event.text
            if level_pattern.search(whole_text) and \
                role_pattern.search(whole_text) and \
                location_pattern.search(whole_text):
                try:
                    await event.forward_to('BQTechJobs')
                    await asyncio.sleep(1)
                except errors.FloodWaitError as e:
                    print('Flood for', e.seconds)
                    await asyncio.sleep(e.seconds)
                    await event.forward_to('BQTechJobs')
        except Exception as e:
            logging.error(f'Error processing message: {e}')
            # Continue processing other messages instead of crashing


async def main():
    await process_unread_messages()
    print("Unread messages processed. Now listening for new messages...")

with client:
    client.loop.run_until_complete(main())
    client.run_until_disconnected()
