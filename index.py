import os
from dotenv import load_dotenv
load_dotenv()

import discord
from discord import app_commands
from supabase import create_client
import asyncio
import time

intents = discord.Intents.default()
intents.guilds = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

supabase = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_ANON_KEY'))

@client.event
async def on_ready():
    await tree.sync()
    print(f'Logged in as {client.user}')

@tree.command(name="tournamentqueue", description="Create a tournament queue")
@app_commands.describe(name="Tournament name", timestamp="End timestamp")
async def tournamentqueue(interaction: discord.Interaction, name: str, timestamp: str):
    end_time = int(timestamp.replace('<t:', '').replace(':R>', ''))

    # Insert tournament first
    data, error = supabase.table('tournaments').insert({
        'name': name,
        'end_time': end_time * 1000,
        'guild_id': int(os.getenv('GUILD_ID')),
        'current_round': 0,
        'players': []
    }).execute()

    if error:
        print(error)
        await interaction.response.send_message("Failed to create tournament.", ephemeral=True)
        return

    tournament_id = data[1][0]['id']

    embed = discord.Embed(title=f"{name} Tournament", description=f"Csatlakozási határidő: <t:{end_time}:R>", color=0x00FF00)
    embed.add_field(name="Játékosok:", value="None yet")

    join_button = discord.ui.Button(label="Belépés a tournamentbe", style=discord.ButtonStyle.primary, custom_id=f"join_tournament_{tournament_id}")
    leave_button = discord.ui.Button(label="Kilépés a tournamentből", style=discord.ButtonStyle.secondary, custom_id=f"leave_tournament_{tournament_id}")
    view = discord.ui.View()
    view.add_item(join_button)
    view.add_item(leave_button)

    await interaction.response.send_message(embed=embed, view=view)
    message = await interaction.original_response()

    # Update tournament with message ID
    supabase.table('tournaments').update({'queue_message_id': message.id}).eq('id', tournament_id).execute()

    # Set timer
    delay = end_time * 1000 - time.time() * 1000
    if delay > 0:
        await asyncio.sleep(delay / 1000)
        await start_tournament(tournament_id)

@client.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type == discord.InteractionType.component:
        custom_id = interaction.data['custom_id']
        
        if custom_id.startswith('join_tournament_'):
            tournament_id = custom_id.split('_')[2]
            # Check linked
            linked, error = supabase.table('linked_accounts').select('minecraft_name').eq('discord_id', interaction.user.id).execute()
            if not linked[1]:
                await interaction.response.send_message("You must link your Minecraft account first.", ephemeral=True)
                return
            minecraft_name = linked[1][0]['minecraft_name']
            # Get tournament
            tournament, error = supabase.table('tournaments').select('*').eq('id', tournament_id).execute()
            if not tournament[1] or tournament[1][0]['status'] != 'open':
                await interaction.response.send_message("Tournament not open.", ephemeral=True)
                return
            players = tournament[1][0]['players']
            if any(p['discord_id'] == interaction.user.id for p in players):
                await interaction.response.send_message("You already joined.", ephemeral=True)
                return
            new_players = players + [{'discord_id': interaction.user.id, 'minecraft_name': minecraft_name}]
            supabase.table('tournaments').update({'players': new_players}).eq('id', tournament_id).execute()
            # Update embed
            channel = interaction.channel
            message = await channel.fetch_message(tournament[1][0]['queue_message_id'])
            embed = message.embeds[0]
            players_list = '\n'.join([f"<@{p['discord_id']}> ({p['minecraft_name']})" for p in new_players]) or 'None yet'
            new_embed = discord.Embed.from_dict(embed.to_dict())
            new_embed.set_field_at(0, name="Játékosok:", value=players_list)
            await message.edit(embed=new_embed)
            await interaction.response.send_message("Joined the tournament!", ephemeral=True)
        
        elif custom_id.startswith('leave_tournament_'):
            tournament_id = custom_id.split('_')[2]
            tournament, error = supabase.table('tournaments').select('*').eq('id', tournament_id).execute()
            if not tournament[1] or tournament[1][0]['status'] != 'open':
                await interaction.response.send_message("Tournament not open.", ephemeral=True)
                return
            players = tournament[1][0]['players']
            new_players = [p for p in players if p['discord_id'] != interaction.user.id]
            supabase.table('tournaments').update({'players': new_players}).eq('id', tournament_id).execute()
            # Update embed
            channel = interaction.channel
            message = await channel.fetch_message(tournament[1][0]['queue_message_id'])
            embed = message.embeds[0]
            players_list = '\n'.join([f"<@{p['discord_id']}> ({p['minecraft_name']})" for p in new_players]) or 'None yet'
            new_embed = discord.Embed.from_dict(embed.to_dict())
            new_embed.set_field_at(0, name="Játékosok:", value=players_list)
            await message.edit(embed=new_embed)
            await interaction.response.send_message("Left the tournament!", ephemeral=True)
        
        elif custom_id.startswith('close_ticket_'):
            parts = custom_id.split('_')
            tournament_id = parts[2]
            p1 = parts[3]
            p2 = parts[4]
            match, error = supabase.table('matches').select('ticket_channel_id').eq('tournament_id', tournament_id).eq('player1', p1).eq('player2', p2).execute()
            if match[1]:
                channel = await client.fetch_channel(match[1][0]['ticket_channel_id'])
                await channel.delete()
            await interaction.response.send_message("Ticket closed.", ephemeral=True)
        
        elif custom_id.startswith('result_'):
            parts = custom_id.split('_')
            tournament_id = parts[1]
            p1 = parts[2]
            p2 = parts[3]
            select = discord.ui.Select(placeholder="Select winner", options=[
                discord.SelectOption(label=f"{p1} won", value=f"{p1}_win"),
                discord.SelectOption(label=f"{p2} won", value=f"{p2}_win")
            ], custom_id=f"select_winner_{tournament_id}_{p1}_{p2}")
            view = discord.ui.View()
            view.add_item(select)
            await interaction.response.send_message("Select the winner:", view=view, ephemeral=True)
        
        elif custom_id.startswith('select_winner_'):
            parts = custom_id.split('_')
            tournament_id = parts[2]
            p1 = parts[3]
            p2 = parts[4]
            winner = interaction.data['values'][0].split('_')[0]
            modal = ScoreModal(tournament_id, p1, p2, winner)
            await interaction.response.send_modal(modal)
        
        elif custom_id.startswith('score_modal_'):
            parts = custom_id.split('_')
            tournament_id = parts[2]
            p1 = parts[3]
            p2 = parts[4]
            winner = parts[5]
            score = interaction.data['components'][0]['components'][0]['value']
            supabase.table('matches').update({'winner': winner, 'score': score}).eq('tournament_id', tournament_id).eq('player1', p1).eq('player2', p2).execute()
            # Post to results
            results_channel = await client.fetch_channel(int(os.getenv('RESULTS_CHANNEL_ID')))
            await results_channel.send(f"{p1} vs {p2}: {winner} won {score}")
            # Delete ticket
            match, error = supabase.table('matches').select('ticket_channel_id').eq('tournament_id', tournament_id).eq('player1', p1).eq('player2', p2).execute()
            if match[1]:
                channel = await client.fetch_channel(match[1][0]['ticket_channel_id'])
                await channel.delete()
            await check_round_complete(tournament_id)
            await interaction.response.send_message("Result submitted.", ephemeral=True)

class ScoreModal(discord.ui.Modal, title="Enter Score"):
    score = discord.ui.TextInput(label="Score (e.g., 7-3)", style=discord.TextStyle.short, required=True)
    
    def __init__(self, tournament_id, p1, p2, winner):
        super().__init__()
        self.tournament_id = tournament_id
        self.p1 = p1
        self.p2 = p2
        self.winner = winner
    
    async def on_submit(self, interaction: discord.Interaction):
        score = self.score.value
        supabase.table('matches').update({'winner': self.winner, 'score': score}).eq('tournament_id', self.tournament_id).eq('player1', self.p1).eq('player2', self.p2).execute()
        # Post to results
        results_channel = await client.fetch_channel(int(os.getenv('RESULTS_CHANNEL_ID')))
        await results_channel.send(f"{self.p1} vs {self.p2}: {self.winner} won {score}")
        # Delete ticket
        match, error = supabase.table('matches').select('ticket_channel_id').eq('tournament_id', self.tournament_id).eq('player1', self.p1).eq('player2', self.p2).execute()
        if match[1]:
            channel = await client.fetch_channel(match[1][0]['ticket_channel_id'])
            await channel.delete()
        await check_round_complete(self.tournament_id)
        await interaction.response.send_message("Result submitted.", ephemeral=True)

async def start_tournament(tournament_id):
    tournament, error = supabase.table('tournaments').select('*').eq('id', tournament_id).execute()
    if not tournament[1] or tournament[1][0]['status'] != 'open':
        return
    supabase.table('tournaments').update({'status': 'active', 'current_round': 1}).eq('id', tournament_id).execute()
    players = tournament[1][0]['players']
    shuffled = players[:]
    import random
    random.shuffle(shuffled)
    matches = []
    for i in range(0, len(shuffled), 2):
        if i + 1 < len(shuffled):
            matches.append({'p1': shuffled[i], 'p2': shuffled[i+1]})
    guild = client.get_guild(int(os.getenv('GUILD_ID')))
    category = await guild.fetch_channel(int(os.getenv('TICKET_CATEGORY_ID')))
    for match in matches:
        channel_name = f"t-r1-{match['p1']['minecraft_name']}-{match['p2']['minecraft_name']}"
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.get_member(match['p1']['discord_id']): discord.PermissionOverwrite(view_channel=True),
            guild.get_member(match['p2']['discord_id']): discord.PermissionOverwrite(view_channel=True)
        }
        channel = await guild.create_text_channel(channel_name, category=category, overwrites=overwrites)
        embed = discord.Embed(title="Tournament 1. kör", description=f"{match['p1']['minecraft_name']} vs {match['p2']['minecraft_name']}\nSok sikert!", color=0x0000FF)
        close_button = discord.ui.Button(label="Close ticket", style=discord.ButtonStyle.danger, custom_id=f"close_ticket_{tournament_id}_{match['p1']['minecraft_name']}_{match['p2']['minecraft_name']}")
        result_button = discord.ui.Button(label="eredmény beírása", style=discord.ButtonStyle.primary, custom_id=f"result_{tournament_id}_{match['p1']['minecraft_name']}_{match['p2']['minecraft_name']}")
        view = discord.ui.View()
        view.add_item(close_button)
        view.add_item(result_button)
        await channel.send(embed=embed, view=view)
        supabase.table('matches').insert({
            'tournament_id': tournament_id,
            'round': 1,
            'player1': match['p1']['minecraft_name'],
            'player2': match['p2']['minecraft_name'],
            'ticket_channel_id': channel.id
        }).execute()

async def start_round(tournament_id, round_num):
    tournament, error = supabase.table('tournaments').select('*').eq('id', tournament_id).execute()
    if not tournament[1]:
        return
    players = tournament[1][0]['players']
    shuffled = players[:]
    import random
    random.shuffle(shuffled)
    matches = []
    for i in range(0, len(shuffled), 2):
        if i + 1 < len(shuffled):
            matches.append({'p1': shuffled[i], 'p2': shuffled[i+1]})
    guild = client.get_guild(int(os.getenv('GUILD_ID')))
    category = await guild.fetch_channel(int(os.getenv('TICKET_CATEGORY_ID')))
    for match in matches:
        channel_name = f"t-r{round_num}-{match['p1']['minecraft_name']}-{match['p2']['minecraft_name']}"
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.get_member(match['p1']['discord_id']): discord.PermissionOverwrite(view_channel=True),
            guild.get_member(match['p2']['discord_id']): discord.PermissionOverwrite(view_channel=True)
        }
        channel = await guild.create_text_channel(channel_name, category=category, overwrites=overwrites)
        embed = discord.Embed(title=f"Tournament {round_num}. kör", description=f"{match['p1']['minecraft_name']} vs {match['p2']['minecraft_name']}\nSok sikert!", color=0x0000FF)
        close_button = discord.ui.Button(label="Close ticket", style=discord.ButtonStyle.danger, custom_id=f"close_ticket_{tournament_id}_{match['p1']['minecraft_name']}_{match['p2']['minecraft_name']}")
        result_button = discord.ui.Button(label="eredmény beírása", style=discord.ButtonStyle.primary, custom_id=f"result_{tournament_id}_{match['p1']['minecraft_name']}_{match['p2']['minecraft_name']}")
        view = discord.ui.View()
        view.add_item(close_button)
        view.add_item(result_button)
        await channel.send(embed=embed, view=view)
        supabase.table('matches').insert({
            'tournament_id': tournament_id,
            'round': round_num,
            'player1': match['p1']['minecraft_name'],
            'player2': match['p2']['minecraft_name'],
            'ticket_channel_id': channel.id
        }).execute()

async def check_round_complete(tournament_id):
    tournament, error = supabase.table('tournaments').select('*').eq('id', tournament_id).execute()
    round_num = tournament[1][0]['current_round']
    matches, error = supabase.table('matches').select('*').eq('tournament_id', tournament_id).eq('round', round_num).execute()
    all_done = all(m['winner'] for m in matches[1])
    if all_done:
        winners = [m['winner'] for m in matches[1]]
        if len(winners) == 1:
            results_channel = await client.fetch_channel(int(os.getenv('RESULTS_CHANNEL_ID')))
            await results_channel.send(f"Tournament winner: {winners[0]}")
            supabase.table('tournaments').update({'status': 'finished'}).eq('id', tournament_id).execute()
            return
        winners_with_discord = []
        for w in winners:
            linked, error = supabase.table('linked_accounts').select('discord_id').eq('minecraft_name', w).execute()
            winners_with_discord.append({'discord_id': linked[1][0]['discord_id'], 'minecraft_name': w})
        supabase.table('tournaments').update({'players': winners_with_discord, 'current_round': round_num + 1}).eq('id', tournament_id).execute()
        await asyncio.sleep(24 * 60 * 60)
        await start_round(tournament_id, round_num + 1)

client.run(os.getenv('DISCORD_TOKEN'))