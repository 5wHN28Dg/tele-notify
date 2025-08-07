from telethon import TelegramClient

# Use your own values from my.telegram.org
api_id = 98534905
api_hash = 'ff'
client = TelegramClient('test', api_id, api_hash)

async def main():
    # Getting information about yourself
    me = await client.get_me()

    # "me" is a user object. You can pretty-print
    # any Telegram object with the "stringify" method:
    print(me.stringify())

    # When you print something, you see a representation of it.
    # You can access all attributes of Telegram objects with
    # the dot operator. For example, to get the username:
    username = me.username
    print(username)
    print(me.phone)

    # You can print all the dialogs/conversations that you are part of:
    async for dialog in client.iter_dialogs():
        print(dialog.name, 'has ID', dialog.id)

    # You can send messages to yourself...
    await client.send_message(
        'me',
        'This message has **bold**, `code`, __italics__ and '
        'a [nice website](https://example.com)!',
        link_preview=False
    )
    # ...to some chat ID
    await client.send_message('BKQTechJobs', 'Hello, group!')
    # ...to your contacts
    await client.send_message('+9647804315610', 'test 123')

with client:
    client.loop.run_until_complete(main())
