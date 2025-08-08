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

# keyowrds
chats = ['jobsalbasra', 'basrajobs', 'basrajobs3', 'jobbbs', 'basrajobs_ads', 'basrahvacancies',
        'vacancies_training', 'jobs_basra1', 'engineers_jobs_basra', 'basra_job', 'alisaedhsn',
        'Jobs_Advices', 'engahmad88', 'jobs_iraq1', 'Muhannad_job', 'Iraq_careers', 'YSPjobs',
        'jobs_for_us', 'iraq_jobs2', 'eng7600', 'iqjscout', 'Eng_job25', 'iraqjobs1995', 'iraqi_HR',
        'jobs_free'
]

level_keywords_en = [
    "intern", "internship", "trainee", "junior",
    "entry level", "graduate", "co-op"
]
level_keywords_ar = ['خريجين جدد', "تدريب"]

role_keywords_en = [
    "help desk", "it support", "it technician", "network support",
    "noc technician", "desktop support", "system admin",
    "systems analyst", "data center technician", "pc technician",
    "control systems engineer", "automation engineer",
    "embedded systems", "junior developer", "software intern",
    "cybersecurity technician", "ICSS", "technical support", "computer science", "control engineer",
    "instrumentation engineer", "instrumentation technician"
]
role_keywords_ar = ['دعم تقني', 'مهندس نظم', 'محلل نظم', 'تقني شبكات']

location_keywords_en = ['remote', 'basra']
location_keywords_ar = ['البصرة', 'عن بعد']

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

# Listen for new messages, look for matches and forward
@client.on(events.NewMessage(chats=chats))
async def new_message_handler(event):
    text = event.raw_text
    if level_pattern.search(text) and role_pattern.search(text) and location_pattern.search(text):
        await event.forward_to('BQTechJobs')

with client:
    client.run_until_disconnected()
