import asyncio

# import json
import aiohttp
from bs4 import BeautifulSoup


async def main():
    headers = {"user-agent": "tele-notify (+https://github.com/5wHN28Dg/tele-notify)"}
    async with aiohttp.ClientSession() as session:
        async with session.get(
            "https://submit.example.com/api/v2/accounts/awesomecompany-1/jobs/DAF36R174B",
            headers=headers,
        ) as response:
            # with open('IJS.html') as file:
            #   soup = BeautifulSoup(file, "lxml")

            soup_alpha = BeautifulSoup(await response.text(), "lxml")
            # data = json.loads(await response.text())
            # print(data["location"]["city"])

    print(soup_alpha.get_text())
    """
    print(
        soup_alpha.find(
            "div",
            class_="wrapper body",
        ).get_text()
    )
"""


asyncio.run(main())
