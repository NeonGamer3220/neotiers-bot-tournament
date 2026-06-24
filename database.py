"""Supabase persistence layer for NeonTiers Tournament bot.

PRE-EXISTING tables (provided by user, do not modify):
  - linked_accounts(id uuid, discord_id bigint unique, minecraft_name text, created_at)
  - pending_codes(id uuid, discord_id bigint, code text unique,
                  created_at, expires_at, used boolean)
  - tournaments(id uuid, name text, end_time timestamptz, queue_message_id bigint,
                status text, guild_id bigint, current_round int default 0,
                players jsonb default '[]')
                -- players JSONB schema:
                --   [{"discord_id": 649276313395396600, "minecraft_name": "KevinREAL"}, ...]

Created by migration.sql if missing:
  - matches(id uuid, tournament_id uuid, round_number int,
            player1_discord_id bigint, player2_discord_id bigint,
            player1_mc text, player2_mc text,
            ticket_channel_id bigint, winner_discord_id bigint nullable,
            created_at timestamptz)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from supabase import create_client, Client

from config import config


class Database:
    """Thin wrapper around the Supabase REST client used by the bot."""

    def __init__(self) -> None:
        self.client: Client = create_client(
            config.supabase_url, config.supabase_anon_key
        )

    # ------------------------------------------------------------------ #
    # linked_accounts
    # ------------------------------------------------------------------ #
    def get_linked_account(self, discord_id: int) -> Optional[dict[str, Any]]:
        resp = (
            self.client.table("linked_accounts")
            .select("*")
            .eq("discord_id", discord_id)
            .maybe_single()
            .execute()
        )
        return resp.data if resp else None

    # ------------------------------------------------------------------ #
    # pending_codes
    # ------------------------------------------------------------------ #
    def create_pending_code(self, discord_id: int, code: str, ttl_minutes: int) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        expires = now + timedelta(minutes=ttl_minutes)
        payload = {
            "id": str(uuid.uuid4()),
            "discord_id": discord_id,
            "code": code,
            "created_at": now.isoformat(),
            "expires_at": expires.isoformat(),
            "used": False,
        }
        resp = self.client.table("pending_codes").insert(payload).execute()
        return resp.data[0] if resp.data else payload

    def is_pending_code_valid(self, code: str) -> bool:
        """True if a code exists, is unused, and not expired."""
        resp = (
            self.client.table("pending_codes")
            .select("id,used,expires_at")
            .eq("code", code)
            .maybe_single()
            .execute()
        )
        if not resp or not resp.data:
            return False
        if resp.data.get("used"):
            return False
        expires_at = resp.data.get("expires_at")
        if not expires_at:
            return False
        try:
            exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError:
            return False
        return exp > datetime.now(timezone.utc)

    # ------------------------------------------------------------------ #
    # tournaments
    # ------------------------------------------------------------------ #
    def create_tournament(
        self,
        name: str,
        end_time: datetime,
        queue_message_id: int,
        guild_id: int,
        tournament_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Insert a new tournament row.

        Matches the user-provided schema:
            id, name, end_time, queue_message_id, status, guild_id,
            current_round, players(jsonb)

        If `tournament_id` is provided, it is used as the row's primary key
        (lets callers pre-build a persistent Discord View with the same id
        BEFORE the row is inserted, so the view can be attached to the
        initial channel.send() call).
        """
        payload = {
            "id": tournament_id or str(uuid.uuid4()),
            "name": name,
            "end_time": end_time.isoformat(),
            "queue_message_id": queue_message_id,
            "status": "queued",
            "guild_id": guild_id,
            "current_round": 0,
            "players": [],
        }
        resp = self.client.table("tournaments").insert(payload).execute()
        return resp.data[0] if resp.data else payload

    def get_tournament(self, tournament_id: str) -> Optional[dict[str, Any]]:
        resp = (
            self.client.table("tournaments")
            .select("*")
            .eq("id", tournament_id)
            .maybe_single()
            .execute()
        )
        return resp.data if resp else None

    def list_pending_tournaments(self) -> list[dict[str, Any]]:
        """Tournaments still in 'queued' status whose end_time has passed.

        end_time = queue phase end = round 1 auto-start trigger.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        resp = (
            self.client.table("tournaments")
            .select("*")
            .eq("status", "queued")
            .lte("end_time", now_iso)
            .execute()
        )
        return resp.data if resp and resp.data else []

    def list_all_tournaments(self) -> list[dict[str, Any]]:
        resp = (
            self.client.table("tournaments")
            .select("*")
            .order("end_time", desc=True)
            .execute()
        )
        return resp.data if resp and resp.data else []

    def update_tournament(self, tournament_id: str, **fields: Any) -> None:
        self.client.table("tournaments").update(fields).eq("id", tournament_id).execute()

    # ------------------------------------------------------------------ #
    # tournaments.players (JSONB array)
    # ------------------------------------------------------------------ #
    def get_tournament_players(self, tournament_id: str) -> list[dict[str, Any]]:
        """Return the players JSONB array from the tournaments row.

        Each item: {"discord_id": int, "minecraft_name": str}
        """
        resp = (
            self.client.table("tournaments")
            .select("players")
            .eq("id", tournament_id)
            .maybe_single()
            .execute()
        )
        if not resp or not resp.data:
            return []
        players = resp.data.get("players") or []
        # Normalise discord_id to int (Supabase may return as int or str).
        normalised = []
        for p in players:
            try:
                did = int(p.get("discord_id", 0))
            except (TypeError, ValueError):
                continue
            normalised.append({
                "discord_id": did,
                "minecraft_name": p.get("minecraft_name", "ismeretlen"),
            })
        return normalised

    def add_player_to_tournament(
        self, tournament_id: str, discord_id: int, minecraft_name: str
    ) -> bool:
        """Append a player to the tournament's players JSONB array.

        Returns True if added, False if the player was already in the array.
        Note: this is a fetch-then-update, not atomic. For a Discord bot where
        joins are infrequent and per-user, this is acceptable.
        """
        players = self.get_tournament_players(tournament_id)
        for p in players:
            if p["discord_id"] == discord_id:
                return False
        players.append({
            "discord_id": discord_id,
            "minecraft_name": minecraft_name,
        })
        self.client.table("tournaments").update({"players": players}).eq(
            "id", tournament_id
        ).execute()
        return True

    def remove_player_from_tournament(self, tournament_id: str, discord_id: int) -> bool:
        """Remove a player from the tournament's players JSONB array.

        Returns True if a player was removed, False if they weren't on the roster.
        """
        players = self.get_tournament_players(tournament_id)
        new_players = [p for p in players if p["discord_id"] != discord_id]
        if len(new_players) == len(players):
            return False
        self.client.table("tournaments").update({"players": new_players}).eq(
            "id", tournament_id
        ).execute()
        return True

    # ------------------------------------------------------------------ #
    # matches
    # ------------------------------------------------------------------ #
    def create_match(
        self,
        tournament_id: str,
        round_number: int,
        player1_discord_id: int,
        player2_discord_id: int,
        player1_mc: str,
        player2_mc: str,
        ticket_channel_id: int,
    ) -> dict[str, Any]:
        payload = {
            "id": str(uuid.uuid4()),
            "tournament_id": tournament_id,
            "round_number": round_number,
            "player1_discord_id": player1_discord_id,
            "player2_discord_id": player2_discord_id,
            "player1_mc": player1_mc,
            "player2_mc": player2_mc,
            "ticket_channel_id": ticket_channel_id,
            "winner_discord_id": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        resp = self.client.table("matches").insert(payload).execute()
        return resp.data[0] if resp.data else payload

    def get_match_by_ticket(self, ticket_channel_id: int) -> Optional[dict[str, Any]]:
        resp = (
            self.client.table("matches")
            .select("*,tournaments(name)")
            .eq("ticket_channel_id", ticket_channel_id)
            .maybe_single()
            .execute()
        )
        return resp.data if resp else None

    def set_match_winner(self, match_id: str, winner_discord_id: int) -> None:
        self.client.table("matches").update({"winner_discord_id": winner_discord_id}).eq(
            "id", match_id
        ).execute()

    def get_matches(self, tournament_id: str, round_number: int | None = None) -> list[dict[str, Any]]:
        q = self.client.table("matches").select("*").eq("tournament_id", tournament_id)
        if round_number is not None:
            q = q.eq("round_number", round_number)
        q = q.order("created_at", desc=False)
        resp = q.execute()
        return resp.data if resp and resp.data else []


# Singleton
db = Database()
