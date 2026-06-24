"""Tournament lifecycle: queue command, auto-start loop, round management, ticket creation."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import config
from database import db, arun
from utils import (
    format_discord_timestamp,
    format_match_line,
    pair_players,
    parse_timestamp,
    safe_channel_name,
    utcnow,
)
from views import QueueJoinView, TicketActionView, build_queue_embed

log = logging.getLogger("neontiers.tournament")


async def find_queue_message(
    guild: discord.Guild, queue_message_id: int
) -> Optional[discord.Message]:
    """Locate the queue message by ID across all text channels of the guild.

    The tournaments table does not store queue_channel_id, so we iterate the
    guild's text channels and try `fetch_message` on each. This is O(channels)
    per round start — acceptable for typical Discord servers.
    """
    for channel in guild.text_channels:
        try:
            msg = await channel.fetch_message(queue_message_id)
            if msg is not None:
                return msg
        except discord.NotFound:
            continue
        except discord.Forbidden:
            continue
        except discord.HTTPException as exc:
            log.warning(
                "fetch_message failed in #%s: %s", channel.name, exc
            )
            continue
    return None


class TournamentCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.auto_start_loop.start()

    def cog_unload(self) -> None:  # noqa: D401
        self.auto_start_loop.cancel()

    # ------------------------------------------------------------------ #
    # /tournamentqueue
    # ------------------------------------------------------------------ #
    @app_commands.command(
        name="tournamentqueue",
        description="Létrehoz egy Tournament queue-t csatlakozási/kilépési gombokkal.",
    )
    @app_commands.describe(
        name="A Tournament neve (pl. teszt)",
        timestamp="Queue lezárás / kör indulás ideje — ISO 8601 (pl. 2026-06-25T18:00:00+02:00)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def tournament_queue(
        self,
        interaction: discord.Interaction,
        name: str,
        timestamp: str,
    ) -> None:
        try:
            end_time = parse_timestamp(timestamp)
        except ValueError as exc:
            await interaction.response.send_message(
                f"❌ Érvénytelen timestamp: {exc}", ephemeral=True
            )
            return

        if end_time < utcnow():
            await interaction.response.send_message(
                "❌ A megadott időpont a múltban van.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        if interaction.channel is None or not isinstance(interaction.channel, discord.TextChannel):
            await interaction.followup.send(
                "Ezt a parancsot egy szöveges csatornában használd.", ephemeral=True
            )
            return
        if interaction.guild is None:
            await interaction.followup.send(
                "Ezt a parancsot egy szerverben használd.", ephemeral=True
            )
            return

        # Pre-generate the tournament UUID so we can build the persistent
        # Join/Leave view BEFORE inserting the row.
        tournament_id = str(uuid.uuid4())

        # 1) Insert the tournament row FIRST with queue_message_id=0.
        #    This eliminates the race condition where a user could click the
        #    Join button in the milliseconds between the message appearing
        #    and the DB insert completing (which would cause "Tournament
        #    not available").
        try:
            await arun(
                db.create_tournament,
                name=name,
                end_time=end_time,
                queue_message_id=0,  # placeholder, updated below
                guild_id=interaction.guild.id,
                tournament_id=tournament_id,
            )
        except Exception as exc:
            await interaction.followup.send(
                f"❌ Nem sikerült létrehozni a Tournament sort az adatbázisban.\n"
                f"```\n{exc}\n```",
                ephemeral=True,
            )
            return

        # 2) Build the embed + persistent view, then send the message.
        embed = discord.Embed(
            title=f"{name} Tournament",
            description=(
                f"**Indulás:** {format_discord_timestamp(end_time)}"
            ),
            color=0x00E5FF,
        )
        embed.add_field(name="Játékosok", value="0", inline=True)
        embed.set_footer(text="Kattints a Belépés a tournamentbe gombra a jelentkezéshez.")

        view = QueueJoinView(tournament_id)

        try:
            sent = await interaction.channel.send(embed=embed, view=view)
        except discord.HTTPException as exc:
            # Roll back the DB row if the message send failed.
            try:
                await arun(db.delete_tournament, tournament_id)
            except Exception:
                pass
            await interaction.followup.send(
                f"❌ Nem sikerült elküldeni a queue üzenetet: `{exc}`",
                ephemeral=True,
            )
            return

        # 3) Update the row with the real queue_message_id.
        try:
            await arun(
                db.update_tournament,
                tournament_id,
                queue_message_id=sent.id,
            )
        except Exception as exc:
            log.warning(
                "Tournament %s row created but queue_message_id update failed: %s",
                tournament_id, exc,
            )

        log.info(
            "Created tournament %s (name=%r, queue_message_id=%s, end_time=%s)",
            tournament_id, name, sent.id, end_time.isoformat(),
        )

        await interaction.followup.send(
            f"✅ Tournament létrehozva: **{name}** (ID `{tournament_id}`). "
            f"Indulás {format_discord_timestamp(end_time)}.",
            ephemeral=True,
        )

    # ------------------------------------------------------------------ #
    # /tournamentround
    # ------------------------------------------------------------------ #
    @app_commands.command(
        name="tournamentround",
        description="Kör indítása vagy leállítása egy meglévő Tournament-re.",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="start", value="start"),
            app_commands.Choice(name="stop", value="stop"),
        ]
    )
    @app_commands.default_permissions(manage_guild=True)
    async def tournament_round(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        tournament_id: str,
        round_number: int,
    ) -> None:
        try:
            tournament = await arun(db.get_tournament, tournament_id)
        except Exception as exc:
            await interaction.response.send_message(
                f"❌ DB hiba: `{exc}`", ephemeral=True
            )
            return
        if not tournament:
            await interaction.response.send_message(
                f"❌ Nincs ilyen Tournament: `{tournament_id}`.", ephemeral=True
            )
            return

        if action.value == "start":
            await interaction.response.defer(ephemeral=True, thinking=True)
            await self._start_round(tournament, round_number)
            await interaction.followup.send(
                f"✅ Kör {round_number} elindítva a **{tournament['name']}** Tournament-n.",
                ephemeral=True,
            )
        else:  # stop
            try:
                await arun(
                    db.update_tournament,
                    tournament_id,
                    status="stopped",
                    current_round=round_number,
                )
            except Exception as exc:
                await interaction.response.send_message(
                    f"❌ DB hiba: `{exc}`", ephemeral=True
                )
                return
            await interaction.response.send_message(
                f"🛑 A **{tournament['name']}** Tournament {round_number}. köre leállítva.",
                ephemeral=True,
            )

    # ------------------------------------------------------------------ #
    # Round start implementation
    # ------------------------------------------------------------------ #
    async def _start_round(self, tournament: dict, round_number: int) -> None:
        # Resolve guild: prefer the guild_id stored on the tournament row,
        # fall back to the configured GUILD_ID, then to the first guild.
        guild_id_raw = tournament.get("guild_id") or config.guild_id
        try:
            guild_id_int = int(guild_id_raw)
        except (TypeError, ValueError):
            guild_id_int = config.guild_id

        guild = self.bot.get_guild(guild_id_int) or (
            self.bot.guilds[0] if self.bot.guilds else None
        )
        if guild is None:
            log.error("Bot is not in any guild; cannot start round.")
            return

        # Read players from the JSONB column
        try:
            players = await arun(db.get_tournament_players, tournament["id"])
        except Exception as exc:
            log.error("Failed to fetch players for tournament %s: %s", tournament["id"], exc)
            return
        if len(players) < 2:
            log.warning(
                "Not enough players to start a round (%d) for tournament %s.",
                len(players),
                tournament.get("name"),
            )
            return

        pairs = pair_players(players)
        matches_lines = [format_match_line(p1, p2) for p1, p2 in pairs]

        # Update the queue message with the round embed (always edit the same message)
        try:
            queue_message_id = int(tournament["queue_message_id"])
            message = await find_queue_message(guild, queue_message_id)
            if message is not None:
                round_embed = discord.Embed(
                    title=f"{tournament['name']} Tournament - {round_number}. kör",
                    color=0x00E5FF,
                )
                round_embed.add_field(
                    name="Játékosok", value=str(len(players)), inline=False
                )
                round_embed.add_field(
                    name="Meccsek",
                    value="\n".join(matches_lines) or "—",
                    inline=False,
                )
                # Clear the Join/Leave view since the queue phase is over
                await message.edit(embed=round_embed, view=None)
            else:
                log.warning(
                    "Could not find queue message %s in any channel of guild %s.",
                    queue_message_id,
                    guild.name,
                )
        except (discord.HTTPException, ValueError, KeyError) as exc:
            log.warning("Failed to update queue message: %s", exc)

        # Create a ticket channel for each match
        category = guild.get_channel(config.ticket_category_id)
        if not isinstance(category, discord.CategoryChannel):
            log.error(
                "TICKET_CATEGORY_ID (%s) is not a category channel; cannot create tickets.",
                config.ticket_category_id,
            )
            return

        for idx, (p1, p2) in enumerate(pairs, start=1):
            if p2 is None:
                # Bye — skip ticket creation but record the match
                try:
                    await arun(
                        db.create_match,
                        tournament_id=tournament["id"],
                        round_number=round_number,
                        player1_discord_id=int(p1["discord_id"]),
                        player2_discord_id=0,
                        player1_mc=p1["minecraft_name"],
                        player2_mc="",
                        ticket_channel_id=0,
                    )
                except Exception as exc:
                    log.error("Failed to record bye match: %s", exc)
                continue

            channel_name = safe_channel_name(
                f"{tournament['name']}-r{round_number}-m{idx}-{p1['minecraft_name']}-vs-{p2['minecraft_name']}"
            )
            overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                guild.me: discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, manage_channels=True
                ),
            }
            p1_member = guild.get_member(int(p1["discord_id"]))
            if p1_member is not None:
                overwrites[p1_member] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True
                )
            p2_member = guild.get_member(int(p2["discord_id"]))
            if p2_member is not None:
                overwrites[p2_member] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True
                )
            regulator_role = guild.get_role(config.regulator_role_id)
            if regulator_role is not None:
                overwrites[regulator_role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    manage_messages=True,
                    read_message_history=True,
                )

            try:
                ticket_channel = await guild.create_text_channel(
                    name=channel_name,
                    category=category,
                    overwrites=overwrites,
                    reason=f"Tournament ticket: {tournament['name']} R{round_number} M{idx}",
                )
            except discord.HTTPException as exc:
                log.error("Failed to create ticket channel: %s", exc)
                continue

            try:
                match = await arun(
                    db.create_match,
                    tournament_id=tournament["id"],
                    round_number=round_number,
                    player1_discord_id=int(p1["discord_id"]),
                    player2_discord_id=int(p2["discord_id"]),
                    player1_mc=p1["minecraft_name"],
                    player2_mc=p2["minecraft_name"],
                    ticket_channel_id=ticket_channel.id,
                )
            except Exception as exc:
                log.error("Failed to persist match in DB: %s", exc)
                continue

            embed = discord.Embed(
                title=f"{tournament['name']} Tournament",
                description=(
                    f"**Párosítás:** <@{p1['discord_id']}> vs <@{p2['discord_id']}>\n"
                    f"**In-game nevek:** {p1['minecraft_name']} vs {p2['minecraft_name']}\n\n"
                    f"Regulator: <@&{config.regulator_role_id}> szerepkörű tagok használhatják a gombokat."
                ),
                color=0x00E5FF,
            )
            view = TicketActionView(match_id=match["id"])
            await ticket_channel.send(
                content=f"<@{p1['discord_id']}> <@{p2['discord_id']}>",
                embed=embed,
                view=view,
            )

        try:
            await arun(
                db.update_tournament,
                tournament["id"],
                status="running",
                current_round=round_number,
            )
        except Exception as exc:
            log.error("Failed to mark tournament as running: %s", exc)

    # ------------------------------------------------------------------ #
    # Background loop: auto-start tournaments whose end_time has passed
    # ------------------------------------------------------------------ #
    @tasks.loop(seconds=config.auto_start_poll_seconds)
    async def auto_start_loop(self) -> None:
        try:
            pending = await arun(db.list_pending_tournaments)
        except Exception as exc:
            log.error("DB error in auto-start loop: %s", exc)
            return
        for tournament in pending:
            try:
                next_round = int(tournament.get("current_round") or 0) + 1
                await self._start_round(tournament, next_round)
                log.info(
                    "Auto-started tournament %s round %d",
                    tournament.get("name"),
                    next_round,
                )
            except Exception as exc:
                log.exception(
                    "Failed to auto-start tournament %s: %s", tournament.get("id"), exc
                )

    @auto_start_loop.before_loop
    async def _wait_until_ready(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TournamentCog(bot))
