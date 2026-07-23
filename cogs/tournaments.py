"""NeonTiers Tournament Bot - Bajnokság Cogs modul."""

from __future__ import annotations

import logging
import random
import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import config
from database import arun, db
from views import MatchTicketView, TournamentQueueView

log = logging.getLogger("neontiers.tournaments")


class TournamentsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.auto_start_loop.start()

    def cog_unload(self):
        self.auto_start_loop.cancel()

    @tasks.loop(seconds=15)
    async def auto_start_loop(self):
        """Háttérfeladat: Automatikus indítás vizsgálata."""
        try:
            pending = await arun(db.get_pending_autostart_tournaments)
            for tourney in pending:
                await self._start_round_logic(tourney, round_num=1)
        except Exception as exc:
            log.error("Hiba az auto_start_loop futása közben: %s", exc)

    async def _start_round_logic(self, tourney: dict, round_num: int = 1):
        tourney_id = tourney["id"]
        
        # Ha 1. forduló, a regisztráltakból dolgozunk
        if round_num == 1:
            players = tourney.get("players") or []
        else:
            # Következő forduló: az előző forduló győzteseit kérjük le
            prev_matches = await arun(db.get_matches_for_round, tourney_id, round_num - 1)
            players = [m["winner_discord_id"] for m in prev_matches if m.get("winner_discord_id")]

        if len(players) < 2:
            if round_num == 1:
                await arun(db.update_tournament_status, tourney_id, "cancelled")
                log.info("Tournament %s törölve: nincs elég jelentkező.", tourney_id)
            else:
                await arun(db.update_tournament_status, tourney_id, "completed")
                log.info("Tournament %s befejeződött! Győztes: %s", tourney_id, players)
            return

        shuffled = players.copy()
        random.shuffle(shuffled)

        await arun(db.update_tournament_status, tourney_id, "active")
        await arun(db.update_tournament_round, tourney_id, round_num)

        guild = self.bot.get_guild(tourney.get("guild_id") or config.guild_id)
        category_id = tourney.get("ticket_category_id") or config.ticket_category_id
        category = guild.get_channel(category_id) if guild else None

        for i in range(0, len(shuffled) - 1, 2):
            p1_id = shuffled[i]
            p2_id = shuffled[i + 1]

            p1_acc = await arun(db.get_linked_account, p1_id)
            p2_acc = await arun(db.get_linked_account, p2_id)

            p1_mc = p1_acc.get("minecraft_name", "Ismeretlen") if p1_acc else "Ismeretlen"
            p2_mc = p2_acc.get("minecraft_name", "Ismeretlen") if p2_acc else "Ismeretlen"

            ticket_channel = None
            if guild and isinstance(category, discord.CategoryChannel):
                if len(category.channels) < 50:
                    try:
                        overwrites = {
                            guild.default_role: discord.PermissionOverwrite(read_messages=False),
                            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                        }
                        u1 = guild.get_member(p1_id)
                        u2 = guild.get_member(p2_id)
                        if u1: overwrites[u1] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
                        if u2: overwrites[u2] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

                        ticket_channel = await category.create_text_channel(
                            name=f"r{round_num}-{p1_mc}-vs-{p2_mc}",
                            overwrites=overwrites
                        )
                    except discord.HTTPException as exc:
                        log.error("Discord API Hiba a csatorna létrehozásakor: %s", exc)

            match_id = await arun(
                db.create_match,
                tournament_id=tourney_id,
                round_num=round_num,
                p1_id=p1_id,
                p2_id=p2_id,
                p1_mc=p1_mc,
                p2_mc=p2_mc,
                ticket_channel_id=ticket_channel.id if ticket_channel else None,
            )

            if ticket_channel:
                match_data = {"id": match_id, "player1_discord_id": p1_id, "player2_discord_id": p2_id, "player1_mc": p1_mc, "player2_mc": p2_mc}
                embed = discord.Embed(
                    title=f"⚔️ {round_num}. Forduló: {p1_mc} vs {p2_mc}",
                    description="Készüljetek fel a küzdelemre! Az eredményt a lenti gombbal rögzíthetitek.",
                    color=discord.Color.gold(),
                )
                await ticket_channel.send(
                    content=f"<@{p1_id}> <@{p2_id}>",
                    embed=embed,
                    view=MatchTicketView(match_data, tourney)
                )

    # ==========================================
    # SLASH PARANCSOK
    # ==========================================

    @app_commands.command(name="tournamentqueue", description="Új bajnokság regisztráció nyitása.")
    async def tournamentqueue(self, interaction: discord.Interaction, name: str, minutes: int):
        await interaction.response.defer()
        end_time = discord.utils.utcnow() + discord.utils.timedelta(minutes=minutes)

        tourney_id = await arun(
            db.create_tournament,
            name=name,
            end_time=end_time.isoformat(),
            guild_id=interaction.guild_id,
        )

        view = TournamentQueueView(tourney_id)
        embed = discord.Embed(
            title=f"🏆 Bajnokság: {name}",
            description=f"Kattints a **✅ Belépés** gombra a regisztrációhoz!\n\n**Jelentkezési határidő:** <t:{int(end_time.timestamp())}:R>",
            color=discord.Color.blue(),
        )
        msg = await interaction.followup.send(embed=embed, view=view)
        await arun(db.update_queue_message_id, tourney_id, msg.id)

    @app_commands.command(name="tournamentround", description="Forduló kézi indítása vagy kezelése.")
    @app_commands.choices(action=[
        app_commands.Choice(name="start", value="start"),
        app_commands.Choice(name="close", value="close")
    ])
    async def tournamentround(
        self, 
        interaction: discord.Interaction, 
        action: app_commands.Choice[str], 
        tournament_id: str, 
        round_number: int
    ):
        await interaction.response.defer(ephemeral=True)

        tourney = await arun(db.get_tournament, tournament_id)
        if not tourney:
            await interaction.followup.send("❌ Nem található bajnokság ezzel az ID-val!", ephemeral=True)
            return

        if action.value == "start":
            await self._start_round_logic(tourney, round_num=round_number)
            await interaction.followup.send(f"✅ A(z) **{round_number}. forduló** sikeresen elindítva!", ephemeral=True)
        else:
            await interaction.followup.send(f"ℹ️ Forduló lezárása bejegyezve.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TournamentsCog(bot))
