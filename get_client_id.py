import os
import discord
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')

async def get_client_id():
    """Get and print the bot's client ID."""
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        print('Client ID:', client.user.id)
        await client.close()

    await client.start(DISCORD_TOKEN)

if __name__ == '__main__':
    import asyncio
    asyncio.run(get_client_id())
