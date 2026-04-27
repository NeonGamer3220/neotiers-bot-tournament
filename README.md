# neotiers-bot-tournament

## Setup

1. Fill in the `.env` file with your Supabase and Discord details.

2. Create the following tables in your Supabase database:

### linked_accounts
- `discord_id` (text, primary key)
- `minecraft_username` (text)

### tournaments
- `id` (uuid, primary key, default gen_random_uuid())
- `name` (text)
- `end_time` (timestamptz)
- `queue_message_id` (text)
- `status` (text) // 'open', 'active', 'finished'
- `guild_id` (text)
- `current_round` (int)
- `players` (jsonb) // array of {discord_id, minecraft_username}

### matches
- `id` (uuid, primary key, default gen_random_uuid())
- `tournament_id` (uuid, foreign key to tournaments.id)
- `round` (int)
- `player1` (text) // minecraft username
- `player2` (text)
- `winner` (text)
- `score` (text)
- `ticket_channel_id` (text)

3. Run `npm install` to install dependencies.

4. Run `node deploy.js` to register the slash commands.

5. Run `node index.js` to start the bot.

## Usage

- Use `/tournamentqueue <name> <timestamp>` to create a tournament queue.
- Timestamp should be a Unix timestamp (e.g., 1777464000).
- Only linked players can join.
- Tournament starts automatically when time expires.
- Matches are created in private channels under the specified category.
- Admins can manage tickets (you may need to add admin role permissions in the code).

## Notes

- Assumes even number of players; adjust for odd numbers if needed.
- Results are posted to the specified channel.
- Rounds repeat every 24 hours until one winner.