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

    @tasks.loop(seconds=config.auto_start_poll_seconds)
    async def auto_start_loop(self):
        """Háttérfeladat: Automatikus indítás vizsgálata."""
        try:
            pending = await arun(db.list_pending_tournaments)
            for tourney in pending:
                await self._start_round_logic(tourney, round_num=1)
        except Exception as exc:
            log.error("Hiba az auto_start_loop futása közben: %s", exc)

    async def _start_round_logic(self, tourney: dict, round_num: int = 1):
        tourney_id = tourney["id"]
        
        # 1. forduló esetén a regisztrált játékosokat kérjük le
        if round_num == 1:
            raw_players = await arun(db.get_tournament_players, tourney_id)
            players = [p["discord_id"] for p in raw_players if p.get("discord_id")]
        else:
            # Későbbi fordulók: az előző forduló meccseiből gyűjtjük a győzteseket
            unresolved = await arun(db.get_unresolved_matches, tourney_id)
            if unresolved:
                log.warning("Tournament %s még tartalmaz lezáratlan meccseket!", tourney_id)
            # Lekérjük az összes meccset a korábbi fordulóból
            resp = db._client.table("matches").select("*").eq("tournament_id", tourney_id).eq("round_number", round_num - 1).execute()
            prev_matches = resp.data or []
            players = [m["winner_discord_id"] for m in prev_matches if m.get("winner_discord_id")]

        if len(players) < 2:
            if round_num == 1:
                await arun(db.update_tournament, tourney_id, status="cancelled")
                log.info("Tournament %s törölve: nincs elég jelentkező.", tourney_id)
            else:
                await arun(db.update_tournament, tourney_id, status="completed")
                log.info("Tournament %s befejeződött!", tourney_id)
            return

        shuffled = players.copy()
        random.shuffle(shuffled)

        await arun(db.update_tournament, tourney_id, status="running", current_round=round_num)

        guild = self.bot.get_guild(tourney.get("guild_id") or config.guild_id)
        category_id = tourney.get("ticket_category_id") or config.ticket_category_id
        category = guild.get_channel(category_id) if guild and category_id else None

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

            match_data = await arun(
                db.create_match,
                tournament_id=tourney_id,
                round_number=round_num,
                player1_discord_id=p1_id,
                player2_discord_id=p2_id,
                player1_mc=p1_mc,
                player2_mc=p2_mc,
                ticket_channel_id=ticket_channel.id if ticket_channel else 0,
            )

            if ticket_channel:
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

        tourney_data = await arun(
            db.create_tournament,
            name=name,
            end_time=end_time,
            queue_message_id=0,
            guild_id=interaction.guild_id or config.guild_id,
            ticket_category_id=config.ticket_category_id,
            results_channel_id=config.results_channel_id,
            regulator_role_id=config.regulator_role_id,
        )
        tourney_id = tourney_data["id"]

        view = TournamentQueueView(tourney_id)
        embed = discord.Embed(
            title=f"🏆 Bajnokság: {name}",
            description=f"Kattints a **✅ Belépés** gombra a regisztrációhoz!\n\n**Jelentkezési határidő:** <t:{int(end_time.timestamp())}:R>",
            color=discord.Color.blue(),
        )
        msg = await interaction.followup.send(embed=embed, view=view)
        await arun(db.update_tournament, tourney_id, queue_message_id=msg.id)

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
            await interaction.followup.send("ℹ️ Forduló lezárva.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TournamentsCog(bot))
