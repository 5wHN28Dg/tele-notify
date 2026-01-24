from telethon import TelegramClient

api_id = your_api_id_here
api_hash = "your_api_hash_here"
client = TelegramClient(
    "test",
    api_id,
    api_hash,
    request_retries=5,
    connection_retries=5,
    retry_delay=5,
    auto_reconnect=True,
)


async def main():
    dialogs = await client.get_dialogs()
    for dialog in dialogs:
        if dialog.id == :
            async for message in client.iter_messages(dialog.id, limit=1):
                print(message)


with client:
    client.loop.run_until_complete(main())
