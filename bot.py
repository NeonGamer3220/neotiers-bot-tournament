"""NeonTiers Tournament Discord Bot — consolidated entry point.

This file merges utils.py + views.py + tournaments.py + main.py into one
module so the whole bot is 4 files: requirements.txt, config.py,
database.py, bot.py.

Run with:  python bot.py
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import string
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord import ui
from discord.ext import commands, tasks

from config import config
from database import arun, db

# ======================================================================
# Logging
# ======================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("neontiers")


# ======================================================================
# Utilities (formerly utils.py)
# ======================================================================

_CODE_ALPHABET = "".join(
    c for c in (string.ascii_uppercase + string.digits)
    if c not in {"0", "O", "1", "I", "L"}
)

_TS_PATTERNS = (
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
)

_SAFE_NAME_RE_1 = re.compile(r"[^a-z0-9]+")
_SAFE_NAME_RE_2 = re.compile(r"-{2,}")
_MENTION_RE = re.compile(r"^<@!?(\d+)>$")


def generate_code(length: Optional[int] = None) -> str:
    n = length if length is not None else config.pending_code_length
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(n))


def parse_timestamp(raw) -> datetime:
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return datetime.fromtimestamp(raw, tz=timezone.utc)
    if not raw or not str(raw).strip():
        raise ValueError("Empty timestamp.")
    text = str(raw).strip()
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    last_err = None
    for fmt in _TS_PATTERNS:
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError as exc:
            last_err = exc
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    raise ValueError(
        f"Could not parse timestamp {raw!r}. Expected ISO-8601 or YYYY-MM-DD HH:MM."
    ) from last_err


def format_discord_timestamp(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return f"<t:{int(dt.timestamp())}:F>"


def format_relative_timestamp(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return f"<t:{int(dt.timestamp())}:R>"


def pair_players(players: list[dict]) -> list[tuple[dict, Optional[dict]]]:
    pairs: list[tuple[dict, Optional[dict]]] = []
    i = 0
    while i < len(players):
        p1 = players[i]
        p2 = players[i + 1] if i + 1 < len(players) else None
        pairs.append((p1, p2))
        i += 2
    return pairs


def format_match_line(p1: dict, p2: Optional[dict]) -> str:
    mc1 = p1.get("minecraft_name", "?")
    if p2 is None:
        return f"<@{int(p1['discord_id'])}> | {mc1} — *bye*"
    mc2 = p2.get("minecraft_name", "?")
    return f"<@{int(p1['discord_id'])}> | {mc1} vs <@{int(p2['discord_id'])}> | {mc2}"


def safe_channel_name(name: str, max_len: int = 90) -> str:
    if not name:
        return "channel"
    cleaned = _SAFE_NAME_RE_1.sub("-", name.lower())
    cleaned = _SAFE_NAME_RE_2.sub("-", cleaned)
    cleaned = cleaned.strip("-")
    return (cleaned or "channel")[:max_len]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ======================================================================
# Per-tournament locks + queue channel cache
# ======================================================================

_tournament_locks: dict[str, asyncio.Lock] = {}
_round_locks: dict[str, asyncio.Lock] = {}
_queue_channel_cache: dict[int, int] = {}


def _get_tournament_lock(tournament_id: str) -> asyncio.Lock:
    if tournament_id not in _tournament_locks:
        _tournament_locks[tournament_id] = asyncio.Lock()
    return _tournament_locks[tournament_id]


def _get_round_lock(tournament_id: str) -> asyncio.Lock:
    if tournament_id not in _round_locks:
        _round_locks[tournament_id] = asyncio.Lock()
    return _round_locks[tournament_id]


# ======================================================================
# Views (formerly views.py)
# ======================================================================

class QueueJoinView(ui.View):
    """Persistent Belépés / Kilépés buttons for a queue message."""

    def __init__(self, tournament_id: str) -> None:
        super().__init__(timeout=None)
        self.tournament_id = tournament_id

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

    async def _join_callback(self, interaction: discord.Interaction) -> None:
        await handle_join(interaction, self.tournament_id)

    async def _leave_callback(self, interaction: discord.Interaction) -> None:
        await handle_leave(interaction, self.tournament_id)


async def handle_join(interaction: discord.Interaction, tournament_id: str) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)

    try:
        tournament = await arun(db.get_tournament, tournament_id)
    except Exception as exc:
        log.exception("handle_join: get_tournament failed (%s)", type(exc).__name__)
        await interaction.followup.send(
            f"❌ Hiba a Tournament lekérésekor.\n"
            f"**Típus:** `{type(exc).__name__}`\n**Üzenet:** `{exc}`",
            ephemeral=True,
        )
        return

    if tournament is None:
        await interaction.followup.send(
            f"Ez a Tournament nem elérhető. (id `{tournament_id}`)",
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
    try:
        linked = await arun(db.get_linked_account, discord_id)
    except Exception as exc:
        log.exception("handle_join: get_linked_account failed (%s)", type(exc).__name__)
        await interaction.followup.send(
            f"❌ Hiba a fiók lekérésekor: `{exc}`", ephemeral=True
        )
        return

    if linked is None:
        code = generate_code()
        try:
            await arun(
                db.create_pending_code,
                discord_id, code, config.pending_code_ttl_minutes,
            )
        except Exception as exc:
            log.error("create_pending_code failed: %s", exc)
            await interaction.followup.send(
                "Hiba történt a kód generálásakor.", ephemeral=True,
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
                "Kérlek, engedélyezd a DM-et a szerveren lévő tagoktól.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"Elküldtem a Minecraft összekapcsoló kódot privát üzenetben. "
            f"Futtasd a `/link {code}` parancsot a **chaosffa.kinetic.host** szerveren.",
            ephemeral=True,
        )
        return

    minecraft_name = linked.get("minecraft_name", "?")
    lock = _get_tournament_lock(tournament_id)
    async with lock:
        try:
            added = await arun(
                db.add_player_to_tournament,
                tournament_id, discord_id, minecraft_name,
            )
        except Exception as exc:
            log.exception("add_player_to_tournament failed (%s)", type(exc).__name__)
            await interaction.followup.send(
                f"❌ Hiba a csatlakozáskor: `{exc}`", ephemeral=True
            )
            return

    if not added:
        await interaction.followup.send(
            "Már csatlakoztál ehhez a Tournamenthez.", ephemeral=True
        )
        return

    await interaction.followup.send(
        f"Sikeresen csatlakoztál! Minecraft név: **{minecraft_name}**",
        ephemeral=True,
    )
    await refresh_queue_embed(interaction, tournament)


async def handle_leave(interaction: discord.Interaction, tournament_id: str) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)

    try:
        tournament = await arun(db.get_tournament, tournament_id)
    except Exception as exc:
        log.exception("handle_leave: get_tournament failed (%s)", type(exc).__name__)
        await interaction.followup.send(f"❌ Hiba: `{exc}`", ephemeral=True)
        return

    if tournament is None:
        await interaction.followup.send(
            f"Ez a Tournament nem elérhető. (id `{tournament_id}`)",
            ephemeral=True,
        )
        return

    lock = _get_tournament_lock(tournament_id)
    async with lock:
        try:
            removed = await arun(
                db.remove_player_from_tournament, tournament_id, interaction.user.id
            )
        except Exception as exc:
            log.exception("remove_player failed (%s)", type(exc).__name__)
            await interaction.followup.send(f"❌ Hiba: `{exc}`", ephemeral=True)
            return

    if not removed:
        await interaction.followup.send(
            "Nem voltál regisztrálva erre a Tournamentre.", ephemeral=True
        )
        return

    await interaction.followup.send(
        "Sikeresen kiléptél a Tournamentből.", ephemeral=True
    )
    await refresh_queue_embed(interaction, tournament)


async def refresh_queue_embed(interaction: discord.Interaction, tournament: dict) -> None:
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
    try:
        players = await arun(db.get_tournament_players, tournament["id"])
    except Exception as exc:
        log.warning("refresh_queue_embed: get_tournament_players failed: %s", exc)
        return
    embed = build_queue_embed(tournament, players)
    view = QueueJoinView(tournament["id"])
    try:
        await message.edit(embed=embed, view=view)
    except discord.HTTPException as exc:
        log.warning("refresh_queue_embed: message edit failed: %s", exc)


def build_queue_embed(tournament: dict, players: list[dict]) -> discord.Embed:
    name = tournament.get("name", "Tournament")
    embed = discord.Embed(title=f"{name} Tournament", color=0x00E5FF)
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


class TicketActionView(ui.View):
    """Persistent Jegy lezárása / Eredmény beírása buttons."""

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

    async def _resolve_regulator_id(self, interaction: discord.Interaction) -> int:
        """Return per-tournament regulator role id (fallback to global config)."""
        regulator_id = config.regulator_role_id
        try:
            match = await arun(db.get_match_by_ticket, interaction.channel_id)
            if match:
                tournament_id = match.get("tournament_id")
                if tournament_id:
                    tournament = await arun(db.get_tournament, tournament_id)
                    if tournament and tournament.get("regulator_role_id"):
                        regulator_id = int(tournament["regulator_role_id"])
        except Exception as exc:
            log.warning("_resolve_regulator_id failed: %s", exc)
        return regulator_id

    async def _close_callback(self, interaction: discord.Interaction) -> None:
        regulator_id = await self._resolve_regulator_id(interaction)
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
            overwrites = dict(channel.overwrites)
            for target, ov in list(overwrites.items()):
                if isinstance(target, discord.Member) and not target.bot:
                    ov = ov.copy() if hasattr(ov, "copy") else discord.PermissionOverwrite(view_channel=False, send_messages=False)
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
        regulator_id = await self._resolve_regulator_id(interaction)
        if not regulator_id or regulator_id not in {r.id for r in interaction.user.roles}:
            await interaction.response.send_message(
                "Nincs jogod eredményt beírni. "
                "Csak a regulator szerepkörrel rendelkező tagok rögzíthetnek eredményt.",
                ephemeral=True,
            )
            return

        try:
            match = await arun(db.get_match_by_ticket, interaction.channel_id)
        except Exception as exc:
            log.exception("_result_callback: get_match_by_ticket failed (%s)", type(exc).__name__)
            await interaction.response.send_message(
                f"❌ Hiba a meccs lekérésekor: `{exc}`", ephemeral=True
            )
            return

        if match is None:
            await interaction.response.send_message(
                "Ez a csatorna nem egy aktív meccs jegye.", ephemeral=True
            )
            return

        await interaction.response.send_modal(ResultModal(match_id=match["id"]))


class ResultModal(ui.Modal, title="Eredmény beírása"):
    winner_input = ui.TextInput(
        label="Győztes Discord ID vagy @mention",
        required=True, max_length=50,
    )
    score_input = ui.TextInput(
        label="Eredmény (opcionális)",
        required=False, max_length=20,
    )

    def __init__(self, match_id: str) -> None:
        super().__init__()
        self.match_id = match_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = str(self.winner_input.value).strip()
        score = str(self.score_input.value).strip() if self.score_input.value else ""

        m = _MENTION_RE.match(raw)
        winner_id_str = m.group(1) if m else raw
        try:
            winner_id = int(winner_id_str)
        except ValueError:
            await interaction.response.send_message(
                "Érvénytelen győztes azonosító. Add meg a Discord ID-t vagy @mention-t.",
                ephemeral=True,
            )
            return

        try:
            await arun(db.set_match_winner, self.match_id, winner_id)
        except Exception as exc:
            log.exception("set_match_winner failed (%s)", type(exc).__name__)
            await interaction.response.send_message(
                f"❌ Hiba a győztes rögzítésekor: `{exc}`", ephemeral=True
            )
            return

        try:
            match_row = await arun(db.get_match_by_ticket, interaction.channel_id)
        except Exception as exc:
            log.exception("get_match_by_ticket failed (%s)", type(exc).__name__)
            await interaction.response.send_message(
                "Eredmény rögzítve, de a meccs már nem található.",
                ephemeral=True,
            )
            return

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

        # Per-tournament results channel (fallback to global config).
        results_channel_id = config.results_channel_id
        tournament_id = match_row.get("tournament_id")
        if tournament_id:
            try:
                tournament = await arun(db.get_tournament, tournament_id)
                if tournament and tournament.get("results_channel_id"):
                    results_channel_id = int(tournament["results_channel_id"])
            except Exception as exc:
                log.warning("ResultModal: get_tournament failed: %s", exc)

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


# ======================================================================
# Queue message discovery (cache-first, parallel fallback)
# ======================================================================

async def find_queue_message(
    guild: discord.Guild, queue_message_id: int
) -> Optional[discord.Message]:
    if queue_message_id <= 0:
        return None

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

    channels = list(guild.text_channels)
    if not channels:
        return None

    found: list[tuple[int, discord.Message]] = []
    found_lock = asyncio.Lock()

    async def _probe(channel: discord.TextChannel) -> None:
        try:
            msg = await channel.fetch_message(queue_message_id)
        except (discord.NotFound, discord.HTTPException):
            return
        async with found_lock:
            found.append((channel.id, msg))

    await asyncio.gather(*[_probe(c) for c in channels], return_exceptions=True)

    if found:
        channel_id, message = found[0]
        _queue_channel_cache[queue_message_id] = channel_id
        return message
    return None


# ======================================================================
# TournamentCog (formerly tournaments.py)
# ======================================================================

class TournamentCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.auto_start_loop.start()

    async def cog_unload(self) -> None:  # type: ignore[override]
        self.auto_start_loop.cancel()

    # ----- /tournamentqueue -----

    @app_commands.command(
        name="tournamentqueue",
        description="Létrehoz egy Tournament queue-t csatlakozási/kilépési gombokkal.",
    )
    @app_commands.describe(
        name="A Tournament neve.",
        timestamp="Indulási idő (ISO-8601 vagy YYYY-MM-DD HH:MM).",
        regulator_role="A rang, amely a Jegy lezárása / Eredmény gombokat használhatja.",
        results_channel="Csatorna, ahová a meccs eredmények kerülnek.",
        ticket_category="Kategória, amely alá a jegy csatornák kerülnek (opcionális).",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def tournamentqueue(
        self,
        interaction: discord.Interaction,
        name: str,
        timestamp: str,
        regulator_role: discord.Role,
        results_channel: discord.TextChannel,
        ticket_category: Optional[discord.CategoryChannel] = None,
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
                "Ezt a parancsot szöveges csatornában használd.", ephemeral=True
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

        try:
            await arun(
                db.create_tournament,
                name=name,
                end_time=end_time,
                queue_message_id=0,
                guild_id=guild.id,
                tournament_id=tournament_id,
                regulator_role_id=regulator_role.id,
                results_channel_id=results_channel.id,
                ticket_category_id=ticket_category.id if ticket_category else 0,
            )
        except Exception as exc:
            log.exception("create_tournament failed (%s)", type(exc).__name__)
            await interaction.followup.send(
                f"❌ Hiba a Tournament létrehozásakor.\n"
                f"**Típus:** `{type(exc).__name__}`\n**Üzenet:** `{exc}`\n"
                f"**Tournament ID:** `{tournament_id}`",
                ephemeral=True,
            )
            return

        # Verify the row landed.
        try:
            verify = await arun(db.get_tournament, tournament_id)
        except Exception:
            verify = None
        if verify is None:
            await interaction.followup.send(
                f"❌ A Tournament sort beszúrtuk, de nem tudjuk visszolvasni.\n"
                f"**Tournament ID:** `{tournament_id}`\n"
                f"Futtasd a `/dbtest` parancsot.",
                ephemeral=True,
            )
            return

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
                f"Hiba az üzenet elküldésekor: {exc}", ephemeral=True
            )
            return

        await arun(db.update_tournament, tournament_id, queue_message_id=sent.id)
        _queue_channel_cache[sent.id] = channel.id

        log.info(
            "Tournament created: id=%s name=%r guild=%s message=%s",
            tournament_id, name, guild.id, sent.id,
        )
        await interaction.followup.send(
            f"✅ Tournament létrehozva. ID: `{tournament_id}`", ephemeral=True
        )

    # ----- /tournamentround -----

    @app_commands.command(
        name="tournamentround",
        description="Kör indítása vagy leállítása egy meglévő Tournament-re.",
    )
    @app_commands.describe(
        action="start vagy stop",
        tournament_id="A Tournament UUID-ja.",
        round_number="A kör száma.",
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
                f"Nem található Tournament: `{tournament_id}`", ephemeral=True
            )
            return

        if action.value == "start":
            await interaction.response.defer(ephemeral=True, thinking=True)
            await self._start_round(tournament, round_number)
            await interaction.followup.send(
                f"✅ Kör {round_number} elindítva a Tournamenten `{tournament_id}`.",
                ephemeral=True,
            )
        else:
            await arun(
                db.update_tournament, tournament_id,
                status="stopped", current_round=round_number,
            )
            await interaction.response.send_message(
                f"🛑 Tournament `{tournament_id}` leállítva a(z) {round_number}. körnél.",
                ephemeral=True,
            )

    # ----- _start_round — the heavy lifter -----

    async def _start_round(self, tournament: dict, round_number: int) -> None:
        tournament_id = tournament["id"]

        lock = _get_round_lock(tournament_id)
        if lock.locked():
            log.info("tournament %s round %d already in progress, skipping",
                     tournament_id, round_number)
            return
        async with lock:
            try:
                claimed = await arun(db.claim_for_round, tournament_id, round_number)
            except Exception as exc:
                log.exception("claim_for_round failed (%s)", type(exc).__name__)
                return
            if not claimed:
                log.info("tournament %s not claimed for round %d",
                         tournament_id, round_number)
                return

            guild_id = int(tournament.get("guild_id") or 0)
            guild = self.bot.get_guild(guild_id) if guild_id else None
            if guild is None and config.guild_id:
                guild = self.bot.get_guild(config.guild_id)
            if guild is None and self.bot.guilds:
                guild = self.bot.guilds[0]
            if guild is None:
                log.error("tournament %s: no guild resolvable", tournament_id)
                return

            try:
                players = await arun(db.get_tournament_players, tournament_id)
            except Exception as exc:
                log.exception("get_tournament_players failed (%s)", type(exc).__name__)
                return
            if len(players) < 2:
                log.warning("tournament %s: only %d players, skipping round %d",
                            tournament_id, len(players), round_number)
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
                    name="Meccsek", value=matches_text or "—", inline=False
                )
                try:
                    await queue_message.edit(embed=round_embed, view=None)
                except discord.HTTPException as exc:
                    log.warning("queue message edit failed: %s", exc)

            # 6. Per-tournament config (fall back to global).
            t_cat_id = int(tournament.get("ticket_category_id") or 0) or config.ticket_category_id
            t_reg_id = int(tournament.get("regulator_role_id") or 0) or config.regulator_role_id

            category = (
                guild.get_channel(t_cat_id) if t_cat_id else None
            )
            if t_cat_id and not isinstance(category, discord.CategoryChannel):
                log.warning("ticket_category_id %s not a category", t_cat_id)
                category = None

            bot_member = guild.me
            created = 0
            for idx, (p1, p2) in enumerate(pairs, start=1):
                p1_id = int(p1["discord_id"])
                p1_mc = p1.get("minecraft_name", "?")

                if p2 is None:
                    # Bye — record with player2=0, no ticket.
                    try:
                        await arun(
                            db.create_match, tournament_id, round_number,
                            p1_id, 0, p1_mc, "", 0,
                        )
                    except Exception as exc:
                        log.error("create_match (bye) failed: %s", exc)
                    continue

                p2_id = int(p2["discord_id"])
                p2_mc = p2.get("minecraft_name", "?")

                channel_name = safe_channel_name(
                    f"{name}-r{round_number}-m{idx}-{p1_mc}-vs-{p2_mc}"
                )

                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(view_channel=False),
                    bot_member: discord.PermissionOverwrite(
                        view_channel=True, send_messages=True,
                        read_message_history=True, manage_messages=True,
                        embed_links=True,
                    ),
                }
                member1 = guild.get_member(p1_id)
                if member1 is not None:
                    overwrites[member1] = discord.PermissionOverwrite(
                        view_channel=True, send_messages=True,
                        read_message_history=True,
                    )
                member2 = guild.get_member(p2_id)
                if member2 is not None:
                    overwrites[member2] = discord.PermissionOverwrite(
                        view_channel=True, send_messages=True,
                        read_message_history=True,
                    )
                if t_reg_id:
                    reg_role = guild.get_role(t_reg_id)
                    if reg_role is not None:
                        overwrites[reg_role] = discord.PermissionOverwrite(
                            view_channel=True, send_messages=True,
                            manage_messages=True, read_message_history=True,
                        )

                # Create channel.
                try:
                    ticket_channel = await guild.create_text_channel(
                        channel_name,
                        category=category if isinstance(category, discord.CategoryChannel) else None,
                        overwrites=overwrites,
                    )
                except discord.HTTPException as exc:
                    log.error("ticket channel create failed (match %d %s vs %s): %s",
                              idx, p1_mc, p2_mc, exc)
                    continue

                # Send the embed FIRST so players always see something,
                # even if the DB match row creation fails. We attach a
                # placeholder view-less embed, then create the DB row,
                # then send a second message with the actual buttons.
                ticket_embed = discord.Embed(
                    title=f"{name} Tournament",
                    color=0x00E5FF,
                )
                ticket_embed.description = (
                    f"**Párosítás:**\n<@{p1_id}> ({p1_mc}) vs <@{p2_id}> ({p2_mc})\n\n"
                    f"**In-game nevek:**\n`{p1_mc}` vs `{p2_mc}`\n\n"
                    f"Regulator: az eredményt az **Eredmény beírása** gombbal rögzítsd."
                )

                # Create the DB match row first (we need the match_id for the view).
                match_row = None
                try:
                    match_row = await arun(
                        db.create_match, tournament_id, round_number,
                        p1_id, p2_id, p1_mc, p2_mc, ticket_channel.id,
                    )
                except Exception as exc:
                    log.error("create_match failed (match %d %s vs %s, channel=%s): %s",
                              idx, p1_mc, p2_mc, ticket_channel.id, exc)
                    # Send the embed WITHOUT buttons so the players still
                    # have a channel — but warn that result recording won't work.
                    error_embed = discord.Embed(
                        title=f"{name} Tournament",
                        color=0xFFA500,
                        description=(
                            f"**Párosítás:**\n<@{p1_id}> ({p1_mc}) vs <@{p2_id}> ({p2_mc})\n\n"
                            f"**In-game nevek:**\n`{p1_mc}` vs `{p2_mc}`\n\n"
                            f"⚠️ A meccs rögzítése sikertelen (DB hiba). "
                            f"A jegy csatorna használható, de az eredmény gomb nem működik."
                        ),
                    )
                    try:
                        await ticket_channel.send(
                            content=f"<@{p1_id}> <@{p2_id}>",
                            embed=error_embed,
                        )
                        log.info("sent error embed to ticket channel %s", ticket_channel.id)
                    except discord.HTTPException as send_exc:
                        log.error("even the error embed failed: %s", send_exc)
                    continue

                # Match row exists — send embed WITH the action buttons.
                match_id = match_row["id"]
                ticket_view = TicketActionView(match_id)
                sent_ok = False
                for attempt in range(2):  # one retry
                    try:
                        await ticket_channel.send(
                            content=f"<@{p1_id}> <@{p2_id}>",
                            embed=ticket_embed,
                            view=ticket_view,
                        )
                        sent_ok = True
                        break
                    except discord.HTTPException as exc:
                        log.warning("ticket embed send attempt %d failed (channel=%s): %s",
                                    attempt + 1, ticket_channel.id, exc)
                        if attempt == 0:
                            await asyncio.sleep(1.0)  # brief retry back-off

                if sent_ok:
                    log.info("ticket embed sent to channel %s (match %s, %s vs %s)",
                             ticket_channel.id, match_id, p1_mc, p2_mc)
                    created += 1
                else:
                    log.error("ticket embed send FAILED after retry (channel=%s, match=%s)",
                              ticket_channel.id, match_id)

                # Small delay to avoid Discord rate-limiting on rapid
                # channel+message creation bursts.
                await asyncio.sleep(0.5)

            log.info("tournament %s round %d started: %d/%d matches with embeds",
                     tournament_id, round_number, created, len(pairs))

    # ----- auto-start loop -----

    @tasks.loop(seconds=config.auto_start_poll_seconds)
    async def auto_start_loop(self) -> None:
        try:
            pending = await arun(db.list_pending_tournaments)
        except Exception as exc:
            log.error("auto_start_loop: list_pending_tournaments failed: %s", exc)
            return
        for tournament in pending:
            try:
                next_round = int(tournament.get("current_round") or 0) + 1
                await self._start_round(tournament, next_round)
            except Exception as exc:
                log.error("auto_start_loop: _start_round failed for %s: %s",
                          tournament.get("id"), exc)

    @auto_start_loop.before_loop
    async def _before_auto_start_loop(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TournamentCog(bot))


# ======================================================================
# Bot class + setup_hook (formerly main.py)
# ======================================================================

intents = discord.Intents.default()
intents.members = True
intents.message_content = False
intents.guilds = True


class NeonTiersBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned_or("!"),
            intents=intents,
        )

    async def setup_hook(self) -> None:  # type: ignore[override]
        await self.add_cog(TournamentCog(self))

        # Rehydrate persistent views for active tournaments + unresolved matches.
        try:
            active = await arun(db.list_active_tournaments)
            for tournament in active:
                tid = tournament["id"]
                self.add_view(QueueJoinView(tid))
                unresolved = await arun(db.get_unresolved_matches, tid)
                for match in unresolved:
                    self.add_view(TicketActionView(match_id=match["id"]))
            log.info("rehydrated %d queue views + unresolved ticket views", len(active))
        except Exception as exc:
            log.error("view rehydration failed: %s", exc)

        await self._sync_guild_commands()

    async def _sync_guild_commands(self) -> bool:
        global _synced
        if not config.guild_id:
            # Global sync — works for any guild the bot is in.
            try:
                synced = await self.tree.sync()
                _synced = True
                log.info("synced %d commands globally (no GUILD_ID set)", len(synced))
                return True
            except Exception as exc:
                log.warning("global slash sync failed: %s", exc)
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

@bot.tree.command(name="dbtest", description="Diagnosztika: Supabase kapcsolat + CRUD teszt (admin).")
@app_commands.default_permissions(administrator=True)
async def dbtest_cmd(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)

    test_id = str(uuid.uuid4())
    lines = [
        "**🔧 Supabase diagnosztika**",
        f"• URL: `{config.supabase_url}`",
        f"• Guild ID: `{config.guild_id}`",
        "",
    ]

    try:
        await arun(
            db.create_tournament,
            name="dbtest-please-delete",
            end_time=utcnow(),
            queue_message_id=0,
            guild_id=interaction.guild.id if interaction.guild else 0,
            tournament_id=test_id,
            regulator_role_id=0,
            results_channel_id=0,
            ticket_category_id=0,
        )
        lines.append(f"✅ **INSERT** sikeres (id=`{test_id}`)")
    except Exception as exc:
        lines.append(f"❌ **INSERT** sikertelen: `{type(exc).__name__}: {exc}`")
        lines.append("\n**Diagnosztika:** ez általában RLS policy vagy schema hiba.")
        await interaction.followup.send("\n".join(lines), ephemeral=True)
        return

    try:
        got = await arun(db.get_tournament, test_id)
        if got is None:
            lines.append("❌ **SELECT** visszatért `None`-al — RLS blokkolja a SELECT-et.")
        else:
            lines.append(f"✅ **SELECT** sikeres (name=`{got.get('name')}`)")
    except Exception as exc:
        lines.append(f"❌ **SELECT** hiba: `{type(exc).__name__}: {exc}`")

    try:
        await arun(db.update_tournament, test_id, queue_message_id=999999)
        lines.append("✅ **UPDATE** sikeres")
    except Exception as exc:
        lines.append(f"❌ **UPDATE** hiba: `{type(exc).__name__}: {exc}`")

    # Test matches table BEFORE the DELETE step (FK requires the parent
    # tournament row to still exist).
    try:
        m = await arun(
            db.create_match, test_id, 0, 1, 2, "A", "B", 0
        )
        await arun(db.set_match_winner, m["id"], 1)
        lines.append("✅ **matches** INSERT + UPDATE sikeres")
    except Exception as exc:
        lines.append(f"❌ **matches** hiba: `{type(exc).__name__}: {exc}`")
        lines.append("   → A `matches` tábla hiányzik vagy rossz a schema? Futtasd le a SQL setup-ot!")

    try:
        active = await arun(db.list_active_tournaments)
        lines.append(f"✅ **list_active_tournaments**: {len(active)} aktív")
    except Exception as exc:
        lines.append(f"❌ **list_active_tournaments** hiba: `{type(exc).__name__}: {exc}`")

    # DELETE last — also tests the ON DELETE CASCADE on matches.
    try:
        await arun(db.delete_tournament, test_id)
        lines.append("✅ **DELETE** sikeres (cascade: match is törölve)")
    except Exception as exc:
        lines.append(f"❌ **DELETE** hiba: `{type(exc).__name__}: {exc}`")

    lines.append("\n**Összegzés:** ha bármelyik lépés ❌, a Supabase nincs rendben.")
    await interaction.followup.send("\n".join(lines), ephemeral=True)


@bot.tree.command(name="sync", description="Azonnali guild slash sync (admin).")
@app_commands.default_permissions(administrator=True)
async def sync_cmd(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    if interaction.guild is None:
        await interaction.followup.send("Szerveren belül használd.", ephemeral=True)
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


# ----------------------------------------------------------------------
# on_ready
# ----------------------------------------------------------------------

@bot.event
async def on_ready() -> None:
    global _synced
    log.info(
        "logged in as %s (id=%s) — %d guild(s)",
        bot.user, bot.user.id if bot.user else None, len(bot.guilds),
    )
    if bot.guilds:
        g = bot.guilds[0]
        log.info("active guild: %s (id=%s)", g.name, g.id)
    if not _synced:
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
