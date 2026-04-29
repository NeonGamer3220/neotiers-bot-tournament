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
    # Connect to gateway briefly to sync commands
    await bot.start(DISCORD_TOKEN)

if __name__ == '__main__':
    # Override on_ready to sync and exit
    original_on_ready = bot.on_ready

    async def on_ready():
        print(f'Logged in as {bot.user}')
        await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        print('Successfully reloaded application (/) commands.')
        await bot.close()

    bot.on_ready = on_ready
    asyncio.run(bot.start(DISCORD_TOKEN))
