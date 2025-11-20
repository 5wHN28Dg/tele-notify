from telethon import TelegramClient

api_id = YOUR_API_ID
api_hash = 'YOUR_API_HASH'

client = TelegramClient('session_name', api_id, api_hash)

async def main():
    async for dialog in client.iter_dialogs():
        print('{:>14}: {}'.format(dialog.id, dialog.title))

with client:
 client.loop.run_until_complete(main())
