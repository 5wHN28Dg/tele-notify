from telethon import TelegramClient, events
import logging
import re

# logging for easier debugging
logging.basicConfig(format='[%(levelname) %(asctime)s] %(name)s: %(message)s',
                    level=logging.WARNING)

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
    "junior developer", "junior programmer", "software intern",
    "software engineer intern", "web developer intern",
    "mobile app developer intern", "python developer intern",

    # Cybersecurity
    "cybersecurity intern", "security analyst intern", "security technician",

    # Embedded / control / automation
    "control systems engineer", "automation engineer",
    "embedded systems engineer", "embedded systems developer",
    "control engineer", "ics engineer", "icss engineer",
    "instrumentation engineer", "instrumentation technician",
    "plc programmer", "scada engineer", "dcs engineer",
    "industrial automation", "process control engineer",

    # Academic background
    "computer science", "control engineering", "computer engineering",
    "electrical and computer engineering"
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
    dialogs = await client.get_dialogs()
    for dialog in dialogs:
        if dialog.entity.Username in chats:
            if dialog.unread_count > 0:
                last_read_id = dialog.message.id - dialog.unread_count
                async for message in client.iter_messages(dialog.id, min_id=last_read_id):
                    if level_pattern.search(message.text) and role_pattern.search(message.text) and location_pattern.search(message.text):
                        await message.forward_to('BQTechJobs')

# Listen for new messages, look for matches and forward
@client.on(events.NewMessage(chats=chats))
async def new_message_handler(event):
    text = event.raw_text
    if level_pattern.search(text) and role_pattern.search(text) and location_pattern.search(text):
        await event.forward_to('BQTechJobs')


async def main():
    await process_unread_messages()
    print("Unread messages processed. Now listening for new messages...")

with client:
    client.loop.run_until_complete(main())
    client.run_until_disconnected()
