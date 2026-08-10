"""NeonTiers Tournament Bot - Közös embed builder függvények.

Ezeket a queue (bajnokság jelentkezési) embed és a meccs ticket embed
egyaránt használja, hogy induláskor (rehydration) és futás közben
(frissítés) is ugyanaz a formátum jöjjön létre.
"""

from __future__ import annotations

import discord

PAGE_SIZE = 10

_STATUS_LABELS = {
    "queued": "Nyitva",
    "running": "Folyamatban",
    "completed": "Vége",
    "cancelled": "Vége",
}


def status_label(status: str) -> str:
    return _STATUS_LABELS.get(status, status or "Ismeretlen")


def build_queue_embed(tourney: dict, players: list[dict], page: int = 0) -> tuple[discord.Embed, int]:
    """Felépíti a bajnokság jelentkezési (queue) embedet.

    Visszatér az embeddel és a ténylegesen érvényes oldalszámmal (clamped).
    """
    name = tourney.get("name", "Bajnokság")
    end_ts = int(tourney.get("end_time") or 0)
    status = tourney.get("status", "queued")
    ft = tourney.get("ft") or 1
    posted_at = int(tourney.get("posted_at") or 0)
    current_round = int(tourney.get("current_round") or 0)

    total = len(players)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    lines = [
        f"**Belépés vége:** <t:{end_ts}:R>",
        f"**Pontos idő:** <t:{end_ts}:F>",
        f"**Állapot:** {status_label(status)}",
        f"**FT:** Bo{ft}",
    ]
    if status == "running" and current_round > 0:
        lines.append(f"**Jelenlegi forduló:** {current_round}.")
    if posted_at:
        lines.append(f"\n*Létrehozva: <t:{posted_at}:R>*")

    description = "\n".join(lines)

    embed = discord.Embed(
        title=f"{name} - Tournament Queue",
        description=description,
        color=discord.Color.blue(),
    )

    start = page * PAGE_SIZE
    page_players = players[start:start + PAGE_SIZE]

    if page_players:
        entry_lines = []
        for idx, p in enumerate(page_players, start=start + 1):
            discord_id = p.get("discord_id")
            mc_name = p.get("minecraft_name") or "Ismeretlen"
            entry_lines.append(f"{idx}. <@{discord_id}> - `{mc_name}`")
        field_value = "\n".join(entry_lines)
    else:
        field_value = "*Még senki sem jelentkezett.*"

    embed.add_field(
        name=f"Jelentkezők ({total})",
        value=field_value[:1024],
        inline=False,
    )

    if total_pages > 1:
        embed.set_footer(text=f"Oldal {page + 1}/{total_pages}")

    return embed, page


def build_ticket_embed(tourney: dict, match: dict) -> discord.Embed:
    """Felépíti a meccs ticket embedet (Párosítás / Határidő / Állapot)."""
    tourney_name = tourney.get("name", "Bajnokság")
    round_num = int(match.get("round_number") or 1)

    p1_id = match.get("player1_discord_id")
    p2_id = match.get("player2_discord_id")
    p1_mc = match.get("player1_mc") or "?"
    p2_mc = match.get("player2_mc") or "?"

    deadline = int(match.get("deadline") or 0)
    is_open = not match.get("winner_discord_id") and match.get("winner_discord_id") != 0
    # winner_discord_id can legitimately be 0 (double-FF); anything else falsy = open
    if match.get("winner_discord_id") in (None,):
        state_label = "Nyitva"
    else:
        state_label = "Lezárt"

    embed = discord.Embed(
        title=f"{tourney_name} Tournament - {round_num}. Forduló",
        description="A Regulator használhatja az Eredmény, FF, Lezárás gombokat.",
        color=discord.Color.gold() if state_label == "Nyitva" else discord.Color.dark_grey(),
    )
    embed.add_field(
        name="Párosítás",
        value=f"<@{p1_id}> `{p1_mc}` vs <@{p2_id}> `{p2_mc}`",
        inline=False,
    )
    if deadline:
        embed.add_field(name="Határidő", value=f"<t:{deadline}:F>", inline=False)
    embed.add_field(name="Állapot", value=state_label, inline=False)

    return embed
