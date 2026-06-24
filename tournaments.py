"""Tournament lifecycle for NeonTiers.

Hosts the ``/tournamentqueue`` and ``/tournamentround`` slash commands, the
background auto-start loop, and the heavy ``_start_round`` method that pairs
players, edits the queue message, and creates per-match ticket channels.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import config
from database import arun, db
from utils import (
    format_discord_timestamp,
    format_match_line,
    format_relative_timestamp,
    pair_players,
    parse_timestamp,
    safe_channel_name,
    utcnow,
)
from views import QueueJoinView, TicketActionView, build_queue_embed

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Channel cache: queue_message_id -> channel_id
# Avoids O(N) full-channel scan on every round start.
# ----------------------------------------------------------------------

_queue_channel_cache: dict[int, int] = {}

# Per-tournament round-start locks (separate from player-op locks in views.py).
_round_locks: dict[str, asyncio.Lock] = {}


def _get_round_lock(tournament_id: str) -> asyncio.Lock:
    lock = _round_locks.get(tournament_id)
    if lock is None:
        lock = asyncio.Lock()
        _round_locks[tournament_id] = lock
    return lock


# ----------------------------------------------------------------------
# Queue message discovery (cache-first, parallel fallback)
# ----------------------------------------------------------------------

async def find_queue_message(
    guild: discord.Guild, queue_message_id: int
) -> Optional[discord.Message]:
    """Locate the queue message for *queue_message_id*.

    Strategy:
      1. ``queue_message_id <= 0`` → never sent, return ``None``.
      2. Try the cache; on hit, ``fetch_message`` on the cached channel.
         On NotFound, evict the stale entry.
      3. Fall back to a parallel scan over all text channels with
         ``asyncio.gather`` — first hit wins and is cached.
    """
    if queue_message_id <= 0:
        return None

    # --- cache hit ---
    cached_channel_id = _queue_channel_cache.get(queue_message_id)
    if cached_channel_id is not None:
        channel = guild.get_channel(cached_channel_id)
        if isinstance(channel, discord.TextChannel):
            try:
                return await channel.fetch_message(queue_message_id)
            except discord.NotFound:
                _queue_channel_cache.pop(queue_message_id, None)
            except discord.HTTPException as exc:
                log.warning("find_queue_message: cached fetch failed: %s", exc)

    # --- parallel full scan ---
    channels = [c for c in guild.text_channels]
    if not channels:
        return None

    found: list[tuple[int, discord.Message]] = []
    lock = asyncio.Lock()

    async def _probe(channel: discord.TextChannel) -> None:
        try:
            msg = await channel.fetch_message(queue_message_id)
        except discord.NotFound:
            return
        except discord.HTTPException:
            return
        async with lock:
            found.append((channel.id, msg))

    await asyncio.gather(*[_probe(c) for c in channels], return_exceptions=True)

    if found:
        channel_id, message = found[0]
        _queue_channel_cache[queue_message_id] = channel_id
        return message

    return None


# ----------------------------------------------------------------------
# TournamentCog
# ----------------------------------------------------------------------

class TournamentCog(commands.Cog):
    """Slash commands + auto-start loop for tournament lifecycle."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.auto_start_loop.start()

    async def cog_unload(self) -> None:  # type: ignore[override]
        self.auto_start_loop.cancel()

    # ------------------------------------------------------------------
    # /tournamentqueue
    # ------------------------------------------------------------------

    @app_commands.command(
        name="tournamentqueue",
        description="Létrehoz egy Tournament queue-t csatlakozási/kilépési gombokkal.",
    )
    @app_commands.describe(
        name="A Tournament neve.",
        timestamp="Indulási idő (ISO-8601 vagy YYYY-MM-DD HH:MM).",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def tournamentqueue(
        self,
        interaction: discord.Interaction,
        name: str,
        timestamp: str,
    ) -> None:
        try:
            end_time = parse_timestamp(timestamp)
        except ValueError as exc:
            await interaction.response.send_message(
                f"Érvénytelen időformátum: {exc}", ephemeral=True
            )
            return

        if end_time < utcnow():
            await interaction.response.send_message(
                "A megadott időpont a múltban van.", ephemeral=True
            )
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Ezt a parancsot szöveges csatornában használd.",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Ezt a parancsot szerveren belül használd.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        tournament_id = str(uuid.uuid4())

        # Insert DB row FIRST with queue_message_id=0 — eliminates the race
        # where a user clicks Join before the row exists.
        try:
            await arun(
                db.create_tournament,
                name=name,
                end_time=end_time,
                queue_message_id=0,
                guild_id=guild.id,
                tournament_id=tournament_id,
            )
        except RuntimeError as exc:
            log.error("create_tournament failed: %s", exc)
            await interaction.followup.send(
                f"Hiba a Tournament létrehozásakor: {exc}",
                ephemeral=True,
            )
            return

        # Build queue message.
        embed = discord.Embed(title=f"{name} Tournament", color=0x00E5FF)
        embed.description = (
            f"**Indulás:** {format_discord_timestamp(end_time)} "
            f"({format_relative_timestamp(end_time)})"
        )
        embed.add_field(name="Játékosok", value="0", inline=False)
        embed.set_footer(
            text="Kattints a Belépés a tournamentbe gombra a jelentkezéshez."
        )

        view = QueueJoinView(tournament_id)
        try:
            sent = await channel.send(embed=embed, view=view)
        except discord.HTTPException as exc:
            log.error("queue send failed: %s", exc)
            await arun(db.delete_tournament, tournament_id)
            await interaction.followup.send(
                f"Hiba az üzenet elküldésekor: {exc}",
                ephemeral=True,
            )
            return

        # Patch the row with the real message id, then cache the channel.
        await arun(
            db.update_tournament,
            tournament_id,
            queue_message_id=sent.id,
        )
        _queue_channel_cache[sent.id] = channel.id

        log.info(
            "Tournament created: id=%s name=%r guild=%s message=%s",
            tournament_id,
            name,
            guild.id,
            sent.id,
        )

        await interaction.followup.send(
            f"✅ Tournament létrehozva. ID: `{tournament_id}`",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # /tournamentround
    # ------------------------------------------------------------------

    @app_commands.command(
        name="tournamentround",
        description="Kör indítása vagy leállítása egy meglévő Tournament-re.",
    )
    @app_commands.describe(
        action="start vagy stop",
        tournament_id="A Tournament UUID-ja.",
        round_number="A kör száma (1-tőlindexelve).",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.choices(
        action=[
            app_commands.Choice(name="start", value="start"),
            app_commands.Choice(name="stop", value="stop"),
        ]
    )
    async def tournamentround(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        tournament_id: str,
        round_number: int,
    ) -> None:
        tournament = await arun(db.get_tournament, tournament_id)
        if tournament is None:
            await interaction.response.send_message(
                f"Nem található Tournament ezzel az azonosítóval: `{tournament_id}`",
                ephemeral=True,
            )
            return

        if action.value == "start":
            await interaction.response.defer(ephemeral=True, thinking=True)
            await self._start_round(tournament, round_number)
            await interaction.followup.send(
                f"✅ Kör {round_number} elindítva a Tournamenten "
                f"`{tournament_id}`.",
                ephemeral=True,
            )
        else:  # stop
            await arun(
                db.update_tournament,
                tournament_id,
                status="stopped",
                current_round=round_number,
            )
            await interaction.response.send_message(
                f"🛑 Tournament `{tournament_id}` leállítva "
                f"a(z) {round_number}. körnél.",
                ephemeral=True,
            )

    # ------------------------------------------------------------------
    # _start_round — the heavy lifter
    # ------------------------------------------------------------------

    async def _start_round(
        self, tournament: dict, round_number: int
    ) -> None:
        tournament_id = tournament["id"]

        # 1. Skip if already in progress (non-blocking acquire).
        lock = _get_round_lock(tournament_id)
        if lock.locked():
            log.info(
                "tournament %s round %d start already in progress, skipping",
                tournament_id,
                round_number,
            )
            return
        async with lock:
            # 2. Atomic claim — prevents double-starts when the auto-start
            #    loop races with a manual /tournamentround call.
            claimed = await arun(db.claim_for_round, tournament_id, round_number)
            if not claimed:
                log.info(
                    "tournament %s not claimed for round %d (already on it?)",
                    tournament_id,
                    round_number,
                )
                return

            # 3. Resolve guild.
            guild_id = int(tournament.get("guild_id") or 0)
            guild = self.bot.get_guild(guild_id) if guild_id else None
            if guild is None and config.guild_id:
                guild = self.bot.get_guild(config.guild_id)
            if guild is None and self.bot.guilds:
                guild = self.bot.guilds[0]
            if guild is None:
                log.error(
                    "tournament %s: no guild resolvable, cannot start round",
                    tournament_id,
                )
                return

            # 4. Fetch + pair players.
            players = await arun(db.get_tournament_players, tournament_id)
            if len(players) < 2:
                log.warning(
                    "tournament %s: only %d players, skipping round %d",
                    tournament_id,
                    len(players),
                    round_number,
                )
                return

            pairs = pair_players(players)
            match_lines = [format_match_line(p1, p2) for p1, p2 in pairs]

            # 5. Update queue message.
            queue_message_id = int(tournament.get("queue_message_id") or 0)
            queue_message = await find_queue_message(guild, queue_message_id)
            name = tournament.get("name", "Tournament")

            if queue_message is not None:
                round_embed = discord.Embed(
                    title=f"{name} Tournament - {round_number}. kör",
                    color=0x00E5FF,
                )
                round_embed.add_field(
                    name="Játékosok", value=str(len(players)), inline=False
                )
                matches_text = "\n".join(match_lines)
                if len(matches_text) > 1024:
                    matches_text = matches_text[:1021] + "…"
                round_embed.add_field(
                    name="Meccsek",
                    value=matches_text or "—",
                    inline=False,
                )
                try:
                    await queue_message.edit(embed=round_embed, view=None)
                except discord.HTTPException as exc:
                    log.warning("queue message edit failed: %s", exc)

            # 6. Create ticket channels.
            category = (
                guild.get_channel(config.ticket_category_id)
                if config.ticket_category_id
                else None
            )
            if config.ticket_category_id and not isinstance(category, discord.CategoryChannel):
                log.warning(
                    "TICKET_CATEGORY_ID %s is not a category — tickets will be top-level.",
                    config.ticket_category_id,
                )
                category = None

            bot_member = guild.me
            created = 0
            for idx, (p1, p2) in enumerate(pairs, start=1):
                p1_id = int(p1["discord_id"])
                p1_mc = p1.get("minecraft_name", "?")

                if p2 is None:
                    # Bye — record a match with player2=0 and no ticket.
                    try:
                        await arun(
                            db.create_match,
                            tournament_id,
                            round_number,
                            p1_id,
                            0,
                            p1_mc,
                            "",
                            0,
                        )
                    except RuntimeError as exc:
                        log.error("create_match (bye) failed: %s", exc)
                    continue

                p2_id = int(p2["discord_id"])
                p2_mc = p2.get("minecraft_name", "?")

                channel_name = safe_channel_name(
                    f"{name}-r{round_number}-m{idx}-{p1_mc}-vs-{p2_mc}"
                )

                overwrites: dict[discord.Member | discord.Role, discord.PermissionOverwrite] = {
                    guild.default_role: discord.PermissionOverwrite(view_channel=False),
                    bot_member: discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        read_message_history=True,
                        manage_messages=True,
                    ),
                }
                member1 = guild.get_member(p1_id)
                if member1 is not None:
                    overwrites[member1] = discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        read_message_history=True,
                    )
                member2 = guild.get_member(p2_id)
                if member2 is not None:
                    overwrites[member2] = discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        read_message_history=True,
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
                    ticket_channel = await guild.create_text_channel(
                        channel_name,
                        category=category if isinstance(category, discord.CategoryChannel) else None,
                        overwrites=overwrites,
                    )
                except discord.HTTPException as exc:
                    log.error(
                        "ticket channel create failed for match %d (%s vs %s): %s",
                        idx,
                        p1_mc,
                        p2_mc,
                        exc,
                    )
                    continue

                try:
                    match_row = await arun(
                        db.create_match,
                        tournament_id,
                        round_number,
                        p1_id,
                        p2_id,
                        p1_mc,
                        p2_mc,
                        ticket_channel.id,
                    )
                except RuntimeError as exc:
                    log.error("create_match failed: %s", exc)
                    try:
                        await ticket_channel.delete()
                    except discord.HTTPException:
                        pass
                    continue

                match_id = match_row["id"]
                ticket_embed = discord.Embed(
                    title=f"{name} Tournament",
                    color=0x00E5FF,
                )
                ticket_embed.description = (
                    f"**Párosítás:**\n<@{p1_id}> ({p1_mc}) vs <@{p2_id}> ({p2_mc})\n\n"
                    f"**In-game nevek:**\n`{p1_mc}` vs `{p2_mc}`\n\n"
                    f"Regulator: az eredményt az **Eredmény beírása** gombbal rögzítsd."
                )
                ticket_view = TicketActionView(match_id)
                try:
                    await ticket_channel.send(
                        content=f"<@{p1_id}> <@{p2_id}>",
                        embed=ticket_embed,
                        view=ticket_view,
                    )
                except discord.HTTPException as exc:
                    log.warning("ticket message send failed: %s", exc)

                created += 1

            log.info(
                "tournament %s round %d started: %d matches, %d players",
                tournament_id,
                round_number,
                created,
                len(players),
            )

    # ------------------------------------------------------------------
    # Background auto-start loop
    # ------------------------------------------------------------------

    @tasks.loop(seconds=config.auto_start_poll_seconds)
    async def auto_start_loop(self) -> None:
        """Auto-start any queued tournament whose end_time has passed."""
        try:
            pending = await arun(db.list_pending_tournaments)
        except Exception as exc:  # pragma: no cover - DB hiccup
            log.error("auto_start_loop: list_pending_tournaments failed: %s", exc)
            return

        for tournament in pending:
            try:
                next_round = int(tournament.get("current_round") or 0) + 1
                await self._start_round(tournament, next_round)
            except Exception as exc:  # never let one bad tournament kill the loop
                log.error(
                    "auto_start_loop: _start_round failed for %s: %s",
                    tournament.get("id"),
                    exc,
                )

    @auto_start_loop.before_loop
    async def _before_auto_start_loop(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TournamentCog(bot))
