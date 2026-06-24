-- NeonTiers Tournament Discord Bot — Supabase setup script
-- Run this ONCE in the Supabase SQL Editor.
--
-- The linked_accounts, pending_codes, and tournaments tables are assumed to
-- already exist (they are pre-existing). This script only creates the
-- bot-owned `matches` table and applies the required RLS policies.

-- ----------------------------------------------------------------------
-- 1) Bot-created table: matches
-- ----------------------------------------------------------------------

create table if not exists public.matches (
    id uuid primary key default gen_random_uuid(),
    tournament_id uuid not null references public.tournaments(id) on delete cascade,
    round_number int not null,
    player1_discord_id bigint not null,
    player2_discord_id bigint not null,
    player1_mc text not null,
    player2_mc text not null,
    ticket_channel_id bigint not null default 0,
    winner_discord_id bigint,
    created_at timestamptz default now()
);

create index if not exists matches_tournament_id_idx
    on public.matches(tournament_id);

create index if not exists matches_ticket_channel_id_idx
    on public.matches(ticket_channel_id);

create index if not exists matches_winner_idx
    on public.matches(winner_discord_id)
    where winner_discord_id is null;

-- ----------------------------------------------------------------------
-- 2) RLS policies (allow anon full access — bot uses the anon key)
-- ----------------------------------------------------------------------

alter table public.tournaments enable row level security;
alter table public.pending_codes enable row level security;
alter table public.linked_accounts enable row level security;
alter table public.matches enable row level security;

-- Tournaments
drop policy if exists "anon full access tournaments" on public.tournaments;
create policy "anon full access tournaments"
    on public.tournaments for all to anon
    using (true) with check (true);

-- Pending codes
drop policy if exists "anon full access pending_codes" on public.pending_codes;
create policy "anon full access pending_codes"
    on public.pending_codes for all to anon
    using (true) with check (true);

-- Linked accounts
drop policy if exists "anon full access linked_accounts" on public.linked_accounts;
create policy "anon full access linked_accounts"
    on public.linked_accounts for all to anon
    using (true) with check (true);

-- Matches
drop policy if exists "anon full access matches" on public.matches;
create policy "anon full access matches"
    on public.matches for all to anon
    using (true) with check (true);
