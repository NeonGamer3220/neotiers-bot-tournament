-- Create the tournaments table (if not exists)
CREATE TABLE IF NOT EXISTS tournaments (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    name TEXT NOT NULL,
    end_time BIGINT NOT NULL,
    queue_message_id BIGINT,
    status TEXT DEFAULT 'open',
    guild_id BIGINT NOT NULL,
    current_round INTEGER DEFAULT 0,
    players JSONB DEFAULT '[]'::jsonb
);

-- Disable RLS for tournaments table
ALTER TABLE tournaments DISABLE ROW LEVEL SECURITY;

-- Add missing columns to tournaments table if they don't exist
ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'open';
ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS guild_id BIGINT;
ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS current_round INTEGER DEFAULT 0;
ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS players JSONB DEFAULT '[]'::jsonb;

-- Create the linked_accounts table (if not exists)
CREATE TABLE IF NOT EXISTS linked_accounts (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    discord_id BIGINT UNIQUE NOT NULL,
    minecraft_name TEXT NOT NULL
);

-- Disable RLS for linked_accounts table
ALTER TABLE linked_accounts DISABLE ROW LEVEL SECURITY;

-- Create the matches table (if not exists)
CREATE TABLE IF NOT EXISTS matches (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    tournament_id UUID REFERENCES tournaments(id) ON DELETE CASCADE,
    round INTEGER NOT NULL,
    player1 TEXT NOT NULL,
    player2 TEXT NOT NULL,
    winner TEXT,
    score TEXT,
    ticket_channel_id BIGINT
);

-- Disable RLS for matches table
ALTER TABLE matches DISABLE ROW LEVEL SECURITY;