"""NeonTiers Tournament Discord bot — entry point.

Run on Railway with:
    python main.py

The bot loads the TournamentCog, registers persistent views so buttons keep
working across restarts, and exposes /sync, /syncglobal, /tournamentaddticket
and /tournamentfixpermissions commands.
"""
from __future__ import annotations

import asyncio
import logging
import sys

import discord
from discord import app_commands
from discord.ext import commands

from config import config
from database import db
from tournaments import TournamentCog
from views import QueueJoinView, TicketActionView

# ---------------------------------------------------------------------------- #
# Logging
# ---------------------------------------------------------------------------- #
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("neontiers")

# ---------------------------------------------------------------------------- #
# Intents
# ---------------------------------------------------------------------------- #
intents = discord.Intents.default()
intents.members = True          # required to add players to private ticket channels
intents.message_content = False  # we only use slash commands and buttons
intents.guilds = True


class NeonTiersBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned_or("!"),
            intents=intents,
        )

    async def setup_hook(self) -> None:
        # Register cogs
        await self.add_cog(TournamentCog(self))

        # Re-register persistent views so existing buttons stay functional
        # after a restart. We pull all current tournaments and matches from DB.
        try:
            for tournament in db.list_all_tournaments():
                self.add_view(QueueJoinView(tournament["id"]))
            # For ticket views we need match ids — fetch across recent tournaments
            for tournament in db.list_all_tournaments():
                for match in db.get_matches(tournament["id"]):
                    self.add_view(TicketActionView(match_id=match["id"]))
        except Exception as exc:
            log.warning("Could not rehydrate persistent views: %s", exc)

        # Instant slash-command sync to the configured guild.
        # Done in setup_hook (which runs exactly once, BEFORE the gateway
        # connection opens) so commands are live the moment the bot appears
        # online — no need to run /sync manually on first boot.
        if config.guild_id:
            guild_obj = discord.Object(id=config.guild_id)
            try:
                # Clear existing guild commands first to avoid stale entries.
                self.tree.clear_commands(guild=guild_obj)
                self.tree.copy_global_to(guild=guild_obj)
                synced = await self.tree.sync(guild=guild_obj)
                log.info(
                    "Instant-synced %d commands to guild %s",
                    len(synced), config.guild_id,
                )
            except Exception as exc:
                log.warning("Instant guild sync failed: %s", exc)
        else:
            log.info("GUILD_ID not set — skipping auto-sync. Run /sync manually.")

        log.info("Setup hook complete.")


bot = NeonTiersBot()


# ---------------------------------------------------------------------------- #
# Admin: /sync (guild) and /syncglobal
# ---------------------------------------------------------------------------- #
@bot.tree.command(name="sync", description="Szinkronizálja a parancsokat erre a guildra (admin).")
@app_commands.default_permissions(administrator=True)
async def sync_cmd(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        await interaction.response.send_message("Ezt csak szerverben lehet használni.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    bot.tree.copy_global_to(guild=interaction.guild)
    synced = await bot.tree.sync(guild=interaction.guild)
    await interaction.followup.send(
        f"✅ Szinkronizálva: {len(synced)} parancs ezen a guildon.", ephemeral=True
    )


@bot.tree.command(name="syncglobal", description="Globális parancsszinkron (admin).")
@app_commands.default_permissions(administrator=True)
async def sync_global_cmd(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    synced = await bot.tree.sync()
    await interaction.followup.send(
        f"✅ Globálisan szinkronizálva: {len(synced)} parancs.", ephemeral=True
    )


# ---------------------------------------------------------------------------- #
# Debug: /tournamentaddticket
# ---------------------------------------------------------------------------- #
@bot.tree.command(
    name="tournamentaddticket",
    description="DEBUG: két játékos hozzáadása egy meglévő ticket csatornához.",
)
@app_commands.default_permissions(manage_guild=True)
async def tournament_add_ticket(
    interaction: discord.Interaction,
    tournament_id: str,
    player1: discord.Member,
    player2: discord.Member,
) -> None:
    tournament = db.get_tournament(tournament_id)
    if not tournament:
        await interaction.response.send_message(
            f"❌ Nincs ilyen turné: `{tournament_id}`.", ephemeral=True
        )
        return
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message(
            "Ezt egy ticket csatornában használd.", ephemeral=True
        )
        return

    for member in (player1, player2):
        try:
            await interaction.channel.set_permissions(
                member,
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            )
        except discord.HTTPException as exc:
            await interaction.response.send_message(
                f"Hiba {member.mention} hozzáadásakor: {exc}", ephemeral=True
            )
            return

    linked1 = db.get_linked_account(player1.id) or {"minecraft_name": "ismeretlen"}
    linked2 = db.get_linked_account(player2.id) or {"minecraft_name": "ismeretlen"}
    db.create_match(
        tournament_id=tournament_id,
        round_number=int(tournament.get("current_round") or 1),
        player1_discord_id=player1.id,
        player2_discord_id=player2.id,
        player1_mc=linked1.get("minecraft_name", "ismeretlen"),
        player2_mc=linked2.get("minecraft_name", "ismeretlen"),
        ticket_channel_id=interaction.channel.id,
    )

    embed = discord.Embed(
        title=f"{tournament['name']} Tournament",
        description=(
            f"**Párosítás:** {player1.mention} vs {player2.mention}\n"
            f"**In-game nevek:** {linked1.get('minecraft_name','?')} vs {linked2.get('minecraft_name','?')}\n\n"
            f"Regulator: <@&{config.regulator_role_id}> szerepkörű tagok használhatják a gombokat."
        ),
        color=0x00E5FF,
    )
    await interaction.channel.send(
        content=f"{player1.mention} {player2.mention}",
        embed=embed,
    )
    await interaction.response.send_message("✅ Ticket létrehozva.", ephemeral=True)


# ---------------------------------------------------------------------------- #
# Debug: /tournamentfixpermissions
# ---------------------------------------------------------------------------- #
@bot.tree.command(
    name="tournamentfixpermissions",
    description="DEBUG: jogosultságok javítása az összes ticket csatornában.",
)
@app_commands.default_permissions(manage_guild=True)
async def tournament_fix_permissions(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        await interaction.response.send_message("Csak szerverben használható.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)

    category = interaction.guild.get_channel(config.ticket_category_id)
    if not isinstance(category, discord.CategoryChannel):
        await interaction.followup.send(
            f"TICKET_CATEGORY_ID ({config.ticket_category_id}) nem egy kategória.",
            ephemeral=True,
        )
        return

    fixed = 0
    for channel in category.text_channels:
        try:
            await channel.set_permissions(
                interaction.guild.default_role, view_channel=False
            )
            await channel.set_permissions(
                interaction.guild.me,
                view_channel=True,
                send_messages=True,
                manage_channels=True,
            )
            role = interaction.guild.get_role(config.regulator_role_id)
            if role is not None:
                await channel.set_permissions(
                    role,
                    view_channel=True,
                    send_messages=True,
                    manage_messages=True,
                    read_message_history=True,
                )
            fixed += 1
        except discord.HTTPException as exc:
            log.warning("Could not fix perms for %s: %s", channel.name, exc)

    await interaction.followup.send(
        f"✅ {fixed} csatorna jogosultságai javítva.", ephemeral=True
    )


# ---------------------------------------------------------------------------- #
# Boot
# ---------------------------------------------------------------------------- #
@bot.event
async def on_ready() -> None:
    log.info("Logged in as %s (id=%s)", bot.user, bot.user.id if bot.user else "?")
    if config.guild_id:
        guild = bot.get_guild(config.guild_id)
        if guild:
            log.info("Active guild: %s (%s)", guild.name, guild.id)
    # Slash commands are already instant-synced in setup_hook (runs once,
    # before the gateway opens). No need to re-sync here on every reconnect.


def main() -> None:
    try:
        bot.run(config.discord_token)
    except KeyboardInterrupt:
        log.info("Shutdown requested.")
    except Exception as exc:
        log.exception("Fatal error: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
