from telethon import TelegramClient, events

# Use your own values from my.telegram.org
api_id = 0
api_hash = '0'
client = TelegramClient('test', api_id, api_hash)
chats = ['jobsalbasra', 'basrajobs', 'basrajobs3', 'jobbbs', 'basrajobs_ads', 'basrahvacancies',
        'vacancies_training', 'jobs_basra1', 'engineers_jobs_basra', 'basra_job', 'alisaedhsn',
        'Jobs_Advices', 'engahmad88', 'jobs_iraq1', 'Muhannad_job', 'Iraq_careers', 'YSPjobs',
        'jobs_for_us', 'iraq_jobs2', 'eng7600', 'iqjscout', 'Eng_job25', 'iraqjobs1995', 'iraqi_HR',
        'jobs_free']
level_keywords = [
    "intern", "internship", "trainee", "junior",
    "entry level", "graduate", "co-op"
]
role_keywords = [
    "help desk", "it support", "it technician", "network support",
    "noc technician", "desktop support", "system admin",
    "systems analyst", "data center technician", "pc technician",
    "control systems engineer", "automation engineer",
    "embedded systems", "junior developer", "software intern",
    "cybersecurity technician"]
location_keywords = ['remote', 'basra', 'بصرة', 'عن بعد', 'ام قصر']

@client.on(events.NewMessage(chats))
async def new_message_handler(event):
    if 'hello' in event.raw_text:
        await event.reply('hi!')

async def main():

    # You can print all the dialogs/conversations that you are part of:
    async for dialog in client.iter_dialogs():
        print(dialog.name, 'has ID', dialog.id)

    # ...to some chat ID
    await client.send_message('placeholder', 'Hello, group!')

with client:
    client.loop.run_until_complete(main())
