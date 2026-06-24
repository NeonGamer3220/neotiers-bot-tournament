# NeonTiers | Tournament — Discord Bot

A Python Discord bot for running tournaments with Supabase persistence,
auto-started rounds, per-match ticket channels, and a Minecraft account
linking flow via secret codes.

## Features

- **`/tournamentqueue name timestamp`** — posts a queue message with Join / Leave
  buttons. Players can join until the timestamp fires.
- **Auto-start** — a background loop polls Supabase every 15s; when a
  tournament's `end_time` has passed it automatically starts round 1.
- **`/tournamentround action tournament_id round_number`** — manually start or
  stop a round.
- **`/tournamentaddticket`** — debug: add two players to the current ticket
  channel and post the ticket embed.
- **`/tournamentfixpermissions`** — debug: fix bot permissions in every ticket
  channel under the configured category.
- **`/sync`** / **`/syncglobal`** — slash command sync (admin only).
- **Ticket channels** — one per match, with both players + Regulators able to
  see and post. Each ticket gets a **Jeg lezárása** (close) and **Eredmény
  beírása** (result) button; only Regulators can use them.
- **Result submission** — modal collects the winner; result is posted to the
  configured results channel and persisted in the `matches` table.

## Workflow

1. Admin runs `/tournamentqueue name:teszt timestamp:2026-06-25T18:00:00+02:00`.
2. Players click **Csatlakozás**:
   - If they have a linked Minecraft account in `linked_accounts`, they're
     added to the roster and the queue embed is refreshed.
   - If not, the bot mints a 6-character code, stores it in `pending_codes`
     (TTL = `PENDING_CODE_TTL_MINUTES`, default 30 min), and DMs the user with
     instructions to run `/link <code>` on `chaosffa.kinetic.host`.
   - The Minecraft server is responsible for validating the code, marking it
     `used = true`, and inserting into `linked_accounts`. After that the user
     clicks **Csatlakozás** again and is admitted.
3. When `end_time` passes (or an admin runs `/tournamentround action:start`),
   the queue message is rewritten in place as:

       teszt Tournament - 1. kör
       Játékosok:
       2
       Meccsek:
       @NeonGamer322 | CLUB&CAT ONLY vs @Keszegg | Alex

   And one ticket channel is created per match with the format:

       teszt Tournament
       Párosítás: <@discord1> vs <@discord2>
       In-game nevek: minecraft1 vs minecraft2
       Regulator: <@&REGULATOR_ROLE_ID> szerepkörű tagok használhatják a gombokat
       [Jeg lezárása] [Eredmény beírása]

## Setup

### 1. Database

Your Supabase project already has these tables (do not need to be
re-created):

- `linked_accounts(id, discord_id, minecraft_name, created_at)`
- `pending_codes(id, discord_id, code, created_at, expires_at, used)`
- `tournaments(id, name, end_time, queue_message_id, status, guild_id,
  current_round, players)` — `players` is a JSONB array of
  `{"discord_id": <bigint>, "minecraft_name": <text>}` objects.

Run `migration.sql` in the Supabase SQL editor. It creates only the
`matches` table (and a couple of optional indexes on `tournaments`).
`CREATE TABLE IF NOT EXISTS` makes it safe to re-run.

### 2. Environment variables

Railway already injects these. Locally, copy `.env.example` to `.env` and fill
in the values.

| Variable                  | Required | Notes                                                   |
| ------------------------- | -------- | ------------------------------------------------------- |
| `SUPABASE_URL`            | ✅       |                                                         |
| `SUPABASE_ANON_KEY`       | ✅       |                                                         |
| `DISCORD_TOKEN`           | ✅       |                                                         |
| `CLIENT_ID`               | ✅       | Used for slash sync target                              |
| `GUILD_ID`                | ✅       | The bot will auto-sync slash commands here on boot      |
| `REGULATOR_ROLE_ID`       | ✅       | Defaults to `1483822408182796418`                       |
| `TICKET_CATEGORY_ID`      | ✅       | Category under which ticket channels are created       |
| `RESULTS_CHANNEL_ID`      | ✅       | Where match results are posted                          |
| `PENDING_CODE_TTL_MINUTES`| ⛔       | Default 30                                              |
| `PENDING_CODE_LENGTH`     | ⛔       | Default 6                                               |
| `AUTO_START_POLL_SECONDS` | ⛔       | Default 15                                              |

### 3. Discord application setup

- Enable **Server Members Intent** for the bot (required to add players to
  private ticket channels).
- Enable **Message Content Intent** is *not* required — this bot uses slash
  commands and buttons only.
- Make sure the bot has **Manage Channels**, **Manage Roles**, **Send
  Messages**, **Embed Links**, **Read Message History** permissions in the
  guild.

### 4. Minecraft server side (your responsibility)

When a player runs `/link <code>` on `chaosffa.kinetic.host`, your plugin /
script must:

1. Look up the code in Supabase `pending_codes`.
2. Verify `used = false` and `expires_at > now()`.
3. Insert the player's Minecraft name into `linked_accounts` keyed by the
   code's `discord_id`.
4. Mark the code `used = true`.

The bot does not run any logic on the Minecraft side.

## Running

```bash
pip install -r requirements.txt
python main.py
```

On Railway, set the start command to `python main.py` and ensure the env vars
are present.

## File layout

```
neontiers-bot/
├── main.py            # Bot entry point, debug & sync commands
├── config.py          # Env var loading
├── database.py        # Supabase wrapper
├── tournaments.py     # TournamentCog: queue, auto-start, round, tickets
├── views.py           # Join/Leave buttons, Close/Result buttons, Result modal
├── utils.py           # Helpers (code gen, pairing, formatting)
├── requirements.txt
├── .env.example
├── migration.sql      # Supabase schema
└── README.md
```

## Notes

- The Join / Leave and Close / Result views are **persistent**; they survive
  bot restarts because the bot rehydrates them from the DB in `setup_hook`.
- The queue message is *edited in place* when a round starts — it is not
  reposted. This matches the spec ("always just change the main embed").
- The auto-start loop is per-bot-instance. If you run multiple instances,
  consider adding a row-level lock or a "claimed_by" column to avoid double
  starts.
