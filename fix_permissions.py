import re

with open('index.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Pattern 1: In tournamentround function - after channel creation, add explicit permission setting
# Find the block:
#   try:
#       channel = await guild.create_text_channel(...)
#   except discord.Forbidden:
#       await interaction.followup.send(...)
#       return
# We want to insert permission setting inside try after channel creation.

# Let's do simple string replacements for each location.

# Location 1 (tournamentround):
old1 = '''            try:
                channel = await guild.create_text_channel(channel_name, category=category, overwrites=overwrites)
            except discord.Forbidden:
                await interaction.followup.send("A botnak nincs jogosultsága csatornák létrehozására ebben a kategóriában.", ephemeral=True)
                return
            except Exception as e:
                print(f"Hiba a csatorna létrehozésekor: {e}")
                return
            
            embed = discord.Embed('''

new1 = '''            try:
                channel = await guild.create_text_channel(channel_name, category=category, overwrites=overwrites)
                # Explicitly ensure player permissions after creation
                for player_id in [match['p1']['discord_id'], match['p2']['discord_id']]:
                    try:
                        await channel.set_permissions(
                            discord.Object(id=player_id),
                            view_channel=True,
                            send_messages=True,
                            read_message_history=True
                        )
                    except Exception as e:
                        print(f"Could not set permissions for player {player_id}: {e}")
            except discord.Forbidden:
                await interaction.followup.send("A botnak nincs jogosultsága csatornák létrehozására ebben a kategóriában.", ephemeral=True)
                return
            except Exception as e:
                print(f"Hiba a csatorna létrehozésekor: {e}")
                return
            
            embed = discord.Embed('''

if old1 in content:
    content = content.replace(old1, new1, 1)
    print("Fixed location 1 (tournamentround)")
else:
    print("Location 1 pattern not found")

# Location 2 (start_tournament):
old2 = '''            try:
                channel = await guild.create_text_channel(channel_name, category=category, overwrites=overwrites)
            except discord.Forbidden:
                print("Botnak nincs jogosultsága csatornák létrehozására a jegykategóriában")
                return
            except Exception as e:
                print(f"Hiba a csatorna létrehozésekor: {e}")
                return
            
            embed = discord.Embed(title="Tournament 1. kör",'''

new2 = '''            try:
                channel = await guild.create_text_channel(channel_name, category=category, overwrites=overwrites)
                # Explicitly ensure player permissions after creation
                for player_id in [match['p1']['discord_id'], match['p2']['discord_id']]:
                    try:
                        await channel.set_permissions(
                            discord.Object(id=player_id),
                            view_channel=True,
                            send_messages=True,
                            read_message_history=True
                        )
                    except Exception as e:
                        print(f"Could not set permissions for player {player_id}: {e}")
            except discord.Forbidden:
                print("Botnak nincs jogosultsága csatornák létrehozására a jegykategóriában")
                return
            except Exception as e:
                print(f"Hiba a csatorna létrehozésekor: {e}")
                return
            
            embed = discord.Embed(title="Tournament 1. kör",'''

if old2 in content:
    content = content.replace(old2, new2, 1)
    print("Fixed location 2 (start_tournament)")
else:
    print("Location 2 pattern not found")

# Location 3 (start_round):
old3 = '''            try:
                channel = await guild.create_text_channel(channel_name, category=category, overwrites=overwrites)
            except discord.Forbidden:
                print("Botnak nincs jogosultsága csatornák létrehozására a jegykategóriában")
                return
            except Exception as e:
                print(f"Hiba a csatorna létrehozésekor: {e}")
                return
            
            embed = discord.Embed(title=f"Tournament {round_num}. kör",'''

new3 = '''            try:
                channel = await guild.create_text_channel(channel_name, category=category, overwrites=overwrites)
                # Explicitly ensure player permissions after creation
                for player_id in [match['p1']['discord_id'], match['p2']['discord_id']]:
                    try:
                        await channel.set_permissions(
                            discord.Object(id=player_id),
                            view_channel=True,
                            send_messages=True,
                            read_message_history=True
                        )
                    except Exception as e:
                        print(f"Could not set permissions for player {player_id}: {e}")
            except discord.Forbidden:
                print("Botnak nincs jogosultsága csatornák létrehozására a jegykategóriában")
                return
            except Exception as e:
                print(f"Hiba a csatorna létrehozésekor: {e}")
                return
            
            embed = discord.Embed(title=f"Tournament {round_num}. kör",'''

if old3 in content:
    content = content.replace(old3, new3, 1)
    print("Fixed location 3 (start_round)")
else:
    print("Location 3 pattern not found")

with open('index.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Done")