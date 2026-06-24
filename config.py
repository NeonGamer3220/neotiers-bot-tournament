"""Centralised environment-variable loading for NeonTiers Tournament bot."""
from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # dotenv is optional in production (Railway injects env)
    pass


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            "Set it in your Railway service variables."
        )
    return value


def _optional_int(name: str, default: int | None = None) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(f"Environment variable {name} must be an integer, got: {raw}")


@dataclass(frozen=True)
class Config:
    # --- Supabase ---
    supabase_url: str
    supabase_anon_key: str

    # --- Discord ---
    discord_token: str
    client_id: int
    guild_id: int

    # --- Roles / Channels ---
    regulator_role_id: int
    ticket_category_id: int
    results_channel_id: int

    # --- Tunables ---
    pending_code_ttl_minutes: int  # how long a linking code stays valid
    pending_code_length: int       # length of the generated secret code
    auto_start_poll_seconds: int   # background loop interval

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            supabase_url=_required("SUPABASE_URL"),
            supabase_anon_key=_required("SUPABASE_ANON_KEY"),
            discord_token=_required("DISCORD_TOKEN"),
            client_id=_optional_int("CLIENT_ID") or 0,
            guild_id=_optional_int("GUILD_ID") or 0,
            regulator_role_id=_optional_int("REGULATOR_ROLE_ID", 1483822408182796418),
            ticket_category_id=_optional_int("TICKET_CATEGORY_ID", 0),
            results_channel_id=_optional_int("RESULTS_CHANNEL_ID", 0),
            pending_code_ttl_minutes=_optional_int("PENDING_CODE_TTL_MINUTES", 30),
            pending_code_length=_optional_int("PENDING_CODE_LENGTH", 6),
            auto_start_poll_seconds=_optional_int("AUTO_START_POLL_SECONDS", 15),
        )


# Eager singleton — fails fast if env is misconfigured.
config = Config.from_env()
