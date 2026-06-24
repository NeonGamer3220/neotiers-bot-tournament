"""NeonTiers Tournament Discord Bot — entry point.

Wires up logging, intents, persistent views (rehydrated from DB on boot),
slash command sync, and a small set of admin/debug commands.
"""

from __future__ import annotations

import logging
import sys

import discord
from discord import app_commands
from discord.ext import commands

from config import config
from database import arun, db
from tournaments import TournamentCog
from views import QueueJoinView, TicketActionView

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("neontiers")

# Members intent is required for ticket channel permission management.
intents = discord.Intents.default()
intents.members = True
intents.message_content = False  # we only use slash commands + buttons
intents.guilds = True


class NeonTiersBot(commands.Bot):
    """Bot subclass with setup_hook-driven view rehydration + slash sync."""

    def __init__(self) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned_or("!"),
            intents=intents,
        )

    async def setup_hook(self) -> None:  # type: ignore[override]
        # Register the tournament cog (slash commands + auto-start loop).
        await self.add_cog(TournamentCog(self))

        # Rehydrate persistent views for any tournament that is still
        # queued or running, and any unresolved match tickets.
        try:
            active = await arun(db.list_active_tournaments)
            for tournament in active:
                tid = tournament["id"]
                self.add_view(QueueJoinView(tid))
                unresolved = await arun(db.get_unresolved_matches, tid)
                for match in unresolved:
                    self.add_view(TicketActionView(match_id=match["id"]))
            log.info(
                "rehydrated %d queue views + their unresolved ticket views",
                len(active),
            )
        except Exception as exc:
            log.error("view rehydration failed: %s", exc)

        # Instant guild slash sync (best-effort; on_ready will retry).
        await self._sync_guild_commands()

    async def _sync_guild_commands(self) -> bool:
        global _synced
        if not config.guild_id:
            log.warning("GUILD_ID not set; skipping slash sync.")
            return False
        guild = discord.Object(id=config.guild_id)
        try:
            self.tree.clear_commands(guild=guild)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            _synced = True
            log.info("synced %d guild commands to %s", len(synced), guild.id)
            return True
        except Exception as exc:
            log.warning("guild slash sync failed: %s", exc)
            return False


bot = NeonTiersBot()
_synced: bool = False


# ----------------------------------------------------------------------
# Admin slash commands
# ----------------------------------------------------------------------

@bot.tree.command(name="sync", description="Azonnali guild slash sync (admin).")
@app_commands.default_permissions(administrator=True)
async def sync_cmd(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    if interaction.guild is None:
        await interaction.followup.send("Ezt parancsot szerveren belül használd.", ephemeral=True)
        return
    try:
        bot.tree.copy_global_to(guild=interaction.guild)
        synced = await bot.tree.sync(guild=interaction.guild)
    except Exception as exc:
        await interaction.followup.send(f"Hiba: {exc}", ephemeral=True)
        return
    await interaction.followup.send(
        f"✅ Szinkronizálva: {len(synced)} parancs.", ephemeral=True
    )


@bot.tree.command(name="syncglobal", description="Globális slash sync (admin).")
@app_commands.default_permissions(administrator=True)
async def syncglobal_cmd(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        synced = await bot.tree.sync()
    except Exception as exc:
        await interaction.followup.send(f"Hiba: {exc}", ephemeral=True)
        return
    await interaction.followup.send(
        f"✅ Globálisan szinkronizálva: {len(synced)} parancs.", ephemeral=True
    )


@bot.tree.command(
    name="tournamentaddticket",
    description="DEBUG: Jegy hozzáadása két játékoshoz egy Tournamenthez.",
)
@app_commands.default_permissions(manage_guild=True)
@app_commands.describe(
    tournament_id="A Tournament UUID-ja.",
    player1="Első játékos.",
    player2="Második játékos.",
)
async def tournamentaddticket_cmd(
    interaction: discord.Interaction,
    tournament_id: str,
    player1: discord.Member,
    player2: discord.Member,
) -> None:
    tournament = await arun(db.get_tournament, tournament_id)
    if tournament is None:
        await interaction.response.send_message(
            f"Nem található Tournament: `{tournament_id}`", ephemeral=True
        )
        return

    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message(
            "Ezt a parancsot szöveges csatornában használd.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    guild = interaction.guild
    bot_member = guild.me if guild else None
    overwrites: dict = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
    }
    if bot_member is not None:
        overwrites[bot_member] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_messages=True,
        )
    for m in (player1, player2):
        overwrites[m] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
        )
    if config.regulator_role_id and guild is not None:
        reg_role = guild.get_role(config.regulator_role_id)
        if reg_role is not None:
            overwrites[reg_role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_messages=True,
                read_message_history=True,
            )

    acc1 = await arun(db.get_linked_account, player1.id)
    acc2 = await arun(db.get_linked_account, player2.id)
    mc1 = (acc1 or {}).get("minecraft_name", player1.display_name)
    mc2 = (acc2 or {}).get("minecraft_name", player2.display_name)

    name = tournament.get("name", "Tournament")
    try:
        match_row = await arun(
            db.create_match,
            tournament_id,
            int(tournament.get("current_round") or 0),
            player1.id,
            player2.id,
            mc1,
            mc2,
            channel.id,
        )
    except RuntimeError as exc:
        await interaction.followup.send(
            f"Hiba a match létrehozásakor: {exc}", ephemeral=True
        )
        return

    ticket_embed = discord.Embed(title=f"{name} Tournament", color=0x00E5FF)
    ticket_embed.description = (
        f"**Párosítás:**\n<@{player1.id}> ({mc1}) vs <@{player2.id}> ({mc2})\n\n"
        f"**In-game nevek:**\n`{mc1}` vs `{mc2}`\n\n"
        f"Regulator: az eredményt az **Eredmény beírása** gombbal rögzítsd."
    )
    ticket_view = TicketActionView(match_row["id"])
    await channel.send(
        content=f"<@{player1.id}> <@{player2.id}>",
        embed=ticket_embed,
        view=ticket_view,
    )
    await interaction.followup.send("✅ Ticket létrehozva.", ephemeral=True)


@bot.tree.command(
    name="tournamentfixpermissions",
    description="DEBUG: Jegy kategória jogosultságainak javítása.",
)
@app_commands.default_permissions(manage_guild=True)
async def tournamentfixpermissions_cmd(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "Szerveren belül használd.", ephemeral=True
        )
        return
    if not config.ticket_category_id:
        await interaction.response.send_message(
            "TICKET_CATEGORY_ID nincs beállítva.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    guild = interaction.guild
    category = guild.get_channel(config.ticket_category_id)
    if not isinstance(category, discord.CategoryChannel):
        await interaction.followup.send(
            "A megadott TICKET_CATEGORY_ID nem kategória.", ephemeral=True
        )
        return

    bot_member = guild.me
    successes = 0
    for ch in category.text_channels:
        overwrites = dict(ch.overwrites)
        overwrites[guild.default_role] = discord.PermissionOverwrite(view_channel=False)
        overwrites[bot_member] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_messages=True,
        )
        if config.regulator_role_id:
            reg_role = guild.get_role(config.regulator_role_id)
            if reg_role is not None:
                overwrites[reg_role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    manage_messages=True,
                    read_message_history=True,
                )
        try:
            await ch.edit(overwrites=overwrites)
            successes += 1
        except discord.HTTPException as exc:
            log.warning("fixperm: %s edit failed: %s", ch.name, exc)

    await interaction.followup.send(
        f"✅ {successes} csatorna jogosultságai javítva.", ephemeral=True
    )


# ----------------------------------------------------------------------
# on_ready — retry slash sync if setup_hook failed
# ----------------------------------------------------------------------

@bot.event
async def on_ready() -> None:
    global _synced
    log.info(
        "logged in as %s (id=%s) — %d guild(s)",
        bot.user,
        bot.user.id if bot.user else None,
        len(bot.guilds),
    )
    if bot.guilds:
        g = bot.guilds[0]
        log.info("active guild: %s (id=%s)", g.name, g.id)

    if not _synced and config.guild_id:
        await bot._sync_guild_commands()


def main() -> None:
    try:
        bot.run(config.discord_token)
    except KeyboardInterrupt:
        log.info("shutdown requested (KeyboardInterrupt)")
    except Exception as exc:
        log.exception("fatal error: %s", exc)
        raise


if __name__ == "__main__":
    main()
