"""NeonTiers Tournament Bot - Bajnokság Cogs modul."""

from __future__ import annotations

import logging
import random
from datetime import datetime, timezone, timedelta
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
        self.auto_close_inactive_loop.start()

    def cog_unload(self):
        self.auto_start_loop.cancel()
        self.auto_close_inactive_loop.cancel()

    # ==========================================
    # HÁTTÉRFELADATOK (LOOPS)
    # ==========================================

    @tasks.loop(seconds=config.auto_start_poll_seconds)
    async def auto_start_loop(self):
        """Háttérfeladat: Automatikus indítás vizsgálata."""
        try:
            pending = await arun(db.list_pending_tournaments)
            for tourney in pending:
                await self._start_round_logic(tourney, round_num=1)
        except Exception as exc:
            log.error("Hiba az auto_start_loop futása közben: %s", exc)

    @tasks.loop(minutes=15)
    async def auto_close_inactive_loop(self):
        """Háttérfeladat: 24 órája inaktív meccsek automatikus 0-0 FF lezárása."""
        try:
            # Lekérjük az összes nyitott meccset
            running_tourneys = await arun(
                lambda: db._client.table("tournaments").select("id").eq("status", "running").execute().data or []
            )
            for t in running_tourneys:
                unresolved = await arun(db.get_unresolved_matches, t["id"])
                for match in unresolved:
                    ticket_id = _to_int(match.get("ticket_channel_id"))
                    if not ticket_id:
                        continue
                    
                    channel = self.bot.get_channel(ticket_id)
                    if isinstance(channel, discord.TextChannel):
                        # Megkeressük az utolsó emberi üzenetet
                        last_human_msg = None
                        async for msg in channel.history(limit=50):
                            if not msg.author.bot:
                                last_human_msg = msg
                                break
                        
                        now = datetime.now(timezone.utc)
                        cutoff = now - timedelta(hours=24)
                        
                        # Ha nincs emberi üzenet, a csatorna létrehozási idejét vesszük alapul
                        ref_time = last_human_msg.created_at if last_human_msg else channel.created_at
                        
                        if ref_time < cutoff:
                            log.info("Meccs inaktivitás miatt lezárva (24h+): match_id=%s", match["id"])
                            embed = discord.Embed(
                                title="⏰ Automatikus Lezárás (Inaktivitás)",
                                description="Mivel 24 órája nem érkezett emberi üzenet a csatornában, a meccs **0 - 0** eredménnyel zárult, és mindkét játékos **FF (Forfeit)** státuszt kapott.",
                                color=discord.Color.red()
                            )
                            await channel.send(embed=embed)
                            # Rögzítjük az adatbázisban a döntetlent/dupla FF-et (winner = 0)
                            await arun(db.set_match_winner, match["id"], 0)
                            await asyncio.sleep(5)
                            try:
                                await channel.delete(reason="24 órás inaktivitás miatti automatikus törlés")
                            except discord.HTTPException:
                                pass
        except Exception as exc:
            log.error("Hiba az auto_close_inactive_loop futása közben: %s", exc)

    # ==========================================
    # SEGGÉDFÜGGVÉNYEK
    # ==========================================

    async def _get_member_safe(self, guild: discord.Guild, user_id: int) -> discord.Member | None:
        """Biztonságos tag lekérés (Cache / Discord API fallback)."""
        if not guild or not user_id:
            return None
        member = guild.get_member(user_id)
        if not member:
            try:
                member = await guild.fetch_member(user_id)
            except discord.HTTPException:
                member = None
        return member

    async def _get_player_info(self, discord_id: int) -> tuple[int, str]:
        """Közvetlenül a linked_accounts táblából kérdezi le a játékos adatait."""
        linked = await arun(db.get_linked_account, discord_id)
        if linked and linked.get("minecraft_name"):
            return discord_id, linked["minecraft_name"]
        return discord_id, f"Player_{discord_id}"

    async def _get_prev_round_winners(self, tournament_id: str, round_num: int) -> list[dict]:
        """Lekéri az előző forduló győzteseinek adatait."""
        def _fetch():
            resp = db._client.table("matches").select("*").eq("tournament_id", tournament_id).eq("round_number", round_num - 1).execute()
            if not resp or not resp.data:
                return []
            
            winners = []
            for match in resp.data:
                w_id = _to_int(match.get("winner_discord_id"))
                if not w_id or w_id == 0:
                    continue
                
                p1_id = _to_int(match.get("player1_discord_id"))
                mc_name = match.get("player1_mc") if w_id == p1_id else match.get("player2_mc")
                
                winners.append({"discord_id": w_id, "minecraft_name": mc_name})
            return winners

        return await arun(_fetch)

    async def _start_round_logic(self, tourney: dict, round_num: int = 1) -> str:
        tourney_id = tourney["id"]
        tourney_name = tourney.get("name", "Bajnokság")
        
        if round_num == 1:
            raw_players = await arun(db.get_tournament_players, tourney_id)
            players = []
            for p in raw_players:
                d_id = _to_int(p.get("discord_id"))
                if d_id > 0:
                    _, mc_name = await self._get_player_info(d_id)
                    players.append({"discord_id": d_id, "minecraft_name": mc_name})
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
                    return f"🏆 A bajnokság véget ért! A győztes: <@{w['discord_id']}> (`{w['minecraft_name']}`)!"
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

            u1 = await self._get_member_safe(guild, p1_id)
            u2 = await self._get_member_safe(guild, p2_id)

            p1_mc = p1.get("minecraft_name") or (u1.display_name if u1 else f"Player_{p1_id}")
            p2_mc = p2.get("minecraft_name") or (u2.display_name if u2 else f"Player_{p2_id}")

            ticket_channel = None
            if guild and isinstance(category, discord.CategoryChannel):
                if len(category.channels) < 50:
                    try:
                        overwrites = {
                            guild.default_role: discord.PermissionOverwrite(read_messages=False, send_messages=False),
                            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True),
                        }
                        
                        if u1:
                            overwrites[u1] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
                        if u2:
                            overwrites[u2] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

                        clean_p1 = "".join(c for c in p1_mc if c.isalnum() or c in "-_")
                        clean_p2 = "".join(c for c in p2_mc if c.isalnum() or c in "-_")
                        
                        ticket_channel = await category.create_text_channel(
                            name=f"r{round_num}-{clean_p1[:8]}-vs-{clean_p2[:8]}",
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
                # ==========================================
                # EXACT CUSTOM EMBED & TEXT FORMATTING
                # ==========================================
                content_text = f"<@{p1_id}> <@{p2_id}> elindult a meccsetek. Pingeljétek egymást is, hogy minél gyorsabban le tudjátok játszani."

                embed = discord.Embed(
                    title=f"{tourney_name} - {round_num}. kör",
                    description=(
                        "Regulator használhatja az Eredmény, FF és Bezárás gombokat.\n"
                        "Az FF modalban 0 = ff, 1 = nem ff.\n"
                        "Ha 24 óráig nincs emberi üzenet a csatornában, a meccs automatikusan 0-0 és mindkét játékos ff lesz."
                    ),
                    color=discord.Color.gold()
                )

                embed.add_field(
                    name="Párosítás",
                    value=f"<@{p1_id}> vs <@{p2_id}>",
                    inline=False
                )

                embed.add_field(
                    name="In-game nevek",
                    value=f"`{p1_mc}` vs `{p2_mc}`",
                    inline=False
                )

                await ticket_channel.send(
                    content=content_text,
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
                title=f"🏆 Bajnokság Regisztráció: {name}",
                description=(
                    f"Kattints a **✅ Belépés** gombra a jelentkezéshez!\n"
                    f"⚠️ *Kizárólag összekapcsolt Minecraft fiókkal tudsz jelentkezni!*\n\n"
                    f"**Jelentkezési határidő:** <t:{int(end_time.timestamp())}:R>"
                ),
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
