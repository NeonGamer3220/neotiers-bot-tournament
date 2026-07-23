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


def _to_int(val) -> int:
    try:
        return int(val) if val is not None else 0
    except (ValueError, TypeError):
        return 0


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

    async def _get_member_safe(self, guild: discord.Guild, user_id: int) -> discord.Member | None:
        """Biztonságosan lekéri a Guild Tagot megbízhatóan (cache-ből vagy API-ból)."""
        if not guild or not user_id:
            return None
        member = guild.get_member(user_id)
        if not member:
            try:
                member = await guild.fetch_member(user_id)
            except discord.HTTPException:
                member = None
        return member

    async def _get_prev_round_winners(self, tournament_id: str, round_num: int) -> list[dict]:
        """Lekéri az előző forduló győzteseinek adatait."""
        def _fetch():
            resp = db._client.table("matches").select("*").eq("tournament_id", tournament_id).eq("round_number", round_num - 1).execute()
            if not resp or not resp.data:
                return []
            
            reg_players = db.get_tournament_players(tournament_id)
            player_map = {_to_int(p["discord_id"]): p.get("minecraft_name") for p in reg_players if p.get("discord_id")}

            winners = []
            for match in resp.data:
                w_id = _to_int(match.get("winner_discord_id"))
                if not w_id:
                    continue
                
                p1_id = _to_int(match.get("player1_discord_id"))
                if w_id == p1_id:
                    mc_name = match.get("player1_mc")
                else:
                    mc_name = match.get("player2_mc")
                
                if not mc_name or mc_name == "Ismeretlen":
                    mc_name = player_map.get(w_id, "")

                winners.append({"discord_id": w_id, "minecraft_name": mc_name})
            return winners

        return await arun(_fetch)

    async def _start_round_logic(self, tourney: dict, round_num: int = 1) -> str:
        tourney_id = tourney["id"]
        
        if round_num == 1:
            raw_players = await arun(db.get_tournament_players, tourney_id)
            players = []
            for p in raw_players:
                d_id = _to_int(p.get("discord_id"))
                if d_id > 0:
                    players.append({"discord_id": d_id, "minecraft_name": p.get("minecraft_name", "")})
        else:
            unresolved = await arun(db.get_unresolved_matches, tourney_id)
            if unresolved:
                return f"⚠️ Még {len(unresolved)} meccs nincs lezárva ebben a fordulóban! Előbb rögzítsétek az eredményeket."
            
            players = await self._get_prev_round_winners(tourney_id, round_num)

        if len(players) < 2:
            if round_num == 1:
                await arun(db.update_tournament, tourney_id, status="cancelled")
                return "❌ A bajnokság törölve lett, mert nincs elég regisztrált játékos (min. 2 fő)."
            else:
                await arun(db.update_tournament, tourney_id, status="completed")
                if len(players) == 1:
                    w = players[0]
                    return f"🏆 A bajnokság véget ért! A győztes: <@{w['discord_id']}> ({w['minecraft_name'] or 'Győztes'})!"
                return "❌ Nincs elég győztes a következő forduló elindításához."

        shuffled = players.copy()
        random.shuffle(shuffled)

        await arun(db.update_tournament, tourney_id, status="running", current_round=round_num)

        guild = self.bot.get_guild(tourney.get("guild_id") or config.guild_id)
        category_id = tourney.get("ticket_category_id") or config.ticket_category_id
        category = guild.get_channel(category_id) if guild and category_id else None

        created_matches = 0
        for i in range(0, len(shuffled) - 1, 2):
            p1 = shuffled[i]
            p2 = shuffled[i + 1]

            p1_id = _to_int(p1["discord_id"])
            p2_id = _to_int(p2["discord_id"])

            # Lekérjük a tagokat az API-ból/Cache-ből
            u1 = await self._get_member_safe(guild, p1_id)
            u2 = await self._get_member_safe(guild, p2_id)

            p1_mc = p1.get("minecraft_name") or (u1.display_name if u1 else f"Player_{p1_id}")
            p2_mc = p2.get("minecraft_name") or (u2.display_name if u2 else f"Player_{p2_id}")

            ticket_channel = None
            if guild and isinstance(category, discord.CategoryChannel):
                if len(category.channels) < 50:
                    try:
                        # Alapvető láthatóság tiltása mindenkinek, csak a bot és a 2 játékos láthatja
                        overwrites = {
                            guild.default_role: discord.PermissionOverwrite(read_messages=False, send_messages=False),
                            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True),
                        }
                        
                        # Hozzáadjuk a 2 ellenfelet a csatornához!
                        if u1:
                            overwrites[u1] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
                        if u2:
                            overwrites[u2] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

                        clean_p1 = "".join(c for c in p1_mc if c.isalnum() or c in "-_")
                        clean_p2 = "".join(c for c in p2_mc if c.isalnum() or c in "-_")
                        
                        ticket_channel = await category.create_text_channel(
                            name=f"r{round_num}-{clean_p1[:10]}-vs-{clean_p2[:10]}",
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

            created_matches += 1

            if ticket_channel:
                embed = discord.Embed(
                    title=f"⚔️ {round_num}. Forduló: {p1_mc} vs {p2_mc}",
                    description="Készüljetek fel a küzdelemre! Az eredményt a lenti gombbal rögzíthetitek.",
                    color=discord.Color.gold(),
                )
                # Meghívjuk őket a csatornába egy pingszóval
                await ticket_channel.send(
                    content=f"🔔 <@{p1_id}> vs <@{p2_id}>",
                    embed=embed,
                    view=MatchTicketView(match_data, tourney)
                )

        return f"✅ A(z) **{round_num}. forduló** elindult! ({created_matches} meccs/ticket létrehozva)."

    # ==========================================
    # SLASH PARANCSOK
    # ==========================================

    @app_commands.command(name="tournamentqueue", description="Új bajnokság regisztráció nyitása.")
    async def tournamentqueue(self, interaction: discord.Interaction, name: str, minutes: int):
        try:
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
        except Exception as exc:
            log.error("Hiba a tournamentqueue parancsnál: %s", exc)
            await interaction.followup.send(f"❌ Hiba történt: `{exc}`", ephemeral=True)

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
        try:
            await interaction.response.defer(ephemeral=True)

            tourney = await arun(db.get_tournament, tournament_id)
            if not tourney:
                await interaction.followup.send("❌ Nem található bajnokság ezzel az ID-val!", ephemeral=True)
                return

            if action.value == "start":
                msg = await self._start_round_logic(tourney, round_num=round_number)
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.followup.send("ℹ️ Forduló lezárva.", ephemeral=True)
        except Exception as exc:
            log.error("Hiba a tournamentround parancsnál: %s", exc)
            await interaction.followup.send(f"❌ Hiba történt: `{exc}`", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TournamentsCog(bot))
