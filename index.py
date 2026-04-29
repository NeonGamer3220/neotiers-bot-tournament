import os
import random
import asyncio
from datetime import datetime, timezone
from dotenv import load_dotenv

import discord
from discord import app_commands
from discord.ext import commands
from postgrest import AsyncPostgrestClient

load_dotenv()

# Discord bot setup
intents = discord.Intents.default()
intents.guilds = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# PostgREST async client (Supabase database only)
supabase_url = os.getenv('SUPABASE_URL')
anon_key = os.getenv('SUPABASE_ANON_KEY')
pg_client = AsyncPostgrestClient(
    f"{supabase_url}/rest/v1/",
    headers={
        "apikey": anon_key,
        "Authorization": f"Bearer {anon_key}"
    }
)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}!')

async def start_tournament(tournament_id: str):
    """Start a tournament, create matches and ticket channels."""
    try:
        result = await pg_client.from_('tournaments').select('*').eq('id', tournament_id).single().execute()
        tournament = result.data

        if not tournament or tournament['status'] != 'open':
            return

        await pg_client.from_('tournaments').update({
            'status': 'active',
            'current_round': 1
        }).eq('id', tournament_id).execute()

        players = tournament['players']
        shuffled = players.copy()
        random.shuffle(shuffled)

        matches = []
        for i in range(0, len(shuffled), 2):
            if i + 1 < len(shuffled):
                matches.append({'p1': shuffled[i], 'p2': shuffled[i + 1]})

        guild = bot.get_guild(int(os.getenv('GUILD_ID')))
        category_id = int(os.getenv('TICKET_CATEGORY_ID'))
        category = guild.get_channel(category_id)

        for match in matches:
            p1 = match['p1']
            p2 = match['p2']
            channel_name = f"t-r1-{p1['minecraft_username']}-{p2['minecraft_username']}"

            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                guild.get_member(int(p1['discord_id'])): discord.PermissionOverwrite(view_channel=True),
                guild.get_member(int(p2['discord_id'])): discord.PermissionOverwrite(view_channel=True)
            }

            channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites
            )

            embed = discord.Embed(
                title='Tournament 1. kör',
                description=f"{p1['minecraft_username']} vs {p2['minecraft_username']}\nSok sikert!",
                color=discord.Color.blue()
            )

            view = discord.ui.View(timeout=None)
            view.add_item(discord.ui.Button(
                label='Close ticket',
                style=discord.ButtonStyle.danger,
                custom_id=f'close_ticket_{tournament_id}_{p1["minecraft_username"]}_{p2["minecraft_username"]}'
            ))
            view.add_item(discord.ui.Button(
                label='eredmény beírása',
                style=discord.ButtonStyle.primary,
                custom_id=f'result_{tournament_id}_{p1["minecraft_username"]}_{p2["minecraft_username"]}'
            ))

            await channel.send(embed=embed, view=view)

            await pg_client.from_('matches').insert({
                'tournament_id': tournament_id,
                'round': 1,
                'player1': p1['minecraft_username'],
                'player2': p2['minecraft_username'],
                'ticket_channel_id': str(channel.id)
            }).execute()

    except Exception as e:
        print(f"Error starting tournament: {e}")

async def start_round(tournament_id: str, round_num: int):
    """Start a new round with matches."""
    try:
        result = await pg_client.from_('tournaments').select('*').eq('id', tournament_id).single().execute()
        tournament = result.data

        if not tournament:
            return

        players = tournament['players']
        shuffled = players.copy()
        random.shuffle(shuffled)

        matches = []
        for i in range(0, len(shuffled), 2):
            if i + 1 < len(shuffled):
                matches.append({'p1': shuffled[i], 'p2': shuffled[i + 1]})

        guild = bot.get_guild(int(os.getenv('GUILD_ID')))
        category_id = int(os.getenv('TICKET_CATEGORY_ID'))
        category = guild.get_channel(category_id)

        for match in matches:
            p1 = match['p1']
            p2 = match['p2']
            channel_name = f"t-r{round_num}-{p1['minecraft_username']}-{p2['minecraft_username']}"

            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                guild.get_member(int(p1['discord_id'])): discord.PermissionOverwrite(view_channel=True),
                guild.get_member(int(p2['discord_id'])): discord.PermissionOverwrite(view_channel=True)
            }

            channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites
            )

            embed = discord.Embed(
                title=f'Tournament {round_num}. kör',
                description=f"{p1['minecraft_username']} vs {p2['minecraft_username']}\nSok sikert!",
                color=discord.Color.blue()
            )

            view = discord.ui.View(timeout=None)
            view.add_item(discord.ui.Button(
                label='Close ticket',
                style=discord.ButtonStyle.danger,
                custom_id=f'close_ticket_{tournament_id}_{p1["minecraft_username"]}_{p2["minecraft_username"]}'
            ))
            view.add_item(discord.ui.Button(
                label='eredmény beírása',
                style=discord.ButtonStyle.primary,
                custom_id=f'result_{tournament_id}_{p1["minecraft_username"]}_{p2["minecraft_username"]}'
            ))

            await channel.send(embed=embed, view=view)

            await pg_client.from_('matches').insert({
                'tournament_id': tournament_id,
                'round': round_num,
                'player1': p1['minecraft_username'],
                'player2': p2['minecraft_username'],
                'ticket_channel_id': str(channel.id)
            }).execute()

    except Exception as e:
        print(f"Error starting round: {e}")

async def check_round_complete(tournament_id: str):
    """Check if all matches in current round are complete and start next round if so."""
    try:
        result = await pg_client.from_('tournaments').select('*').eq('id', tournament_id).single().execute()
        tournament = result.data

        if not tournament:
            return

        round_num = tournament['current_round']
        result = await pg_client.from_('matches').select('*').eq('tournament_id', tournament_id).eq('round', round_num).execute()
        matches = result.data

        all_done = all(m.get('winner') for m in matches)

        if all_done:
            winners = [m['winner'] for m in matches]

            if len(winners) == 1:
                results_channel = bot.get_channel(int(os.getenv('RESULTS_CHANNEL_ID')))
                await results_channel.send(f"Tournament winner: {winners[0]}")
                await pg_client.from_('tournaments').update({'status': 'finished'}).eq('id', tournament_id).execute()
                return

            # Get Discord IDs for winners
            winners_with_discord = []
            for winner_username in winners:
                result = await pg_client.from_('linked_accounts').select('discord_id').eq('minecraft_username', winner_username).single().execute()
                if result.data:
                    winners_with_discord.append({
                        'discord_id': result.data['discord_id'],
                        'minecraft_username': winner_username
                    })

            await pg_client.from_('tournaments').update({
                'players': winners_with_discord,
                'current_round': round_num + 1
            }).eq('id', tournament_id).execute()

            # Schedule next round after 24 hours
            await asyncio.sleep(24 * 60 * 60)
            await start_round(tournament_id, round_num + 1)

    except Exception as e:
        print(f"Error checking round complete: {e}")

@bot.tree.command(name='tournamentqueue', description='Create a tournament queue')
@app_commands.describe(
    name='Tournament name',
    timestamp='Timestamp for join deadline (unix)'
)
async def tournamentqueue(interaction: discord.Interaction, name: str, timestamp: str):
    """Create a tournament queue with join/leave buttons."""
    await interaction.response.defer()

    # Parse timestamp from Discord format <t:unix:R>
    import re
    match = re.search(r'<t:(\d+):', timestamp)
    if match:
        end_time = int(match.group(1))
    else:
        end_time = int(timestamp)

    embed = discord.Embed(
        title=f'{name} Tournament',
        description=f'Csatlakozási határidő: <t:{end_time}:R>',
        color=discord.Color.green()
    )
    embed.add_field(name='Játékosok:', value='None yet')

    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label='Belépés a tournamentbe',
        style=discord.ButtonStyle.primary,
        custom_id='join_tournament_placeholder'
    ))
    view.add_item(discord.ui.Button(
        label='Kilépés a tournamentből',
        style=discord.ButtonStyle.secondary,
        custom_id='leave_tournament_placeholder'
    ))

    await interaction.followup.send(embed=embed, view=view)
    message = await interaction.original_response()

    # Insert tournament into database
    result = await pg_client.from_('tournaments').insert({
        'name': name,
        'end_time': datetime.fromtimestamp(end_time, tz=timezone.utc).isoformat(),
        'queue_message_id': message.id,
        'status': 'open',
        'guild_id': str(interaction.guild.id),
        'current_round': 0,
        'players': []
    }).execute()

    tournament_id = result.data[0]['id']

    # Update buttons with actual tournament ID
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label='Belépés a tournamentbe',
        style=discord.ButtonStyle.primary,
        custom_id=f'join_tournament_{tournament_id}'
    ))
    view.add_item(discord.ui.Button(
        label='Kilépés a tournamentből',
        style=discord.ButtonStyle.secondary,
        custom_id=f'leave_tournament_{tournament_id}'
    ))

    await message.edit(embed=embed, view=view)

    # Set timer for tournament start
    delay = end_time * 1000 - datetime.now().timestamp() * 1000
    if delay > 0:
        asyncio.create_task(start_tournament_after_delay(tournament_id, delay / 1000))

async def start_tournament_after_delay(tournament_id: str, delay: float):
    """Helper to start tournament after a delay."""
    await asyncio.sleep(delay)
    await start_tournament(tournament_id)

# Button, select menu, and modal handlers
@bot.event
async def on_interaction(interaction: discord.Interaction):
    """Handle all interactions: components and modals."""
    if interaction.type == discord.InteractionType.component:
        custom_id = interaction.data.get('custom_id', '')

        if custom_id.startswith('join_tournament_') and custom_id != 'join_tournament_placeholder':
            await handle_join_tournament(interaction)
        elif custom_id.startswith('leave_tournament_') and custom_id != 'leave_tournament_placeholder':
            await handle_leave_tournament(interaction)
        elif custom_id.startswith('close_ticket_'):
            await handle_close_ticket(interaction)
        elif custom_id.startswith('result_'):
            await handle_result_button(interaction)
        elif custom_id.startswith('select_winner_'):
            await handle_winner_select(interaction)

    elif interaction.type == discord.InteractionType.modal_submit:
        custom_id = interaction.data.get('custom_id', '')
        if custom_id.startswith('score_modal_'):
            await handle_score_modal_submit(interaction)

async def handle_join_tournament(interaction: discord.Interaction):
    """Handle join tournament button click."""
    tournament_id = interaction.data['custom_id'].split('_')[2]

    # Check if user has linked account
    result = await pg_client.from_('linked_accounts').select('minecraft_username').eq('discord_id', str(interaction.user.id)).single().execute()
    if not result.data:
        await interaction.response.send_message('You must link your Minecraft account first.', ephemeral=True)
        return

    minecraft_username = result.data['minecraft_username']

    # Get tournament
    result = await pg_client.from_('tournaments').select('*').eq('id', tournament_id).single().execute()
    tournament = result.data

    if not tournament or tournament['status'] != 'open':
        await interaction.response.send_message('Tournament not open.', ephemeral=True)
        return

    # Check if already joined
    already_joined = any(p['discord_id'] == str(interaction.user.id) for p in tournament['players'])
    if already_joined:
        await interaction.response.send_message('You already joined.', ephemeral=True)
        return

    # Add player
    new_player = {
        'discord_id': str(interaction.user.id),
        'minecraft_username': minecraft_username
    }
    new_players = tournament['players'] + [new_player]

    await pg_client.from_('tournaments').update({'players': new_players}).eq('id', tournament_id).execute()

    # Update embed
    channel = interaction.channel
    message = await channel.fetch_message(tournament['queue_message_id'])
    embed = message.embeds[0]
    players_list = '\n'.join([f'<@{p["discord_id"]}> ({p["minecraft_username"]})' for p in new_players]) or 'None yet'

    new_embed = discord.Embed(
        title=embed.title,
        description=embed.description,
        color=embed.color
    )
    new_embed.add_field(name='Játékosok:', value=players_list)

    await message.edit(embed=new_embed)
    await interaction.response.send_message('Joined the tournament!', ephemeral=True)

async def handle_leave_tournament(interaction: discord.Interaction):
    """Handle leave tournament button click."""
    tournament_id = interaction.data['custom_id'].split('_')[2]

    result = await pg_client.from_('tournaments').select('*').eq('id', tournament_id).single().execute()
    tournament = result.data

    if not tournament or tournament['status'] != 'open':
        await interaction.response.send_message('Tournament not open.', ephemeral=True)
        return

    new_players = [p for p in tournament['players'] if p['discord_id'] != str(interaction.user.id)]

    await pg_client.from_('tournaments').update({'players': new_players}).eq('id', tournament_id).execute()

    # Update embed
    channel = interaction.channel
    message = await channel.fetch_message(tournament['queue_message_id'])
    embed = message.embeds[0]
    players_list = '\n'.join([f'<@{p["discord_id"]}> ({p["minecraft_username"]})' for p in new_players]) or 'None yet'

    new_embed = discord.Embed(
        title=embed.title,
        description=embed.description,
        color=embed.color
    )
    new_embed.add_field(name='Játékosok:', value=players_list)

    await message.edit(embed=new_embed)
    await interaction.response.send_message('Left the tournament!', ephemeral=True)

async def handle_close_ticket(interaction: discord.Interaction):
    """Handle close ticket button click."""
    parts = interaction.data['custom_id'].split('_')
    tournament_id = parts[2]
    p1 = parts[3]
    p2 = parts[4]

    result = await pg_client.from_('matches').select('ticket_channel_id').eq('tournament_id', tournament_id).eq('player1', p1).eq('player2', p2).single().execute()

    if result.data:
        channel_id = int(result.data['ticket_channel_id'])
        channel = bot.get_channel(channel_id)
        if channel:
            await channel.delete()

    await interaction.response.send_message('Ticket closed.', ephemeral=True)

async def handle_result_button(interaction: discord.Interaction):
    """Handle result button click - shows winner selection."""
    parts = interaction.data['custom_id'].split('_')
    tournament_id = parts[1]
    p1 = parts[2]
    p2 = parts[3]

    select = discord.ui.Select(
        placeholder='Select winner',
        custom_id=f'select_winner_{tournament_id}_{p1}_{p2}',
        options=[
            discord.SelectOption(label=f'{p1} won', value=f'{p1}_win'),
            discord.SelectOption(label=f'{p2} won', value=f'{p2}_win')
        ]
    )

    view = discord.ui.View(timeout=None)
    view.add_item(select)

    await interaction.response.send_message('Select the winner:', view=view, ephemeral=True)

async def handle_winner_select(interaction: discord.Interaction):
    """Handle winner selection - shows score input modal."""
    parts = interaction.data['custom_id'].split('_')
    tournament_id = parts[2]
    p1 = parts[3]
    p2 = parts[4]
    winner = interaction.data['values'][0].split('_')[0]

    modal = discord.ui.Modal(
        title='Enter Score',
        custom_id=f'score_modal_{tournament_id}_{p1}_{p2}_{winner}'
    )

    score_input = discord.ui.TextInput(
        label='Score (e.g., 7-3)',
        style=discord.TextStyle.short,
        placeholder='e.g., 7-3',
        required=True
    )
    modal.add_item(score_input)

    await interaction.response.send_modal(modal)

async def handle_score_modal_submit(interaction: discord.Interaction):
    """Handle score modal submission."""
    custom_id = interaction.data['custom_id']
    parts = custom_id.split('_')
    tournament_id = parts[2]
    p1 = parts[3]
    p2 = parts[4]
    winner = parts[5]
    score = interaction.data['components'][0]['components'][0]['value']

    # Update match
    await pg_client.from_('matches').update({
        'winner': winner,
        'score': score
    }).eq('tournament_id', tournament_id).eq('player1', p1).eq('player2', p2).execute()

    # Post to results channel
    results_channel = bot.get_channel(int(os.getenv('RESULTS_CHANNEL_ID')))
    await results_channel.send(f'{p1} vs {p2}: {winner} won {score}')

    # Delete ticket channel
    result = await pg_client.from_('matches').select('ticket_channel_id').eq('tournament_id', tournament_id).eq('player1', p1).eq('player2', p2).single().execute()
    if result.data:
        channel_id = int(result.data['ticket_channel_id'])
        channel = bot.get_channel(channel_id)
        if channel:
            await channel.delete()

    # Check if round complete
    await check_round_complete(tournament_id)

    await interaction.response.send_message('Result submitted.', ephemeral=True)

if __name__ == '__main__':
    bot.run(os.getenv('DISCORD_TOKEN'))