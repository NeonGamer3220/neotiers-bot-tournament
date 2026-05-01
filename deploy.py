import os
import asyncio
from dotenv import load_dotenv

import discord

load_dotenv()

GUILD_ID = int(os.getenv('GUILD_ID'))
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')

# Import bot from index.py (with commands already defined)
from index import bot

async def deploy_commands():
    """Deploy slash commands to Discord."""
    GUILD_ID = int(os.getenv('GUILD_ID'))
    await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
    print('Successfully reloaded application (/) commands.')
    await bot.close()

if __name__ == '__main__':
    asyncio.run(deploy_commands())
