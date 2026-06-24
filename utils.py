"""Small pure-Python helpers used across the bot."""
from __future__ import annotations

import secrets
import string
from datetime import datetime, timezone
from typing import Iterable

from config import config


def generate_code(length: int | None = None) -> str:
    """Generate a human-friendly alphanumeric secret code.

    Ambiguous characters (0/O, 1/I/l) are stripped to avoid copy/paste mistakes.
    """
    length = length or config.pending_code_length
    alphabet = "".join(
        c for c in (string.ascii_uppercase + string.digits)
        if c not in {"0", "O", "1", "I", "L"}
    )
    return "".join(secrets.choice(alphabet) for _ in range(length))


def parse_timestamp(raw: str) -> datetime:
    """Parse a user-supplied timestamp into an aware UTC datetime.

    Accepted forms:
      - ISO 8601 with offset: 2026-06-25T18:00:00+02:00
      - ISO 8601 with Z:      2026-06-25T18:00:00Z
      - Naive ISO (assumed UTC): 2026-06-25T18:00:00
      - "YYYY-MM-DD HH:MM"    (assumed UTC)
    """
    raw = raw.strip().replace("T", " ")
    if raw.endswith("Z"):
        raw = raw[:-1]
    # Try with offset
    for fmt in ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    raise ValueError(
        "Invalid timestamp. Use ISO 8601 like `2026-06-25T18:00:00+02:00` "
        "or `2026-06-25 18:00`."
    )


def format_discord_timestamp(dt: datetime) -> str:
    """Return Discord's <t:...:F> absolute timestamp tag."""
    return f"<t:{int(dt.timestamp())}:F>"


def format_relative_timestamp(dt: datetime) -> str:
    """Return Discord's <t:...:R> relative timestamp tag (e.g. 'in 2 hours')."""
    return f"<t:{int(dt.timestamp())}:R>"


def pair_players(players: list[dict]) -> list[tuple[dict, dict | None]]:
    """Pair players for a round.

    If the player count is odd, the last player gets a bye (paired with None).
    Returns a list of (player1, player2_or_None) tuples.
    """
    pairs: list[tuple[dict, dict | None]] = []
    for i in range(0, len(players), 2):
        p1 = players[i]
        p2 = players[i + 1] if i + 1 < len(players) else None
        pairs.append((p1, p2))
    return pairs


def format_match_line(p1: dict, p2: dict | None) -> str:
    """Format a single match line for the round embed.

    Example: '@NeonGamer322 | CLUB&CAT ONLY vs @Keszegg | Alex'
    """
    p1_text = f"<@{p1['discord_id']}> | {p1['minecraft_name']}"
    if p2 is None:
        return f"{p1_text} — *bye*"
    p2_text = f"<@{p2['discord_id']}> | {p2['minecraft_name']}"
    return f"{p1_text} vs {p2_text}"


def safe_channel_name(name: str, max_len: int = 90) -> str:
    """Make a Discord-safe channel name (lowercase, no spaces, no special chars)."""
    cleaned = "".join(c.lower() if c.isalnum() or c == "-" else "-" for c in name)
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return (cleaned or "ticket")[:max_len]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
