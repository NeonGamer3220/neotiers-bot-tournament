-- ============================================================ --
-- NeonTiers Tournament Bot — Supabase schema
-- ============================================================ --
-- The following tables are PRE-EXISTING in your Supabase project
-- (provided by you, do not need to be created by the bot). They are
-- documented here for reference only.
--
--   linked_accounts (
--     id              uuid primary key default gen_random_uuid(),
--     discord_id      bigint unique not null,
--     minecraft_name  text not null,
--     created_at      timestamptz not null default now()
--   );
--
--   pending_codes (
--     id          uuid primary key default gen_random_uuid(),
--     discord_id  bigint not null,
--     code        text unique not null,
--     created_at  timestamptz not null default now(),
--     expires_at  timestamptz not null,
--     used        boolean not null default false
--   );
--
--   tournaments (
--     id                 uuid primary key default gen_random_uuid(),
--     name               text not null,
--     end_time           timestamptz not null,
--     queue_message_id   bigint not null,
--     status             text not null default 'queued',
--     guild_id           bigint not null,
--     current_round      int not null default 0,
--     players            jsonb not null default '[]'::jsonb
--     -- players JSONB schema:
--     --   [{"discord_id": 649276313395396600, "minecraft_name": "KevinREAL"}, ...]
--   );
--
--   -- Recommended indexes for the existing tournaments table:
--   -- create index if not exists tournaments_status_end_time_idx
--   --     on public.tournaments (status, end_time);
--   -- create index if not exists tournaments_queue_message_idx
--   --     on public.tournaments (queue_message_id);
--
-- ============================================================ --
-- The matches table is created by the bot. It tracks each pairing of
-- every round, the ticket channel id, and the recorded winner.
-- ============================================================ --

create table if not exists public.matches (
    id                    uuid primary key default gen_random_uuid(),
    tournament_id         uuid not null references public.tournaments(id) on delete cascade,
    round_number          int not null,
    player1_discord_id    bigint not null,
    player2_discord_id    bigint not null,
    player1_mc            text not null,
    player2_mc            text not null,
    ticket_channel_id     bigint not null default 0,
    winner_discord_id     bigint,
    created_at            timestamptz not null default now()
);

create index if not exists matches_tournament_round_idx
    on public.matches (tournament_id, round_number);

create index if not exists matches_ticket_idx
    on public.matches (ticket_channel_id);

-- ============================================================ --
-- Optional: helpful indexes on the existing tournaments table.
-- These are safe to run — CREATE INDEX IF NOT EXISTS is a no-op if
-- the index already exists. Uncomment to apply.
-- ============================================================ --
create index if not exists tournaments_status_end_time_idx
    on public.tournaments (status, end_time);

create index if not exists tournaments_queue_message_idx
    on public.tournaments (queue_message_id);
