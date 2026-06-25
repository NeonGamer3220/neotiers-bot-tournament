"""Pure-Python helper utilities for NeonTiers.

No Discord / Supabase imports here — these functions are easily unit-testable
and safe to import from any module.
"""

from __future__ import annotations

import re
import secrets
import string
from datetime import datetime, timezone
from typing import Optional

from config import config

# Ambiguous characters removed from generated codes: 0/O and 1/I/L.
_CODE_ALPHABET = "".join(
    c for c in (string.ascii_uppercase + string.digits)
    if c not in {"0", "O", "1", "I", "L"}
)


def generate_code(length: Optional[int] = None) -> str:
    """Return a cryptographically-random alphanumeric code.

    Ambiguous characters (``0``, ``O``, ``1``, ``I``, ``L``) are excluded so
    codes are unambiguous when read by humans in chat.
    """
    n = length if length is not None else config.pending_code_length
    if n <= 0:
        raise ValueError("Code length must be positive.")
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(n))


# Accept ISO-8601 (with/without offset, with/without trailing ``Z``, with
# fractional seconds) AND the friendlier ``YYYY-MM-DD HH:MM`` form.
_TS_PATTERNS = (
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
)


def parse_timestamp(raw) -> datetime:
    """Parse a user-supplied timestamp string OR a Unix epoch int/float.

    Accepted forms:
      - int/float (Unix epoch seconds) — used when reading back from a
        ``bigint`` column.
      - ISO-8601 (``2026-06-25T18:00:00+02:00``, ``2026-06-25T18:00:00Z``,
        ``2026-06-25T18:00:00.123456``)
      - ``YYYY-MM-DD HH:MM`` shorthand.

    Naive datetimes are assumed to be UTC.

    Raises :class:`ValueError` on any unparseable input.
    """
    # Unix epoch (int or float) — handles bigint columns.
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return datetime.fromtimestamp(raw, tz=timezone.utc)

    if not raw or not str(raw).strip():
        raise ValueError("Empty timestamp.")

    text = str(raw).strip()

    # Normalise trailing Z → +00:00 so %z matches.
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"

    last_err: Optional[ValueError] = None
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
        f"Could not parse timestamp {raw!r}. "
        "Expected ISO-8601 (e.g. 2026-06-25T18:00:00+02:00) or YYYY-MM-DD HH:MM."
    ) from last_err


def format_discord_timestamp(dt: datetime) -> str:
    """Return ``<t:UNIX:F>`` (full date-time) for an aware datetime."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return f"<t:{int(dt.timestamp())}:F>"


def format_relative_timestamp(dt: datetime) -> str:
    """Return ``<t:UNIX:R>`` (relative) for an aware datetime."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return f"<t:{int(dt.timestamp())}:R>"


def pair_players(players: list[dict]) -> list[tuple[dict, Optional[dict]]]:
    """Pair adjacent players; if odd, the last player gets a bye (``None``)."""
    pairs: list[tuple[dict, Optional[dict]]] = []
    i = 0
    while i < len(players):
        p1 = players[i]
        p2 = players[i + 1] if i + 1 < len(players) else None
        pairs.append((p1, p2))
        i += 2
    return pairs


def format_match_line(p1: dict, p2: Optional[dict]) -> str:
    """Format a pair as a one-line match string for embeds.

    ``<@id1> | mc1 vs <@id2> | mc2`` — or with a bye: ``<@id1> | mc1 — *bye*``.
    """
    mc1 = p1.get("minecraft_name", "?")
    if p2 is None:
        return f"<@{int(p1['discord_id'])}> | {mc1} — *bye*"
    mc2 = p2.get("minecraft_name", "?")
    return (
        f"<@{int(p1['discord_id'])}> | {mc1} vs "
        f"<@{int(p2['discord_id'])}> | {mc2}"
    )


# Collapse runs of non-alphanumeric characters into a single hyphen,
# lowercase, strip leading/trailing hyphens, and cap length.
_SAFE_NAME_RE_1 = re.compile(r"[^a-z0-9]+")
_SAFE_NAME_RE_2 = re.compile(r"-{2,}")


def safe_channel_name(name: str, max_len: int = 90) -> str:
    """Return a Discord-safe channel name (lowercase, alnum + hyphens only)."""
    if not name:
        return "channel"
    cleaned = _SAFE_NAME_RE_1.sub("-", name.lower())
    cleaned = _SAFE_NAME_RE_2.sub("-", cleaned)
    cleaned = cleaned.strip("-")
    if not cleaned:
        cleaned = "channel"
    return cleaned[:max_len]


def utcnow() -> datetime:
    """Return ``datetime.now(timezone.utc)``."""
    return datetime.now(timezone.utc)
