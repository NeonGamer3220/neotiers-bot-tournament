"""NeonTiers Tournament Discord Bot - Fő belépési pont."""

from __future__ import annotations

import logging
import sys
import discord
from discord.ext import commands

from config import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("neontiers")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def setup_hook():
    """Modulok és parancsok szinkronizálása indításkor."""
    log.info("Modulok betöltése...")
    await bot.load_extension("cogs.tournaments")

    # Kényszerített szinkronizálás az instant elérhetőségért
    if config.guild_id > 0:
        guild = discord.Object(id=config.guild_id)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        log.info("✅ Slash parancsok szinkronizálva a szerverre (%d parancs).", len(synced))
    else:
        synced = await bot.tree.sync()
        log.info("✅ Globális slash parancsok szinkronizálva (%d parancs).", len(synced))


@bot.event
async def on_ready():
    log.info("Sikeres bejelentkezés: %s (ID: %s)", bot.user, bot.user.id)


def main():
    bot.run(config.discord_token)


if __name__ == "__main__":
    main()
