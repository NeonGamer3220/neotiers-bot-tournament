"""Discord UI components for NeonTiers.

All persistent views are built with explicit ``ui.Button(custom_id=...)``
instances created in ``__init__`` rather than the ``@ui.button`` decorator.
The decorator builds a single custom_id at class-definition time, so it cannot
produce per-instance custom_ids (one per tournament / match) on persistent
views. Building buttons explicitly in ``__init__`` is the only supported
pattern for that use-case.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

import discord
from discord import ui
from discord.ext import commands

from config import config
from database import arun, db
from utils import (
    format_discord_timestamp,
    format_relative_timestamp,
    generate_code,
    parse_timestamp,
)

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Per-tournament locks for player add/remove (fetch-modify-write JSONB)
# ----------------------------------------------------------------------

_tournament_locks: dict[str, asyncio.Lock] = {}


def _get_tournament_lock(tournament_id: str) -> asyncio.Lock:
    lock = _tournament_locks.get(tournament_id)
    if lock is None:
        lock = asyncio.Lock()
        _tournament_locks[tournament_id] = lock
    return lock


# ----------------------------------------------------------------------
# Queue message view
# ----------------------------------------------------------------------

class QueueJoinView(ui.View):
    """Persistent view with Belépés / Kilépés buttons for a queue message."""

    def __init__(self, tournament_id: str) -> None:
        super().__init__(timeout=None)
        self.tournament_id = tournament_id

        # Explicit buttons (NOT @ui.button) — required for per-instance
        # custom_ids on persistent views.
        join_btn = ui.Button(
            label="Belépés a tournamentbe",
            style=discord.ButtonStyle.success,
            emoji="✅",
            custom_id=f"join_tournament_{tournament_id}",
        )
        join_btn.callback = self._join_callback  # type: ignore[assignment]

        leave_btn = ui.Button(
            label="Kilépés a tournamentből",
            style=discord.ButtonStyle.danger,
            emoji="❌",
            custom_id=f"leave_tournament_{tournament_id}",
        )
        leave_btn.callback = self._leave_callback  # type: ignore[assignment]

        self.add_item(join_btn)
        self.add_item(leave_btn)

    # ----- callbacks just delegate to module-level handlers -----

    async def _join_callback(self, interaction: discord.Interaction) -> None:
        await handle_join(interaction, self.tournament_id)

    async def _leave_callback(self, interaction: discord.Interaction) -> None:
        await handle_leave(interaction, self.tournament_id)


async def handle_join(interaction: discord.Interaction, tournament_id: str) -> None:
    """Handler for the Belépés button."""
    await interaction.response.defer(ephemeral=True, thinking=True)

    try:
        tournament = await arun(db.get_tournament, tournament_id)
    except Exception as exc:
        log.exception("handle_join: get_tournament failed (type=%s)", type(exc).__name__)
        await interaction.followup.send(
            f"❌ Hiba a Tournament lekérésekor.\n"
            f"**Típus:** `{type(exc).__name__}`\n**Üzenet:** `{exc}`\n"
            f"**Tournament ID:** `{tournament_id}`",
            ephemeral=True,
        )
        return

    if tournament is None:
        await interaction.followup.send(
            f"Ez a Tournament nem elérhető. (id `{tournament_id}`)\n\n"
            f"Lehetséges okok:\n"
            f"• A sor nem került be az adatbázisba (RLS / schema hiba)\n"
            f"• A Tournament törölve lett\n"
            f"• A bot újraindult és nem találta az adatbázisban\n\n"
            f"Futtasd a `/dbtest` parancsot a diagnosztikához.",
            ephemeral=True,
        )
        return
    if tournament.get("status") != "queued":
        await interaction.followup.send(
            "Ez a Tournament már elindult, nem lehet csatlakozni.",
            ephemeral=True,
        )
        return

    discord_id = interaction.user.id
    linked = await arun(db.get_linked_account, discord_id)

    if linked is None:
        # Generate a one-time linking code and DM it.
        code = generate_code()
        try:
            await arun(
                db.create_pending_code,
                discord_id,
                code,
                config.pending_code_ttl_minutes,
            )
        except RuntimeError as exc:
            log.error("create_pending_code failed: %s", exc)
            await interaction.followup.send(
                "Hiba történt a kód generálásakor. Kérlek, próbáld újra később.",
                ephemeral=True,
            )
            return

        instructions = (
            f"A Minecraft fiókod összekapcsolásához használd a következő kódot:\n\n"
            f"```\n/link {code}\n```\n"
            f"Futtasd ezt a parancsot a **chaosffa.kinetic.host** szerveren.\n\n"
            f"A kód **{config.pending_code_ttl_minutes} percig** érvényes."
        )
        try:
            await interaction.user.send(instructions)
        except discord.Forbidden:
            await interaction.followup.send(
                "Nem tudok neked privát üzenetet küldeni. "
                "Kérlek, engedélyezd a DM-et a szerveren lévő tagoktól, "
                "és próbáld újra.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            "Elküldtem a Minecraft összekapcsoló kódot privát üzenetben. "
            "Futtasd a `/link {code}` parancsot a "
            "**chaosffa.kinetic.host** szerveren.".format(code=code),
            ephemeral=True,
        )
        return

    minecraft_name = linked.get("minecraft_name", "?")

    # Player ops are fetch-modify-write on a JSONB column — hold a lock.
    lock = _get_tournament_lock(tournament_id)
    async with lock:
        added = await arun(
            db.add_player_to_tournament,
            tournament_id,
            discord_id,
            minecraft_name,
        )

    if not added:
        await interaction.followup.send(
            "Már csatlakoztál ehhez a Tournamenthez.", ephemeral=True
        )
        return

    await interaction.followup.send(
        f"Sikeresen csatlakoztál a Tournamenthez! "
        f"Minecraft név: **{minecraft_name}**",
        ephemeral=True,
    )

    await refresh_queue_embed(interaction, tournament)


async def handle_leave(interaction: discord.Interaction, tournament_id: str) -> None:
    """Handler for the Kilépés button."""
    await interaction.response.defer(ephemeral=True, thinking=True)

    tournament = await arun(db.get_tournament, tournament_id)
    if tournament is None:
        await interaction.followup.send(
            f"Ez a Tournament nem elérhető. (id `{tournament_id}`)",
            ephemeral=True,
        )
        return

    lock = _get_tournament_lock(tournament_id)
    async with lock:
        removed = await arun(
            db.remove_player_from_tournament, tournament_id, interaction.user.id
        )

    if not removed:
        await interaction.followup.send(
            "Nem voltál regisztrálva erre a Tournamentre.", ephemeral=True
        )
        return

    await interaction.followup.send(
        "Sikeresen kiléptél a Tournamentből.", ephemeral=True
    )

    await refresh_queue_embed(interaction, tournament)


async def refresh_queue_embed(
    interaction: discord.Interaction, tournament: dict
) -> None:
    """Re-render the queue message with the latest player list.

    Best-effort: swallows Discord HTTP errors so a stale message never
    breaks a join/leave flow.
    """
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        return

    queue_message_id = int(tournament.get("queue_message_id") or 0)
    if queue_message_id <= 0:
        return

    try:
        message = await channel.fetch_message(queue_message_id)
    except discord.HTTPException as exc:
        log.warning("refresh_queue_embed: fetch_message failed: %s", exc)
        return

    players = await arun(db.get_tournament_players, tournament["id"])
    embed = build_queue_embed(tournament, players)
    view = QueueJoinView(tournament["id"])
    try:
        await message.edit(embed=embed, view=view)
    except discord.HTTPException as exc:
        log.warning("refresh_queue_embed: message edit failed: %s", exc)


def build_queue_embed(
    tournament: dict, players: list[dict]
) -> discord.Embed:
    """Build the queue message embed from a tournament row + roster."""
    name = tournament.get("name", "Tournament")
    embed = discord.Embed(
        title=f"{name} Tournament",
        color=0x00E5FF,
    )

    try:
        end_time = parse_timestamp(tournament["end_time"])
        description = (
            f"**Indulás:** {format_discord_timestamp(end_time)} "
            f"({format_relative_timestamp(end_time)})"
        )
    except (ValueError, KeyError):
        description = "**Indulás:** ismeretlen"

    embed.description = description
    embed.add_field(name="Játékosok", value=str(len(players)), inline=False)

    if players:
        lines: list[str] = []
        total = 0
        for p in players[:25]:
            line = f"<@{int(p['discord_id'])}> — {p.get('minecraft_name', '?')}"
            if total + len(line) + 1 > 1024:
                # Truncate to 1024 (Discord field value limit).
                remaining = 1024 - total - 2
                if remaining > 0:
                    lines.append(line[:remaining] + "…")
                break
            lines.append(line)
            total += len(line) + 1
        embed.add_field(
            name="Regisztrált játékosok",
            value="\n".join(lines) or "—",
            inline=False,
        )

    embed.set_footer(
        text="Kattints a Belépés a tournamentbe gombra a jelentkezéshez."
    )
    return embed


# ----------------------------------------------------------------------
# Ticket view + result modal
# ----------------------------------------------------------------------

class TicketActionView(ui.View):
    """Persistent view attached to each ticket channel message."""

    def __init__(self, match_id: str) -> None:
        super().__init__(timeout=None)
        self.match_id = match_id

        close_btn = ui.Button(
            label="Jegy lezárása",
            style=discord.ButtonStyle.secondary,
            emoji="🔒",
            custom_id=f"close_ticket_{match_id}",
        )
        close_btn.callback = self._close_callback  # type: ignore[assignment]

        result_btn = ui.Button(
            label="Eredmény beírása",
            style=discord.ButtonStyle.primary,
            emoji="📝",
            custom_id=f"result_{match_id}",
        )
        result_btn.callback = self._result_callback  # type: ignore[assignment]

        self.add_item(close_btn)
        self.add_item(result_btn)

    async def _close_callback(self, interaction: discord.Interaction) -> None:
        # Resolve per-tournament regulator role from the match's tournament.
        match = await arun(db.get_match_by_ticket, interaction.channel_id)
        regulator_id = config.regulator_role_id
        if match:
            tournament_id = match.get("tournament_id")
            if tournament_id:
                tournament = await arun(db.get_tournament, tournament_id)
                if tournament and tournament.get("regulator_role_id"):
                    regulator_id = int(tournament["regulator_role_id"])

        if not regulator_id or regulator_id not in {r.id for r in interaction.user.roles}:
            await interaction.response.send_message(
                "Nincs jogod lezárni ezt a jegyet. "
                "Csak a regulator szerepkörrel rendelkező tagok zárhatnak jegyet.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)
        channel = interaction.channel

        if isinstance(channel, discord.Thread):
            try:
                await channel.edit(archived=True, locked=True)
            except discord.HTTPException as exc:
                log.warning("ticket close (thread): %s", exc)
            await channel.send("🎟️ Jegy lezárva.")
        elif isinstance(channel, discord.TextChannel):
            # Revoke access for all current members (except the bot).
            overwrites = dict(channel.overwrites)
            for target, ov in list(overwrites.items()):
                if isinstance(target, discord.Member) and not target.bot:
                    ov = ov.copy() if hasattr(ov, "copy") else discord.PermissionOverwrite.from_pair(discord.PermissionOverwrite(), discord.PermissionOverwrite())
                    ov.view_channel = False
                    ov.send_messages = False
                    overwrites[target] = ov
            try:
                await channel.edit(overwrites=overwrites)
                await channel.send("🎟️ Jegy lezárva.")
                new_name = f"closed-{channel.name}"[:100]
                await channel.edit(name=new_name)
            except discord.HTTPException as exc:
                log.warning("ticket close (text): %s", exc)

        await interaction.followup.send("A jegy le lett zárva.")

    async def _result_callback(self, interaction: discord.Interaction) -> None:
        # Resolve per-tournament regulator role from the match's tournament.
        match = await arun(db.get_match_by_ticket, interaction.channel_id)
        regulator_id = config.regulator_role_id
        if match:
            tournament_id = match.get("tournament_id")
            if tournament_id:
                tournament = await arun(db.get_tournament, tournament_id)
                if tournament and tournament.get("regulator_role_id"):
                    regulator_id = int(tournament["regulator_role_id"])

        if not regulator_id or regulator_id not in {r.id for r in interaction.user.roles}:
            await interaction.response.send_message(
                "Nincs jogod eredményt beírni. "
                "Csak a regulator szerepkörrel rendelkező tagok rögzíthetnek eredményt.",
                ephemeral=True,
            )
            return

        if match is None:
            await interaction.response.send_message(
                "Ez a csatorna nem egy aktív meccs jegye.", ephemeral=True
            )
            return

        await interaction.response.send_modal(ResultModal(match_id=match["id"]))


_MENTION_RE = re.compile(r"^<@!?(\d+)>$")


class ResultModal(ui.Modal, title="Eredmény beírása"):
    """Modal for recording a match winner (+ optional score)."""

    winner_input = ui.TextInput(
        label="Győztes Discord ID vagy @mention",
        required=True,
        max_length=50,
    )
    score_input = ui.TextInput(
        label="Eredmény (opcionális)",
        required=False,
        max_length=20,
    )

    def __init__(self, match_id: str) -> None:
        super().__init__()
        self.match_id = match_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = str(self.winner_input.value).strip()
        score = str(self.score_input.value).strip() if self.score_input.value else ""

        # Parse winner: <@id>, <@!id>, or bare integer.
        match = _MENTION_RE.match(raw)
        if match:
            winner_id_str = match.group(1)
        else:
            winner_id_str = raw
        try:
            winner_id = int(winner_id_str)
        except ValueError:
            await interaction.response.send_message(
                "Érvénytelen győztes azonosító. "
                "Add meg a Discord ID-t vagy @mention-t.",
                ephemeral=True,
            )
            return

        await arun(db.set_match_winner, self.match_id, winner_id)
        match_row = await arun(db.get_match_by_ticket, interaction.channel_id)
        if match_row is None:
            await interaction.response.send_message(
                "Eredmény rögzítve, de a meccs már nem található.",
                ephemeral=True,
            )
            return

        tournament_data = match_row.get("tournaments") or {}
        tournament_name = (
            tournament_data.get("name") if isinstance(tournament_data, dict) else None
        ) or "Tournament"

        # Resolve per-tournament results channel (fall back to global config).
        results_channel_id = config.results_channel_id
        tournament_id = match_row.get("tournament_id")
        if tournament_id:
            tournament = await arun(db.get_tournament, tournament_id)
            if tournament and tournament.get("results_channel_id"):
                results_channel_id = int(tournament["results_channel_id"])

        p1_id = int(match_row.get("player1_discord_id") or 0)
        p2_id = int(match_row.get("player2_discord_id") or 0)
        p1_mc = match_row.get("player1_mc") or "?"
        p2_mc = match_row.get("player2_mc") or "?"

        score_clause = f" — Eredmény: {score}" if score else ""
        description = (
            f"🏆 Győztes: <@{winner_id}>\n"
            f"Párosítás: <@{p1_id}> ({p1_mc}) vs <@{p2_id}> ({p2_mc})"
            f"{score_clause}"
        )

        embed = discord.Embed(
            title=f"{tournament_name} Tournament — Eredmény",
            description=description,
            color=0xFFD700,
        )

        results_channel = (
            interaction.guild.get_channel(results_channel_id)
            if interaction.guild and results_channel_id
            else None
        )
        if isinstance(results_channel, discord.TextChannel):
            try:
                await results_channel.send(embed=embed)
            except discord.HTTPException as exc:
                log.warning("result post to results channel failed: %s", exc)

        await interaction.response.send_message(
            f"Eredmény rögzítve. Győztes: <@{winner_id}>",
            ephemeral=True,
        )
