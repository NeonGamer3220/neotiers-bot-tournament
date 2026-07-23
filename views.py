"""NeonTiers Tournament Bot - Discord Interaktív UI elemek."""

from __future__ import annotations

import logging
import discord
from discord import ui

from config import config
from database import arun, db
from utils import generate_link_code

log = logging.getLogger("neontiers.views")


class ResultModal(ui.Modal, title="Meccs Eredmény Beírása"):
    winner_input = ui.TextInput(
        label="Győztes Minecraft Neve vagy Discord ID-ja",
        placeholder="Pl. Player123 vagy 123456789012345678",
        required=True,
    )
    score_input = ui.TextInput(
        label="Eredmény (Opcionális)",
        placeholder="Pl. 3-1",
        required=False,
    )

    def __init__(self, match_data: dict, tournament_data: dict):
        super().__init__()
        self.match_data = match_data
        self.tournament_data = tournament_data

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        winner_raw = self.winner_input.value.strip()

        p1_id = self.match_data.get("player1_discord_id")
        p2_id = self.match_data.get("player2_discord_id")
        p1_mc = (self.match_data.get("player1_mc") or "").lower()
        p2_mc = (self.match_data.get("player2_mc") or "").lower()

        winner_id = None
        if winner_raw.isdigit() and int(winner_raw) in (p1_id, p2_id):
            winner_id = int(winner_raw)
        elif winner_raw.lower() == p1_mc:
            winner_id = p1_id
        elif winner_raw.lower() == p2_mc:
            winner_id = p2_id

        if not winner_id:
            await interaction.followup.send(
                "❌ Érvénytelen győztes! Csak a meccsben résztvevő két játékos egyikét adhatod meg.",
                ephemeral=True,
            )
            return

        await arun(db.set_match_winner, self.match_data["id"], winner_id)

        results_channel_id = self.tournament_data.get("results_channel_id") or config.results_channel_id
        if results_channel_id and interaction.guild:
            ch = interaction.guild.get_channel(results_channel_id)
            if isinstance(ch, discord.TextChannel):
                p1_mention = f"<@{p1_id}>" if p1_id else "Ismeretlen"
                p2_mention = f"<@{p2_id}>" if p2_id else "Ismeretlen"
                w_mention = f"<@{winner_id}>"
                score_str = f" ({self.score_input.value.strip()})" if self.score_input.value else ""

                embed = discord.Embed(
                    title=f"🏆 Meccs Eredmény - {self.tournament_data.get('name', 'Tournament')}",
                    description=f"{p1_mention} vs {p2_mention}\n\n**Győztes:** {w_mention}{score_str}",
                    color=discord.Color.green(),
                )
                await ch.send(embed=embed)

        await interaction.followup.send("✅ Eredmény sikeresen rögzítve!", ephemeral=True)


class MatchTicketView(ui.View):
    def __init__(self, match_data: dict, tournament_data: dict):
        super().__init__(timeout=None)
        self.match_data = match_data
        self.tournament_data = tournament_data

    @ui.button(label="📝 Eredmény Beírása", style=discord.ButtonStyle.primary, custom_id="match_submit_result")
    async def submit_result(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(ResultModal(self.match_data, self.tournament_data))

    @ui.button(label="🔒 Jegy Lezárása", style=discord.ButtonStyle.danger, custom_id="match_close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message("🔒 Csatorna törlése 5 másodpercen belül...", ephemeral=False)
        await discord.utils.sleep_until(discord.utils.utcnow() + discord.utils.timedelta(seconds=5))
        if interaction.channel:
            await interaction.channel.delete(reason="Match ticket zárolva.")


class TournamentQueueView(ui.View):
    def __init__(self, tournament_id: str):
        super().__init__(timeout=None)
        self.tournament_id = tournament_id

    @ui.button(label="✅ Belépés", style=discord.ButtonStyle.success, custom_id="tournament_join")
    async def join_tournament(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id

        linked = await arun(db.get_linked_account, user_id)
        if not linked:
            code = generate_link_code()
            await arun(db.create_pending_code, user_id, code, config.pending_code_ttl_minutes)
            await interaction.followup.send(
                f"❌ A fiókod nincs összekapcsolva!\n\n"
                f"Lépj fel a Minecraft szerverre (`chaosffa.kinetic.host`), és írd be ezt a parancsot:\n"
                f"```/link {code}```",
                ephemeral=True,
            )
            return

        mc_name = linked.get("minecraft_name", "Ismeretlen")
        added = await arun(db.add_player_to_tournament, self.tournament_id, user_id, mc_name)
        if added:
            await interaction.followup.send("✅ Sikeresen regisztráltál a bajnokságra!", ephemeral=True)
        else:
            await interaction.followup.send("⚠️ Már regisztráltál erre a bajnokságra!", ephemeral=True)

    @ui.button(label="❌ Kilépés", style=discord.ButtonStyle.danger, custom_id="tournament_leave")
    async def leave_tournament(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True)
        removed = await arun(db.remove_player_from_tournament, self.tournament_id, interaction.user.id)
        if removed:
            await interaction.followup.send("✅ Sikeresen kiléptél a bajnokságból.", ephemeral=True)
        else:
            await interaction.followup.send("⚠️ Nem voltál regisztrálva erre a bajnokságra.", ephemeral=True)
