# cogs/affiliates.py — Affiliate system

import discord
from discord.ext import commands
from discord import app_commands
from utils import fmt_gems, error_embed, success_embed, check_linked, COLOR_GOLD, COLOR_INFO

class Affiliates(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="affiliate", description="Set who referred you (one-time only)")
    @app_commands.describe(user="The user who referred you")
    async def affiliate(self, interaction: discord.Interaction, user: discord.Member):
        if not await check_linked(interaction): return
        if user.id == interaction.user.id:
            return await interaction.response.send_message(
                embed=error_embed("You can't refer yourself."), ephemeral=True)
        # Check referred user exists
        ref_data = await self.bot.db.get_user(str(user.id))
        if not ref_data:
            return await interaction.response.send_message(
                embed=error_embed("That user hasn't linked their account yet."), ephemeral=True)
        ok = await self.bot.db.set_affiliate(str(interaction.user.id), str(user.id))
        if not ok:
            return await interaction.response.send_message(
                embed=error_embed("You already have a referrer set."), ephemeral=True)
        embed = discord.Embed(
            title="✅ Referral Set",
            description=f"You've been referred by **{user.display_name}**.\nThey'll earn 10% of your house edge losses.",
            color=COLOR_GOLD
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="affiliates", description="View your referrals and earnings")
    async def affiliates(self, interaction: discord.Interaction):
        if not await check_linked(interaction): return
        uid      = str(interaction.user.id)
        referrals = await self.bot.db.get_affiliates(uid)
        total    = await self.bot.db.get_total_affiliate_generated(uid)
        referrer = await self.bot.db.get_referrer(uid)

        embed = discord.Embed(title=f"🤝 Affiliates — {interaction.user.display_name}", color=COLOR_GOLD)

        if referrer:
            ref_member = interaction.guild.get_member(int(referrer))
            ref_name   = ref_member.display_name if ref_member else f"<@{referrer}>"
            embed.add_field(name="Your referrer", value=ref_name, inline=False)

        if not referrals:
            embed.description = "You have no referrals yet.\nShare your username so others can use `/affiliate`!"
        else:
            lines = []
            for row in referrals[:15]:
                name = row["roblox_name"] or f"<@{row['referred_id']}>"
                lines.append(f"**{name}** — generated {fmt_gems(row['gems_generated'])}")
            embed.add_field(name=f"Your referrals ({len(referrals)})", value="\n".join(lines), inline=False)
            embed.add_field(name="Total generated for you", value=fmt_gems(total), inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Affiliates(bot))
