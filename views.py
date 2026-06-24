"""Discord UI views and modals used by NeonTiers Tournament bot."""
from __future__ import annotations

from datetime import datetime

import discord
from discord import ui

from config import config
from database import db
from utils import (
    format_discord_timestamp,
    format_relative_timestamp,
    generate_code,
)


# ===================================================================== #
# Queue message: Join / Leave buttons
# ===================================================================== #
class QueueJoinView(ui.View):
    """Persistent view attached to the tournament queue message.

    Buttons use the convention:
        join_tournament_<tournament_id>
        leave_tournament_<tournament_id>

    Note: We build the Button instances explicitly in __init__ (instead of
    using the @ui.button decorator) because persistent views (timeout=None)
    require every button to carry its custom_id at construction time. The
    decorator pattern does not let us stamp a per-tournament custom_id onto
    the class-level button descriptor, so Discord would drop the buttons
    when the view was sent.
    """

    def __init__(self, tournament_id: str) -> None:
        super().__init__(timeout=None)
        self.tournament_id = tournament_id

        join_btn = ui.Button(
            label="Belépés a tournamentbe",
            style=discord.ButtonStyle.success,
            emoji="✅",
            custom_id=f"join_tournament_{tournament_id}",
        )
        leave_btn = ui.Button(
            label="Kilépés a tournamentből",
            style=discord.ButtonStyle.danger,
            emoji="❌",
            custom_id=f"leave_tournament_{tournament_id}",
        )
        join_btn.callback = self._join_callback
        leave_btn.callback = self._leave_callback
        self.add_item(join_btn)
        self.add_item(leave_btn)

    async def _join_callback(self, interaction: discord.Interaction) -> None:
        await handle_join(interaction, self.tournament_id)

    async def _leave_callback(self, interaction: discord.Interaction) -> None:
        await handle_leave(interaction, self.tournament_id)


async def handle_join(interaction: discord.Interaction, tournament_id: str) -> None:
    """Handle a player clicking the Join button.

    - If they have a linked account, add them to the tournament.
    - Otherwise, mint a one-time code, store it in pending_codes, DM the user
      with instructions, and ask them to click Join again after linking.
    """
    # Acknowledge fast — we may do DB work and DM the user
    await interaction.response.defer(ephemeral=True, thinking=True)

    tournament = db.get_tournament(tournament_id)
    if not tournament:
        await interaction.followup.send(
            "Ez a turné már nem elérhető.", ephemeral=True
        )
        return
    if tournament.get("status") != "queued":
        await interaction.followup.send(
            "Ez a turné már elindult, nem lehet csatlakozni.", ephemeral=True
        )
        return

    discord_id = interaction.user.id

    # 1) Check linked account
    linked = db.get_linked_account(discord_id)
    if not linked:
        code = generate_code()
        db.create_pending_code(
            discord_id=discord_id,
            code=code,
            ttl_minutes=config.pending_code_ttl_minutes,
        )
        try:
            await interaction.user.send(
                "Még nincs összekapcsolt Minecraft fiókod ehhez a Discord fiókhoz.\n\n"
                f"A te titkos kódod: **`{code}`**\n"
                "Lépj be a **chaosffa.kinetic.host** Minecraft szerverre, "
                f"és használd a `/link {code}` parancsot (vadd a szerver által meghatározott parancsot).\n"
                f"A kód {config.pending_code_ttl_minutes} percig érvényes.\n\n"
                "Miután sikeresen összekapcsoltad a fiókodat, kattints újra a "
                "**Csatlakozás** gombra a turnéra."
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "Nincs összekapcsolt Minecraft fiókod, és nem tudok neked DM-et küldeni. "
                "Kérlek engedélyezd a privát üzeneteket ezen a szerveren, "
                "majd kattints újra a Csatlakozás gombra.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            "Nincs összekapcsolt fiókod — elküldtem a titkos kódot DM-ben. "
            "Kövesd az utasításokat, majd kattints újra a Csatlakozás gombra.",
            ephemeral=True,
        )
        return

    minecraft_name = linked.get("minecraft_name") or "ismeretlen"
    added = db.add_player_to_tournament(tournament_id, discord_id, minecraft_name)
    if not added:
        await interaction.followup.send(
            "Már csatlakoztál ehhez a turnéhoz.", ephemeral=True
        )
        return

    await interaction.followup.send(
        f"Sikeresen csatlakoztál a **{tournament['name']}** turnéhoz, "
        f"Minecraft név: **{minecraft_name}**.",
        ephemeral=True,
    )

    # Best-effort: refresh the queue embed to show the new player count
    await refresh_queue_embed(interaction, tournament)


async def handle_leave(interaction: discord.Interaction, tournament_id: str) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)

    tournament = db.get_tournament(tournament_id)
    if not tournament:
        await interaction.followup.send("Ez a turné már nem elérhető.", ephemeral=True)
        return

    removed = db.remove_player_from_tournament(tournament_id, interaction.user.id)
    if not removed:
        await interaction.followup.send(
            "Nem voltál regisztrálva erre a turnéra.", ephemeral=True
        )
        return

    await interaction.followup.send(
        f"Sikeresen kiléptél a **{tournament['name']}** turnéból.", ephemeral=True
    )
    await refresh_queue_embed(interaction, tournament)


async def refresh_queue_embed(interaction: discord.Interaction, tournament: dict) -> None:
    """Re-render the queue message with updated player list.

    Since the tournaments table does not store queue_channel_id, we use the
    channel of the interaction (the queue channel) to look up the message.
    """
    if interaction.channel is None or not isinstance(interaction.channel, discord.TextChannel):
        return
    try:
        message = await interaction.channel.fetch_message(
            int(tournament["queue_message_id"])
        )
    except (discord.HTTPException, ValueError, KeyError):
        return

    players = db.get_tournament_players(tournament["id"])
    embed = build_queue_embed(tournament, players)
    view = QueueJoinView(tournament["id"])
    try:
        await message.edit(embed=embed, view=view)
    except discord.HTTPException:
        pass


def build_queue_embed(tournament: dict, players: list[dict]) -> discord.Embed:
    # end_time = queue phase end = round 1 auto-start trigger.
    end_time_raw = tournament.get("end_time")
    end_time_dt: datetime | None = None
    if end_time_raw:
        try:
            end_time_dt = datetime.fromisoformat(
                end_time_raw.replace("Z", "+00:00")
            )
        except Exception:
            end_time_dt = None

    if end_time_dt:
        starts_text = (
            f"**Indulás:** {format_discord_timestamp(end_time_dt)} "
            f"({format_relative_timestamp(end_time_dt)})"
        )
    else:
        starts_text = "**Indulás:** ismeretlen"

    embed = discord.Embed(
        title=f"{tournament['name']} Tournament",
        description=starts_text,
        color=0x00E5FF,
    )
    embed.add_field(
        name="Játékosok",
        value=str(len(players)) if players else "0",
        inline=True,
    )
    if players:
        roster = "\n".join(
            f"<@{p['discord_id']}> — {p['minecraft_name']}" for p in players[:25]
        )
        embed.add_field(name="Regisztrált játékosok", value=roster, inline=False)
    embed.set_footer(text="Kattints a Belépés a tournamentbe gombra a jelentkezéshez.")
    return embed


# ===================================================================== #
# Ticket view: Close / Result buttons
# ===================================================================== #
class TicketActionView(ui.View):
    """Persistent view attached to each match ticket message.

    Buttons use the convention:
        close_ticket_<match_id>
        result_<match_id>

    Built explicitly in __init__ (same reason as QueueJoinView) so each
    match's buttons carry a unique persistent custom_id.
    """

    def __init__(self, match_id: str) -> None:
        super().__init__(timeout=None)
        self.match_id = match_id

        close_btn = ui.Button(
            label="Jegy lezárása",
            style=discord.ButtonStyle.secondary,
            emoji="🔒",
            custom_id=f"close_ticket_{match_id}",
        )
        result_btn = ui.Button(
            label="Eredmény beírása",
            style=discord.ButtonStyle.primary,
            emoji="📝",
            custom_id=f"result_{match_id}",
        )
        close_btn.callback = self._close_callback
        result_btn.callback = self._result_callback
        self.add_item(close_btn)
        self.add_item(result_btn)

    async def _close_callback(self, interaction: discord.Interaction) -> None:
        if config.regulator_role_id not in {r.id for r in interaction.user.roles}:
            await interaction.response.send_message(
                "Ezt csak a Regulator szerepkörrel rendelkező tagok használhatják.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(thinking=True)
        channel = interaction.channel
        if isinstance(channel, discord.Thread):
            await channel.edit(archived=True, locked=True)
        elif isinstance(channel, discord.TextChannel):
            for member in channel.members:
                if member.id != interaction.client.user.id:
                    try:
                        await channel.set_permissions(member, view_channel=False, send_messages=False)
                    except discord.HTTPException:
                        pass
            await channel.send("🎟️ Jegy lezárva.")
            try:
                await channel.edit(name=f"closed-{channel.name}")
            except discord.HTTPException:
                pass
        await interaction.followup.send("A jegy le lett zárva.", ephemeral=True)

    async def _result_callback(self, interaction: discord.Interaction) -> None:
        if config.regulator_role_id not in {r.id for r in interaction.user.roles}:
            await interaction.response.send_message(
                "Ezt csak a Regulator szerepkörrel rendelkező tagok használhatják.",
                ephemeral=True,
            )
            return
        match = db.get_match_by_ticket(interaction.channel_id)
        if not match:
            await interaction.response.send_message(
                "Nem találtam ehhez a csatornához meccset.", ephemeral=True
            )
            return
        await interaction.response.send_modal(ResultModal(match_id=match["id"]))


# ===================================================================== #
# Result submission modal
# ===================================================================== #
class ResultModal(ui.Modal, title="Eredmény beírása"):
    winner_input = ui.TextInput(
        label="Győztes Discord ID vagy @mention",
        placeholder="123456789012345678  vagy  @NeonGamer322",
        required=True,
        max_length=50,
    )
    score_input = ui.TextInput(
        label="Eredmény (opcionális)",
        placeholder="pl. 2-1",
        required=False,
        max_length=20,
    )

    def __init__(self, match_id: str) -> None:
        super().__init__()
        self.match_id = match_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = self.winner_input.value.strip()
        # Accept either a mention or a raw integer
        winner_id: int | None = None
        if raw.startswith("<@") and raw.endswith(">"):
            inner = raw.strip("<@!&>")
            try:
                winner_id = int(inner)
            except ValueError:
                winner_id = None
        else:
            try:
                winner_id = int(raw)
            except ValueError:
                winner_id = None

        if winner_id is None:
            await interaction.response.send_message(
                "Érvénytelen győztes azonosító.", ephemeral=True
            )
            return

        db.set_match_winner(self.match_id, winner_id)
        match = db.get_match_by_ticket(interaction.channel_id)
        tournament_name = (
            match.get("tournaments", {}).get("name") if match else "Tournament"
        ) or "Tournament"

        results_channel = interaction.guild.get_channel(config.results_channel_id)
        if isinstance(results_channel, discord.TextChannel):
            p1_id = match["player1_discord_id"] if match else winner_id
            p2_id = match["player2_discord_id"] if match else 0
            p1_mc = match.get("player1_mc", "?") if match else "?"
            p2_mc = match.get("player2_mc", "?") if match else "?"
            score_txt = f" — Eredmény: {self.score_input.value}" if self.score_input.value else ""
            await results_channel.send(
                embed=discord.Embed(
                    title=f"{tournament_name} — Eredmény",
                    description=(
                        f"🏆 Győztes: <@{winner_id}>\n"
                        f"Párosítás: <@{p1_id}> ({p1_mc}) vs <@{p2_id}> ({p2_mc}){score_txt}"
                    ),
                    color=0xFFD700,
                )
            )

        await interaction.response.send_message(
            f"Eredmény rögzítve. Győztes: <@{winner_id}>",
            ephemeral=True,
        )
