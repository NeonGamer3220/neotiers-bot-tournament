import logging
import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timezone

from database import arun, db

log = logging.getLogger("neontiers.invites")

INVITE_LINK = "https://discord.gg/7fanAQDxaN"

TEXT_MAGAS = (
    "Szia! Ki lettél pörgetve egy magas tesztre a NeonTiers.hu szerverén, "
    "ha szeretnéd lejátszani, 48 órád lesz belépni a szerverre, ekkor a bot "
    "automatikusan hozzáad a kívánt tickethez, illetve a bot 24 óra múlva küld egy ismétlő üzenetet!\n"
    f"Csatlakozás: {INVITE_LINK}"
)

TEXT_TOURNAMENT = (
    "Szia! Jelenleg egy tournament folyik a NeonTiers.hu szerverén amire te jelentkeztél, "
    "hogyha szeretnéd lejátszani a mérkőzésed, 24 órád lesz belépni a szerverre, ekkor a bot "
    "automatikusan hozzáad a tournament ticketedhez!\n"
    f"Csatlakozás: {INVITE_LINK}"
)

class InvitesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.reminder_loop.start()

    def cog_unload(self):
        self.reminder_loop.cancel()

    # ==========================================
    # SLASH PARANCS: /sendmessage
    # ==========================================
    @app_commands.command(name="sendmessage", description="DM üzenet és automatikus ticket-hozzáadás beállítása.")
    @app_commands.choices(type=[
        app_commands.Choice(name="Magas ticket", value="magas"),
        app_commands.Choice(name="Tournament ticket", value="tournament")
    ])
    async def sendmessage(
        self, 
        interaction: discord.Interaction, 
        discordid: str, 
        type: app_commands.Choice[str], 
        ticket: discord.TextChannel
    ):
        await interaction.response.defer(ephemeral=True)
        
        try:
            target_id = int(discordid)
        except ValueError:
            await interaction.followup.send("❌ Érvénytelen Discord ID!", ephemeral=True)
            return

        msg_text = TEXT_MAGAS if type.value == "magas" else TEXT_TOURNAMENT

        # Megpróbáljuk elküldeni a DM-et a felhasználónak
        dm_sent = False
        try:
            user = await self.bot.fetch_user(target_id)
            if user:
                await user.send(msg_text)
                dm_sent = True
        except Exception as exc:
            log.warning("Nem sikerült közvetlen DM-et küldeni a felhasználónak (%s): %s", target_id, exc)

        # Eltároljuk a feladatot az adatbázisban
        await arun(db.create_pending_invite, target_id, type.value, ticket.id)

        status_msg = "✅ DM üzenet elküldve" if dm_sent else "⚠️ DM üzenet nem küldhető el (lehet, hogy zárt a DM-je)"
        await interaction.followup.send(
            f"{status_msg} és a mentés rögzítve!\n"
            f"**Célpont:** <@{target_id}>\n"
            f"**Típus:** {type.name}\n"
            f"**Ticket:** {ticket.mention}",
            ephemeral=True
        )

    # ==========================================
    # ESEMÉNY: Új tag csatlakozik a szerverre
    # ==========================================
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Amikor a játékos belép a szerverre, ellenőrizzük, van-e függőben lévő ticketje."""
        pending_list = await arun(db.get_pending_invite_for_user, member.id)
        if not pending_list:
            return

        for invite in pending_list:
            channel_id = int(invite.get("ticket_channel_id", 0))
            channel = member.guild.get_channel(channel_id)

            if isinstance(channel, discord.TextChannel):
                try:
                    # Jogosultság megadása a ticket csatornában
                    await channel.set_permissions(member, read_messages=True, send_messages=True)
                    
                    embed = discord.Embed(
                        title="👋 Csatlakozott a hiányzó játékos!",
                        description=f"<@{member.id}> megérkezett a szerverre és hozzáadásra került a tickethez.",
                        color=discord.Color.green()
                    )
                    await channel.send(content=f"🔔 <@{member.id}>", embed=embed)
                    
                    # Megjelöljük befejezettként
                    await arun(db.mark_invite_completed, invite["id"])
                    log.info("Játékos (%s) sikeresen hozzáadva a tickethez (%s).", member.id, channel.id)
                except discord.HTTPException as exc:
                    log.error("Hiba a jogok megadásakor: %s", exc)

    # ==========================================
    # HÁTTÉRFOLYAMAT: 24 órás emlékeztető
    # ==========================================
    @tasks.loop(minutes=30)
    async def reminder_loop(self):
        """24 óra elteltével újra elküldi a 'Magas ticket' emlékeztető üzenetet."""
        try:
            due_invites = await arun(db.get_due_reminders)
            for invite in due_invites:
                target_id = int(invite["discord_id"])
                try:
                    user = await self.bot.fetch_user(target_id)
                    if user:
                        await user.send(f"🔔 **Emlékeztető (24h telt el):**\n\n{TEXT_MAGAS}")
                        await arun(db.mark_reminder_sent, invite["id"])
                        log.info("Emlékeztető DM elküldve: %s", target_id)
                except Exception as exc:
                    log.warning("Emlékeztető DM küldése sikertelen (%s): %s", target_id, exc)
        except Exception as exc:
            log.error("Hiba a reminder_loop futásakor: %s", exc)


async def setup(bot: commands.Bot):
    await bot.add_cog(InvitesCog(bot))
