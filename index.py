import os
from dotenv import load_dotenv
load_dotenv()

import discord
from discord import app_commands
from supabase import create_client
from postgrest.exceptions import APIError
import asyncio
import time

intents = discord.Intents.default()
intents.guilds = True
intents.members = True  # Needed to look up members for channel permissions
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

supabase_url = os.getenv('SUPABASE_URL')
supabase_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY') or os.getenv('SUPABASE_ANON_KEY')
if not supabase_url or not supabase_key:
    raise EnvironmentError("Missing required Supabase environment variables")
supabase = create_client(supabase_url, supabase_key)
print(f"Supabase initialized with {'service_role' if os.getenv('SUPABASE_SERVICE_ROLE_KEY') else 'anon'} key")

@client.event
async def on_ready():
    try:
        import sys
        print(f'Logged in as {client.user} (ID: {client.user.id})')
        guilds = [g.id for g in client.guilds]
        print(f'Bot is in guilds: {guilds}')
        sys.stdout.flush()
        
        guild_id_env = os.getenv('GUILD_ID')
        print(f'GUILD_ID from env: {guild_id_env}')
        sys.stdout.flush()
        
        try:
            guild_id = int(guild_id_env)
        except (TypeError, ValueError):
            print(f'ERROR: Érvénytelen GUILD_ID: {guild_id_env}')
            sys.stdout.flush()
            return
        
        guild = client.get_guild(guild_id)
        if not guild:
            print(f'WARNING: A bot NINCS a beállított szerverben {guild_id}. Elérhető szerverek: {guilds}')
            if client.guilds:
                guild = client.guilds[0]
                guild_id = guild.id
                print(f'Fallback: első elérhető szerver: {guild.name} (ID: {guild_id})')
            else:
                print('ERROR: A bot nincs egyetlen szerverben sem. Nem lehet parancsokat szinkronizálni.')
                sys.stdout.flush()
                return
        sys.stdout.flush()
        
        print(f'Bot user ID: {client.user.id}')
        all_commands = tree.get_commands()
        print(f'Tree has {len(all_commands)} commands registered: {[c.name for c in all_commands]}')
        sys.stdout.flush()
        
        await asyncio.sleep(2)
        
        guild_obj = discord.Object(id=guild_id)
        
        # Clear existing
        try:
            existing = await tree.fetch_commands(guild=guild_obj)
            print(f"Found {len(existing)} existing commands in guild")
            for cmd in existing:
                await tree.delete_command(cmd.name, guild=guild_obj)
            print("Existing commands cleared")
        except Exception as e:
            print(f"Nem sikerült törölni a korábbi parancsokat: {e}")
        
        # Sync
        print(f'Szinkronizálás a szerverre: {guild_id}...')
        try:
            synced = await tree.sync(guild=guild_obj)
            print(f'Sync returned: {[c.name for c in synced]} ({len(synced)} commands)')
        except Exception as e:
            print(f'Sync error: {e}')
            import traceback; traceback.print_exc()
        
        # Verify
        await asyncio.sleep(2)
        commands = await tree.fetch_commands(guild=guild_obj)
        print(f"VÉGLEGES - Regisztrált parancsok: {[c.name for c in commands]}")
        print(f"VÉGLEGES - Összesen: {len(commands)}")
        sys.stdout.flush()
        
        if len(commands) == 0:
            print('FIGYELEM: Nincsenek regisztrált parancsok. Jogosultságok ellenőrzése...')
            print(f'Bot scope: {client.user.public_flags}')
            print(f'Bot mentions: {client.user.mention}')
        sys.stdout.flush()
    except Exception as e:
        import traceback
        print(f'ERROR in on_ready: {e}')
        traceback.print_exc()
        sys.stdout.flush()

@tree.command(name="tournamentqueue", description="Tournament létrehozása")
@app_commands.describe(name="Tournament neve", timestamp="Befejezés időpontja")
async def tournamentqueue(interaction: discord.Interaction, name: str, timestamp: str):
    end_time = int(timestamp.replace('<t:', '').replace(':R>', ''))

    try:
        response = supabase.table('tournaments').insert({
            'name': name,
            'end_time': end_time * 1000,
            'guild_id': int(os.getenv('GUILD_ID')),
            'current_round': 0,
            'players': []
        }).execute()
        tournament_id = response.data[0]['id']
    except APIError as e:
        print(f"Hiba a tournament létrehozásakor: {e}")
        await interaction.response.send_message(f"Nem sikerült létrehozni a tournamentt: {e}", ephemeral=True)
        return

    embed = discord.Embed(title=f"{name} Tournament", description=f"Csatlakozási határidő: <t:{end_time}:R>", color=0x00FF00)
    embed.add_field(name="Játékosok:", value="None yet")

    join_button = discord.ui.Button(label="Belépés a tournamentba", style=discord.ButtonStyle.primary, custom_id=f"join_tournament_{tournament_id}")
    leave_button = discord.ui.Button(label="Kilépés a tournamentból", style=discord.ButtonStyle.secondary, custom_id=f"leave_tournament_{tournament_id}")
    view = discord.ui.View()
    view.add_item(join_button)
    view.add_item(leave_button)

    await interaction.response.send_message(embed=embed, view=view)
    message = await interaction.original_response()

    try:
        supabase.table('tournaments').update({'queue_message_id': message.id}).eq('id', tournament_id).execute()
    except APIError as e:
        print(f"Nem sikerült frissíteni a queue_message_id-t: {e}")

    # Set timer
    delay = end_time * 1000 - time.time() * 1000
    if delay > 0:
        await asyncio.sleep(delay / 1000)
    # Start tournament regardless (if delay <= 0, start immediately)
    try:
        await start_tournament(tournament_id)
        except Exception as e:
            print(f"Hiba a tournament automatikus indításakor: {e}")

@tree.command(name="tournamentround", description="Kör indítása/leállítása")
@app_commands.describe(
    action="Indítás vagy leállítás",
    tournament_id="Tournament azonosító",
    round_number="Kör száma"
)
async def tournamentround(interaction: discord.Interaction, action: str, tournament_id: str, round_number: int):
    await interaction.response.defer(ephemeral=True)
    
    try:
        tournament_uuid = tournament_id
        tournament_response = supabase.table('tournaments').select('*').eq('id', tournament_uuid).execute()
        if not tournament_response.data:
            await interaction.followup.send("Tournament nem található.", ephemeral=True)
            return
        tournament = tournament_response.data[0]
        
        if action.lower() == 'start':
            if tournament['status'] not in ['open', 'active']:
                await interaction.followup.send("A tournamentnak nyitottnak vagy aktívnak kell lennie a kör indításához.", ephemeral=True)
                return
            
            players = tournament['players']
            if len(players) < 2:
                await interaction.followup.send("Nincs elég játékos a kör indításához (min. 2 szükséges).", ephemeral=True)
                return
            
            shuffled = players[:]
            import random
            random.shuffle(shuffled)
            matches = []
            for i in range(0, len(shuffled), 2):
                if i + 1 < len(shuffled):
                    matches.append({'p1': shuffled[i], 'p2': shuffled[i+1]})
            
            guild = client.get_guild(int(os.getenv('GUILD_ID')))
            category_id = int(os.getenv('TICKET_CATEGORY_ID'))
            category = guild.get_channel(category_id)
            if not category or not isinstance(category, discord.CategoryChannel):
                await interaction.followup.send(f"Érvénytelen jegykategória ID: {category_id}", ephemeral=True)
                return
            
            for match in matches:
                channel_name = f"t-r{round_number}-{match['p1']['minecraft_name']}-{match['p2']['minecraft_name']}"
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(view_channel=False, send_messages=False)
                }
                # Bot always gets full access
                bot_member = guild.get_member(client.user.id)
                if bot_member:
                    overwrites[bot_member] = discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        manage_channels=True,
                        manage_permissions=True,
                        read_message_history=True
                    )
                else:
                    # Fallback to using Object ID if member not cached
                    overwrites[discord.Object(id=client.user.id)] = discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        manage_channels=True,
                        manage_permissions=True,
                        read_message_history=True
                    )
                
                # Add players using Object IDs (works without member cache)
                overwrites[discord.Object(id=match['p1']['discord_id'])] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True
                )
                overwrites[discord.Object(id=match['p2']['discord_id'])] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True
                )
                
                try:
                    channel = await guild.create_text_channel(channel_name, category=category, overwrites=overwrites)
                except discord.Forbidden:
                    await interaction.followup.send("A botnak nincs jogosultsága csatornák létrehozására ebben a kategóriában.", ephemeral=True)
                    return
                embed = discord.Embed(title=f"Tournament {round_number}. kör", description=f"{match['p1']['minecraft_name']} vs {match['p2']['minecraft_name']}\nSok sikert!", color=0x0000FF)
                close_button = discord.ui.Button(label="Jegy lezárása", style=discord.ButtonStyle.danger, custom_id=f"close_ticket_{tournament_uuid}_{match['p1']['minecraft_name']}_{match['p2']['minecraft_name']}")
                result_button = discord.ui.Button(label="eredmény beírása", style=discord.ButtonStyle.primary, custom_id=f"result_{tournament_uuid}_{match['p1']['minecraft_name']}_{match['p2']['minecraft_name']}")
                view = discord.ui.View()
                view.add_item(close_button)
                view.add_item(result_button)
                await channel.send(embed=embed, view=view)
                try:
                    supabase.table('matches').insert({
                        'tournament_id': tournament_uuid,
                        'round': round_number,
                        'player1': match['p1']['minecraft_name'],
                        'player2': match['p2']['minecraft_name'],
                        'ticket_channel_id': channel.id
                    }).execute()
                except APIError as e:
                    print(f"Nem sikerült beszúrni a mérkőzést: {e}")
            
            supabase.table('tournaments').update({'current_round': round_number, 'status': 'active'}).eq('id', tournament_uuid).execute()
            await interaction.followup.send(f"{round_number}. kör indítva {len(matches)} mérkőzéssel.", ephemeral=True)
            
        elif action.lower() == 'stop':
            matches_response = supabase.table('matches').select('*').eq('tournament_id', tournament_uuid).eq('round', round_number).execute()
            if not matches_response.data:
                await interaction.followup.send("Nincsenek mérkőzések ebben a körben.", ephemeral=True)
                return
            
        elif action.lower() == 'stop':
            matches_response = supabase.table('matches').select('*').eq('tournament_id', tournament_uuid).eq('round', round_number).execute()
            if not matches_response.data:
                await interaction.followup.send("Nincsenek mérkőzések ebben a körben.", ephemeral=True)
                return
            
            deleted_channels = 0
            for match in matches_response.data:
                if match['ticket_channel_id']:
                    channel = await client.fetch_channel(match['ticket_channel_id'])
                    if channel:
                try:
                    await channel.delete()
                    print(f"Jegycsatorna törölve: {channel_id}")
                except discord.Forbidden:
                    print("Botnak nincs 'Csatornák kezelése' jogosultsága a jegy törléséhez")
                except Exception as e:
                    print(f"Hiba a csatorna törlésekor: {e}")
            
            supabase.table('matches').delete().eq('tournament_id', tournament_uuid).eq('round', round_number).execute()
            await interaction.followup.send(f"{round_number}. kör leállítva. {deleted_channels} jegycsatorna törölve.", ephemeral=True)
        
        else:
            await interaction.followup.send("Érvénytelen művelet. Használj 'start' vagy 'stop'.", ephemeral=True)
            
    except APIError as e:
        print(f"Error in tournamentround: {e}")
        await interaction.followup.send(f"Adatbázis hiba: {e}", ephemeral=True)
    except Exception as e:
        print(f"Váratlan hiba in tournamentround: {e}")
        await interaction.followup.send(f"Hiba: {e}", ephemeral=True)


@tree.command(name="tournamentaddticket", description="Játékosok hozzáadása jegycsatornákhoz (debug)")
@app_commands.describe(tournament_id="Tournament azonosító")
async def tournamentaddticket(interaction: discord.Interaction, tournament_id: str):
    await interaction.response.defer(ephemeral=True)
    
    try:
        tournament_response = supabase.table('tournaments').select('*').eq('id', tournament_id).execute()
        if not tournament_response.data:
            await interaction.followup.send("Tournament nem található.", ephemeral=True)
            return
        
        tournament = tournament_response.data[0]
        players = tournament['players']
        if not players:
            await interaction.followup.send("Nincsenek játékosok a tournamentban.", ephemeral=True)
            return
        
        guild = client.get_guild(int(os.getenv('GUILD_ID')))
        bot_member = guild.get_member(client.user.id)
        
        matches_response = supabase.table('matches').select('*').eq('tournament_id', tournament_id).execute()
        if not matches_response.data:
            await interaction.followup.send("Nincsenek mérkőzések. Indíts egy kört először.", ephemeral=True)
            return
        
        updated = 0
        for match in matches_response.data:
            channel_id = match['ticket_channel_id']
            if not channel_id:
                continue
            
            try:
                channel = await client.fetch_channel(channel_id)
                if not channel or not isinstance(channel, discord.TextChannel):
                    continue
                
                p1_discord_id = None
                p2_discord_id = None
                for p in players:
                    if p['minecraft_name'] == match['player1']:
                        p1_discord_id = p['discord_id']
                    if p['minecraft_name'] == match['player2']:
                        p2_discord_id = p['discord_id']
                
                if not p1_discord_id or not p2_discord_id:
                    continue
                
                overwrites = channel.overwrites.copy() if channel.overwrites else {}
                p1_member = guild.get_member(p1_discord_id)
                p2_member = guild.get_member(p2_discord_id)
                
                if p1_member:
                    overwrites[p1_member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
                if p2_member:
                    overwrites[p2_member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
                if bot_member:
                    overwrites[bot_member] = discord.PermissionOverwrite(
                        view_channel=True, send_messages=True, manage_channels=True, manage_permissions=True, read_message_history=True
                    )
                
                await channel.edit(overwrites=overwrites)
                updated += 1
            except Exception as e:
                print(f"Failed to update channel {channel_id}: {e}")
                continue
        
        await interaction.followup.send(f"{updated} jegycsatorna frissítve a játékos jogosultságokkal.", ephemeral=True)
        
    except APIError as e:
        print(f"Error in tournamentaddticket: {e}")
        await interaction.followup.send(f"Adatbázis hiba: {e}", ephemeral=True)
    except Exception as e:
        print(f"Váratlan hiba in tournamentaddticket: {e}")
        await interaction.followup.send(f"Hiba: {e}", ephemeral=True)


@tree.command(name="tournamentfixpermissions", description="Bot jogosultság javítása összes jegycsatornában")
@app_commands.describe(tournament_id="Tournament azonosító")
async def tournamentfixpermissions(interaction: discord.Interaction, tournament_id: str):
    await interaction.response.defer(ephemeral=True)
    
    try:
        guild = client.get_guild(int(os.getenv('GUILD_ID')))
        bot_member = guild.get_member(client.user.id)
        
        matches_response = supabase.table('matches').select('*').eq('tournament_id', tournament_id).execute()
        if not matches_response.data:
            await interaction.followup.send("Nincsenek mérkőzések.", ephemeral=True)
            return
        
        updated = 0
        for match in matches_response.data:
            channel_id = match['ticket_channel_id']
            if not channel_id:
                continue
            
            try:
                channel = await client.fetch_channel(channel_id)
                if not channel or not isinstance(channel, discord.TextChannel):
                    continue
                
                overwrites = channel.overwrites.copy() if channel.overwrites else {}
                if bot_member:
                    overwrites[bot_member] = discord.PermissionOverwrite(
                        view_channel=True, send_messages=True, manage_channels=True, manage_permissions=True, read_message_history=True
                    )
                
                await channel.edit(overwrites=overwrites)
                updated += 1
            except Exception as e:
                print(f"Failed to update channel {channel_id}: {e}")
                continue
        
        await interaction.followup.send(f"Bot jogosultságai javítva {updated} jegycsatornában.", ephemeral=True)
        
    except APIError as e:
        print(f"Error in tournamentfixpermissions: {e}")
        await interaction.followup.send(f"Adatbázis hiba: {e}", ephemeral=True)
    except Exception as e:
        print(f"Váratlan hiba in tournamentfixpermissions: {e}")
        await interaction.followup.send(f"Hiba: {e}", ephemeral=True)


@tree.command(name="sync", description="Parancsok szinkronizálása (csak admin)")
async def sync_commands(interaction: discord.Interaction):
    # Check if user has admin role
    admin_role_id = os.getenv('ADMIN_ROLE_ID')
    if admin_role_id:
        admin_role = interaction.guild.get_role(int(admin_role_id))
        if admin_role and admin_role not in interaction.user.roles:
            await interaction.response.send_message("Adminisztrátori szerepkör szükséges a parancs használatához.", ephemeral=True)
            return
    
    await interaction.response.defer(ephemeral=True)
    try:
        guild_id = int(os.getenv('GUILD_ID'))
        await tree.sync(guild=discord.Object(id=guild_id))
        commands = await tree.fetch_commands(guild=discord.Object(id=guild_id))
        await interaction.followup.send(
            f"Parancsok szinkronizálva a szerverre: {', '.join([cmd.name for cmd in commands])}",
            ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(f"Szinkronizálás sikertelen: {e}", ephemeral=True)


@tree.command(name="syncglobal", description="Globális szinkronizálás (csak admin)")
async def sync_global(interaction: discord.Interaction):
    # Check if user has admin role
    admin_role_id = os.getenv('ADMIN_ROLE_ID')
    if admin_role_id:
        admin_role = interaction.guild.get_role(int(admin_role_id))
        if admin_role and admin_role not in interaction.user.roles:
            await interaction.response.send_message("Adminisztrátori szerepkör szükséges a parancs használatához.", ephemeral=True)
            return
    
    await interaction.response.defer(ephemeral=True)
    try:
        await tree.sync()
        commands = await tree.fetch_commands()
        await interaction.followup.send(
            f"Globális parancsok szinkronizálva: {', '.join([cmd.name for cmd in commands])}",
            ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(f"Szinkronizálás sikertelen: {e}", ephemeral=True)


@client.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type != discord.InteractionType.component:
        return
    custom_id = interaction.data['custom_id']

    try:
        if custom_id.startswith('join_tournament_'):
            tournament_id = custom_id.split('_')[2]
            try:
                await interaction.response.defer(ephemeral=True)
            except Exception:
                pass
            linked_response = supabase.table('linked_accounts').select('minecraft_name').eq('discord_id', interaction.user.id).execute()
            if not linked_response.data:
                await interaction.followup.send("Először csatlakoztatni kell a Minecraft fiókot.", ephemeral=True)
                return
            minecraft_name = linked_response.data[0]['minecraft_name']
            tournament_response = supabase.table('tournaments').select('*').eq('id', tournament_id).execute()
            if not tournament_response.data or tournament_response.data[0]['status'] != 'open':
                await interaction.followup.send("A tournament nincs nyitva.", ephemeral=True)
                return
            tournament = tournament_response.data[0]
            players = tournament['players']
            if any(p['discord_id'] == interaction.user.id for p in players):
                await interaction.followup.send("Már csatlakoztál.", ephemeral=True)
                return
            new_players = players + [{'discord_id': interaction.user.id, 'minecraft_name': minecraft_name}]
            supabase.table('tournaments').update({'players': new_players}).eq('id', tournament_id).execute()
            channel = interaction.channel
            message = await channel.fetch_message(tournament['queue_message_id'])
            embed = message.embeds[0]
            players_list = '\n'.join([f"<@{p['discord_id']}> ({p['minecraft_name']})" for p in new_players]) or 'Még senki'
            new_embed = discord.Embed.from_dict(embed.to_dict())
            new_embed.set_field_at(0, name="Játékosok:", value=players_list)
            await message.edit(embed=new_embed)
            await interaction.followup.send("Sikeresen csatlakoztál a tournamenthoz!", ephemeral=True)

        elif custom_id.startswith('leave_tournament_'):
            tournament_id = custom_id.split('_')[2]
            try:
                await interaction.response.defer(ephemeral=True)
            except Exception:
                pass
            tournament_response = supabase.table('tournaments').select('*').eq('id', tournament_id).execute()
            if not tournament_response.data or tournament_response.data[0]['status'] != 'open':
                await interaction.followup.send("A tournament nincs nyitva.", ephemeral=True)
                return
            tournament = tournament_response.data[0]
            players = tournament['players']
            new_players = [p for p in players if p['discord_id'] != interaction.user.id]
            supabase.table('tournaments').update({'players': new_players}).eq('id', tournament_id).execute()
            channel = interaction.channel
            message = await channel.fetch_message(tournament['queue_message_id'])
            embed = message.embeds[0]
            players_list = '\n'.join([f"<@{p['discord_id']}> ({p['minecraft_name']})" for p in new_players]) or 'Még senki'
            new_embed = discord.Embed.from_dict(embed.to_dict())
            new_embed.set_field_at(0, name="Játékosok:", value=players_list)
            await message.edit(embed=new_embed)
            await interaction.followup.send("Kiléptél a tournamentból!", ephemeral=True)

        elif custom_id.startswith('close_ticket_'):
            parts = custom_id.split('_')
            tournament_id = parts[2]
            p1 = parts[3]
            p2 = parts[4]
            try:
                await interaction.response.defer(ephemeral=True)
            except Exception:
                pass
            
            # Get match and delete channel
            match_response = supabase.table('matches').select('ticket_channel_id').eq('tournament_id', tournament_id).eq('player1', p1).eq('player2', p2).execute()
            if match_response.data:
                channel_id = match_response.data[0]['ticket_channel_id']
                channel = await client.fetch_channel(channel_id)
                if channel:
                    try:
                        await channel.delete()
                    except discord.Forbidden:
                        print("Botnak nincs 'Csatornák kezelése' jogosultsága a jegy törléséhez")
                    except Exception as e:
                        print(f"Hiba a csatorna törlésekor: {e}")
            
            await interaction.followup.send("Jegy lezárva.", ephemeral=True)

        elif custom_id.startswith('result_'):
            parts = custom_id.split('_')
            tournament_id = parts[1]
            p1 = parts[2]
            p2 = parts[3]
            try:
                await interaction.response.defer(ephemeral=True)
            except Exception:
                pass
            select = discord.ui.Select(placeholder="Válassz győztest", options=[
                discord.SelectOption(label=f"{p1} nyert", value=f"{p1}_win"),
                discord.SelectOption(label=f"{p2} nyert", value=f"{p2}_win")
            ], custom_id=f"select_winner_{tournament_id}_{p1}_{p2}")
            view = discord.ui.View()
            view.add_item(select)
            await interaction.followup.send("Válaszd ki a győztest:", view=view, ephemeral=True)

        elif custom_id.startswith('select_winner_'):
            parts = custom_id.split('_')
            tournament_id = parts[2]
            p1 = parts[3]
            p2 = parts[4]
            winner = interaction.data['values'][0].split('_')[0]
            modal = ScoreModal(tournament_id, p1, p2, winner)
            await interaction.response.send_modal(modal)

    except APIError as e:
        print(f"Supabase interaction error: {e}")
        try:
            await interaction.response.send_message(f"Hiba történt: {e}", ephemeral=True)
        except:
            try:
                await interaction.followup.send(f"Hiba történt: {e}", ephemeral=True)
            except:
                pass


class ScoreModal(discord.ui.Modal, title="Pontozás"):
    score = discord.ui.TextInput(label="Pontszám (pl. 7-3)", style=discord.TextStyle.short, required=True)

    def __init__(self, tournament_id, p1, p2, winner):
        super().__init__()
        self.tournament_id = tournament_id
        self.p1 = p1
        self.p2 = p2
        self.winner = winner

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        score = self.score.value
        try:
            supabase.table('matches').update({'winner': self.winner, 'score': score}).eq('tournament_id', self.tournament_id).eq('player1', self.p1).eq('player2', self.p2).execute()
        except APIError as e:
            print(f"Nem sikerült frissíteni a mérkőzés eredményét: {e}")
        results_channel = await client.fetch_channel(int(os.getenv('RESULTS_CHANNEL_ID')))
        await results_channel.send(f"{self.p1} vs {self.p2}: {self.winner} nyert {score}-val")
        
        match_response = supabase.table('matches').select('ticket_channel_id').eq('tournament_id', self.tournament_id).eq('player1', self.p1).eq('player2', self.p2).execute()
        if match_response.data:
            channel_id = match_response.data[0]['ticket_channel_id']
            channel = await client.fetch_channel(channel_id)
            if channel:
                try:
                    await channel.delete()
                    print(f"Jegycsatorna törölve: {channel_id}")
                except discord.Forbidden:
                    print("Botnak nincs 'Csatornák kezelése' jogosultsága a jegy törléséhez")
                except Exception as e:
                    print(f"Hiba a csatorna törlésekor: {e}")
        
        await check_round_complete(self.tournament_id)
        await interaction.followup.send("Eredmény elküldve.", ephemeral=True)


async def start_tournament(tournament_id):
    try:
            tournament_response = supabase.table('tournaments').select('*').eq('id', tournament_id).execute()
            if not tournament_response.data or tournament_response.data[0]['status'] != 'open':
                await interaction.followup.send("A tournament nincs nyitva.", ephemeral=True)
                return
    except APIError as e:
        print(f"Error starting tournament: {e}")
        return
    
    try:
        supabase.table('tournaments').update({'status': 'active', 'current_round': 1}).eq('id', tournament_id).execute()
        players = tournament_response.data[0]['players']
        
        if len(players) < 2:
            print(f"Not enough players to start tournament {tournament_id}")
            return
        
        shuffled = players[:]
        import random
        random.shuffle(shuffled)
        matches = []
        for i in range(0, len(shuffled), 2):
            if i + 1 < len(shuffled):
                matches.append({'p1': shuffled[i], 'p2': shuffled[i+1]})
        
        # Update the original tournament embed with round 1 info
        try:
            queue_message_id = tournament_response.data[0].get('queue_message_id')
            if queue_message_id:
                guild = client.get_guild(int(os.getenv('GUILD_ID')))
                if not guild:
                    print("Bot not in guild - check GUILD_ID")
                    return
                for ch in guild.text_channels:
                    try:
                        msg = await ch.fetch_message(queue_message_id)
                        embed = discord.Embed(
                            title=f"{tournament_response.data[0]['name']} Tournament - 1. kör",
                            color=0x00FF00
                        )
                        embed.add_field(name="Játékosok:", value=str(len(players)))
                        matches_text = ""
                        for match in matches:
                            p1 = match['p1']
                            p2 = match['p2']
                            matches_text += f"<@{p1['discord_id']}> vs <@{p2['discord_id']}>\n"
                        embed.add_field(name="Meccsek:", value=matches_text or "Nincsenek meccsek")
                        await msg.edit(embed=embed, view=None)
                        break
                    except Exception as e:
                        continue
        except Exception as e:
            print(f"Failed to update queue message: {e}")
        
        guild = client.get_guild(int(os.getenv('GUILD_ID')))
        if not guild:
            print("Bot not in guild - check GUILD_ID")
            return
        category_id = int(os.getenv('TICKET_CATEGORY_ID'))
        category = guild.get_channel(category_id)
        if not category or not isinstance(category, discord.CategoryChannel):
        print(f"Érvénytelen jegykategória: {category_id}")
        return
    
    for match in matches:
        channel_name = f"t-r1-{match['p1']['minecraft_name']}-{match['p2']['minecraft_name']}"
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, send_messages=False)
        }
            # Bot access using Object ID
            overwrites[discord.Object(id=client.user.id)] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_channels=True,
                manage_permissions=True,
                read_message_history=True
            )
            # Player access using Object IDs (works without member cache)
            overwrites[discord.Object(id=match['p1']['discord_id'])] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            )
            overwrites[discord.Object(id=match['p2']['discord_id'])] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            )
            
            try:
                channel = await guild.create_text_channel(channel_name, category=category, overwrites=overwrites)
            except discord.Forbidden:
                print("Botnak nincs jogosultsága csatornák létrehozására a jegykategóriában")
                return
            except Exception as e:
                print(f"Hiba a csatorna létrehozésekor: {e}")
                return
            
            embed = discord.Embed(title="Tournament 1. kör", description=f"{match['p1']['minecraft_name']} vs {match['p2']['minecraft_name']}\nSok sikert!", color=0x0000FF)
            close_button = discord.ui.Button(label="Jegy lezárása", style=discord.ButtonStyle.danger, custom_id=f"close_ticket_{tournament_id}_{match['p1']['minecraft_name']}_{match['p2']['minecraft_name']}")
            result_button = discord.ui.Button(label="eredmény beírása", style=discord.ButtonStyle.primary, custom_id=f"result_{tournament_id}_{match['p1']['minecraft_name']}_{match['p2']['minecraft_name']}")
            view = discord.ui.View()
            view.add_item(close_button)
            view.add_item(result_button)
            try:
                await channel.send(embed=embed, view=view)
            except Exception as e:
                print(f"Nem sikerült elküldeni az embedet a csatornában: {e}")
                return
            
            try:
                supabase.table('matches').insert({
                    'tournament_id': tournament_id,
                    'round': 1,
                    'player1': match['p1']['minecraft_name'],
                    'player2': match['p2']['minecraft_name'],
                    'ticket_channel_id': channel.id
                }).execute()
            except APIError as e:
                print(f"Nem sikerült beszúrni a mérkőzést: {e}")
    except Exception as e:
        print(f"Unexpected error in start_tournament: {e}")

async def start_round(tournament_id, round_num):
    try:
        tournament_response = supabase.table('tournaments').select('*').eq('id', tournament_id).execute()
        if not tournament_response.data:
            return
    except APIError as e:
        print(f"Error starting round: {e}")
        return
    
    try:
        players = tournament_response.data[0]['players']
        
        if len(players) < 2:
            print(f"Not enough players to start round {round_num}")
            return
        
        # Generate matches
        shuffled = players[:]
        import random
        random.shuffle(shuffled)
        matches = []
        for i in range(0, len(shuffled), 2):
            if i + 1 < len(shuffled):
                matches.append({'p1': shuffled[i], 'p2': shuffled[i+1]})
        
        # Update the original tournament embed with new round info
        try:
            queue_message_id = tournament_response.data[0].get('queue_message_id')
            if queue_message_id:
                guild = client.get_guild(int(os.getenv('GUILD_ID')))
                if not guild:
                    print("Bot not in guild - check GUILD_ID")
                    return
                for ch in guild.text_channels:
                    try:
                        msg = await ch.fetch_message(queue_message_id)
                        embed = discord.Embed(
                            title=f"{tournament_response.data[0]['name']} Tournament - {round_num}. kör",
                            color=0x00FF00
                        )
                        embed.add_field(name="Játékosok:", value=str(len(players)))
                        matches_text = ""
                        for match in matches:
                            p1 = match['p1']
                            p2 = match['p2']
                            matches_text += f"<@{p1['discord_id']}> vs <@{p2['discord_id']}>\n"
                        embed.add_field(name="Meccsek:", value=matches_text or "Nincsenek meccsek")
                        await msg.edit(embed=embed, view=None)
                        break
                    except Exception as e:
                        continue
        except Exception as e:
            print(f"Failed to update queue message: {e}")
        
        guild = client.get_guild(int(os.getenv('GUILD_ID')))
        if not guild:
            print("Bot not in guild - check GUILD_ID")
            return
        category_id = int(os.getenv('TICKET_CATEGORY_ID'))
        category = guild.get_channel(category_id)
        if not category or not isinstance(category, discord.CategoryChannel):
        print(f"Érvénytelen jegykategória: {category_id}")
        return
    
    for match in matches:
        channel_name = f"t-r{round_num}-{match['p1']['minecraft_name']}-{match['p2']['minecraft_name']}"
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, send_messages=False)
        }
            # Bot access using Object ID
            overwrites[discord.Object(id=client.user.id)] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_channels=True,
                manage_permissions=True,
                read_message_history=True
            )
            # Player access using Object IDs (works without member cache)
            overwrites[discord.Object(id=match['p1']['discord_id'])] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            )
            overwrites[discord.Object(id=match['p2']['discord_id'])] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            )
            
            try:
                channel = await guild.create_text_channel(channel_name, category=category, overwrites=overwrites)
            except discord.Forbidden:
                print("Botnak nincs jogosultsága csatornák létrehozására a jegykategóriában")
                return
            except Exception as e:
                print(f"Hiba a csatorna létrehozésekor: {e}")
                return
            
            embed = discord.Embed(title=f"Tournament {round_num}. kör", description=f"{match['p1']['minecraft_name']} vs {match['p2']['minecraft_name']}\nSok sikert!", color=0x0000FF)
            close_button = discord.ui.Button(label="Jegy lezárása", style=discord.ButtonStyle.danger, custom_id=f"close_ticket_{tournament_id}_{match['p1']['minecraft_name']}_{match['p2']['minecraft_name']}")
            result_button = discord.ui.Button(label="eredmény beírása", style=discord.ButtonStyle.primary, custom_id=f"result_{tournament_id}_{match['p1']['minecraft_name']}_{match['p2']['minecraft_name']}")
            view = discord.ui.View()
            view.add_item(close_button)
            view.add_item(result_button)
            try:
                await channel.send(embed=embed, view=view)
            except Exception as e:
                print(f"Nem sikerült elküldeni az embedet a csatornában: {e}")
                return
            
            try:
                supabase.table('matches').insert({
                    'tournament_id': tournament_id,
                    'round': round_num,
                    'player1': match['p1']['minecraft_name'],
                    'player2': match['p2']['minecraft_name'],
                    'ticket_channel_id': channel.id
                }).execute()
            except APIError as e:
                print(f"Nem sikerült beszúrni a mérkőzést: {e}")
    except Exception as e:
        print(f"Unexpected error in start_round: {e}")

async def check_round_complete(tournament_id):
    try:
        tournament_response = supabase.table('tournaments').select('*').eq('id', tournament_id).execute()
        if not tournament_response.data:
            return
        tournament = tournament_response.data[0]
        round_num = tournament['current_round']
        matches_response = supabase.table('matches').select('*').eq('tournament_id', tournament_id).eq('round', round_num).execute()
        all_done = all(m['winner'] for m in matches_response.data)
        if all_done:
            winners = [m['winner'] for m in matches_response.data]
            if len(winners) == 1:
                results_channel = await client.fetch_channel(int(os.getenv('RESULTS_CHANNEL_ID')))
                await results_channel.send(f"Tournament győztes: {winners[0]}")
                supabase.table('tournaments').update({'status': 'finished'}).eq('id', tournament_id).execute()
                return
            winners_with_discord = []
            for w in winners:
                linked_response = supabase.table('linked_accounts').select('discord_id').eq('minecraft_name', w).execute()
                if linked_response.data:
                    winners_with_discord.append({'discord_id': linked_response.data[0]['discord_id'], 'minecraft_name': w})
            supabase.table('tournaments').update({'players': winners_with_discord, 'current_round': round_num + 1}).eq('id', tournament_id).execute()
            await asyncio.sleep(24 * 60 * 60)
            try:
                await start_round(tournament_id, round_num + 1)
            except Exception as e:
                print(f"Error starting next round: {e}")
    except APIError as e:
        print(f"Error in check_round_complete: {e}")

client.run(os.getenv('DISCORD_TOKEN'))