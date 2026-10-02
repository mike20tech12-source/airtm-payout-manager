import asyncio

from .db import init_db
from .bot import build_bot
from .api import create_server


async def main():
    init_db()

    bot = build_bot()
    server = create_server()

    await bot.initialize()
    await bot.start()
    await bot.updater.start_polling()

    loop = asyncio.get_running_loop()

    await loop.run_in_executor(None, server.serve_forever)


if __name__ == "__main__":
    asyncio.run(main())
