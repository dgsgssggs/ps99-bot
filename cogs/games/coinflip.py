# cogs/games/coinflip.py — Coinflip PvP (English, fixed timeout, streak event)

import discord
from discord.ext import commands
from discord import app_commands
import random
_rng = random.SystemRandom()
import asyncio
from utils import (
    parse_amount, check_linked, check_balance, fmt_gems,
    error_embed, update_wager_roles,
    COLOR_GOLD, COLOR_ERROR, COLOR_INFO, COLOR_PURPLE
)

SIDE_EMOJI = {"heads": "🪙", "tails": "✨"}
SIDE_LABEL = {"heads": "🪙 Heads", "tails": "✨ Tails"}


class CoinflipChallenge:
    def __init__(self, creator: discord.Member, bet: int, creator_side: str):
        self.creator       = creator
        self.creator_id    = creator.id
        self.bet           = bet
        self.creator_side  = creator_side
        self.opponent_side = "tails" if creator_side == "heads" else "heads"
        self.message       = None
        self.resolved      = False


class ChooseSideView(discord.ui.View):
    def __init__(self, cog, bet, creator, channel, guild):
        super().__init__(timeout=30)
        self.cog     = cog
        self.bet     = bet
        self.creator = creator
        self.channel = channel
        self.guild   = guild

    @discord.ui.button(label="🪙 Heads", style=discord.ButtonStyle.primary)
    async def btn_heads(self, interaction, button):
        if interaction.user.id != self.creator.id: return
        await self._pick(interaction, "heads")

    @discord.ui.button(label="✨ Tails", style=discord.ButtonStyle.primary)
    async def btn_tails(self, interaction, button):
        if interaction.user.id != self.creator.id: return
        await self._pick(interaction, "tails")

    async def _pick(self, interaction, side):
        for item in self.children: item.disabled = True
        challenge = CoinflipChallenge(self.creator, self.bet, side)
        self.cog.active_challenges[self.creator.id] = challenge

        cf_ch_id = await self.cog.bot.db.get_config("coinflip_channel")
        target   = (self.guild.get_channel(int(cf_ch_id)) if cf_ch_id else None) or self.channel

        embed    = _build_challenge_embed(challenge)
        join_view = JoinView(self.cog, challenge)
        msg      = await target.send(embed=embed, view=join_view)
        challenge.message = msg
        join_view.challenge_msg = msg  # store for timeout cleanup

        link     = f"https://discord.com/channels/{interaction.guild_id}/{target.id}/{msg.id}"
        loc      = f"in {target.mention}" if target.id != self.channel.id else "here"
        await interaction.response.edit_message(
            content=f"✅ Challenge posted {loc} — **{SIDE_LABEL[side]}**\n[View coinflip]({link})",
            embed=None, view=self)


class JoinView(discord.ui.View):
    def __init__(self, cog, challenge: CoinflipChallenge):
        super().__init__(timeout=600)
        self.cog           = cog
        self.challenge     = challenge
        self.challenge_msg = None   # set after message is sent

    async def on_timeout(self):
        ch = self.challenge
        if ch.resolved: return
        ch.resolved = True
        self.cog.active_challenges.pop(ch.creator_id, None)
        # Refund creator
        await self.cog.bot.db.add_balance(str(ch.creator_id), ch.bet)
        # Delete the challenge message
        try:
            if self.challenge_msg:
                await self.challenge_msg.delete()
        except Exception:
            pass

    @discord.ui.button(label="⚔️ Join", style=discord.ButtonStyle.success, row=0)
    async def join(self, interaction, button):
        ch = self.challenge
        if interaction.user.id == ch.creator_id:
            return await interaction.response.send_message("You can't join your own challenge.", ephemeral=True)
        if ch.resolved:
            return await interaction.response.send_message("This challenge has ended.", ephemeral=True)
        user_data = await self.cog.bot.db.get_user(str(interaction.user.id))
        if not user_data or not user_data["roblox_name"]:
            return await interaction.response.send_message(embed=error_embed("Link your Roblox first: `/link`"), ephemeral=True)
        bal = await self.cog.bot.db.get_balance(str(interaction.user.id))
        if bal < ch.bet:
            return await interaction.response.send_message(
                embed=error_embed(f"Insufficient balance. Need {fmt_gems(ch.bet)}, have {fmt_gems(bal)}"), ephemeral=True)
        await self.cog.bot.db.remove_balance(str(interaction.user.id), ch.bet)
        await self.cog.bot.db.add_wager(str(interaction.user.id), ch.bet)
        await self.cog.bot.db.reduce_wager_requirement(str(interaction.user.id), ch.bet)
        await self._resolve(interaction, opponent=interaction.user, vs_bot=False)

    @discord.ui.button(label="🤖 Call Bot", style=discord.ButtonStyle.primary, row=0)
    async def callbot(self, interaction, button):
        ch = self.challenge
        if interaction.user.id != ch.creator_id:
            return await interaction.response.send_message("Only the creator can call the bot.", ephemeral=True)
        if ch.resolved:
            return await interaction.response.send_message("This challenge has ended.", ephemeral=True)
        await self._resolve(interaction, opponent=None, vs_bot=True)

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger, row=0)
    async def cancel(self, interaction, button):
        ch = self.challenge
        if interaction.user.id != ch.creator_id:
            return await interaction.response.send_message("Only the creator can cancel.", ephemeral=True)
        if ch.resolved:
            return await interaction.response.send_message("Already resolved.", ephemeral=True)
        ch.resolved = True
        self.cog.active_challenges.pop(ch.creator_id, None)
        await self.cog.bot.db.add_balance(str(ch.creator_id), ch.bet)
        self.stop()  # stop the timeout
        try: await interaction.message.delete()
        except Exception: pass
        await interaction.response.defer()

    async def _resolve(self, interaction, opponent, vs_bot):
        ch = self.challenge
        if ch.resolved: return
        ch.resolved = True
        self.stop()  # stop timeout so it doesn't refund
        self.cog.active_challenges.pop(ch.creator_id, None)
        for item in self.children: item.disabled = True

        # Animation
        FRAMES = [("🌀","Flipping..."),("🪙","· · · · ·"),("✨","· · · ·"),
                  ("🪙","· · ·"),("✨","· ·"),("🪙","·")]
        DELAYS = [0.2, 0.2, 0.3, 0.4, 0.5, 0.6]
        for (emoji, text), delay in zip(FRAMES, DELAYS):
            spin = discord.Embed(title=f"{emoji}  Coinflip", description=f"**{text}**", color=COLOR_PURPLE)
            try:
                await interaction.response.edit_message(embed=spin, view=self)
            except Exception:
                try: await interaction.edit_original_response(embed=spin, view=self)
                except Exception: pass
            await asyncio.sleep(delay)

        result       = _rng.choice(["heads", "tails"])
        creator_wins = (result == ch.creator_side)
        edge         = await self.cog.bot.db.get_house_edge("coinflip")
        payout_f     = 1 - (edge / 100)

        if vs_bot:
            if creator_wins:
                payout = int(round(ch.bet * 2 * payout_f, 0))
                await self.cog.bot.db.add_balance(str(ch.creator_id), payout)
                profit_creator = payout - ch.bet
                winner_name, loser_name = ch.creator.mention, "🤖 Bot"
            else:
                profit_creator = -ch.bet
                winner_name, loser_name = "🤖 Bot", ch.creator.mention
            await self.cog.bot.db.log_game(str(ch.creator_id), "coinflip", ch.bet,
                                            "win" if creator_wins else "lose", profit_creator)
            opp_display = "🤖 Bot"
            # Streak event (vs bot doesn't count for streak — only PvP)
            if not creator_wins:
                house_p  = int(ch.bet * edge / 100)
                rb_pct   = float(await self.cog.bot.db.get_config("rakeback_pct") or "20")
                rb_amt   = int(house_p * rb_pct / 100)
                if rb_amt > 0:
                    await self.cog.bot.db.add_rakeback(str(ch.creator_id), rb_amt)
                referrer = await self.cog.bot.db.get_referrer(str(ch.creator_id))
                if referrer and house_p > 0:
                    await self.cog.bot.db.add_affiliate_earnings(referrer, str(ch.creator_id), int(house_p * 0.10))
                await self.cog.bot.db.set_coinflip_streak(str(ch.creator_id), 0)
            else:
                cur = await self.cog.bot.db.get_coinflip_streak(str(ch.creator_id))
                await self.cog.bot.db.set_coinflip_streak(str(ch.creator_id), cur + 1)
        else:
            if creator_wins: winner, loser = ch.creator, opponent
            else:            winner, loser = opponent, ch.creator
            payout = int(round(ch.bet * 2 * payout_f, 0))
            await self.cog.bot.db.add_balance(str(winner.id), payout)
            winner_name  = winner.mention
            loser_name   = loser.mention
            opp_display  = opponent.mention
            profit_w, profit_l = payout - ch.bet, -ch.bet
            await self.cog.bot.db.log_game(str(winner.id), "coinflip", ch.bet, "win",  profit_w)
            await self.cog.bot.db.log_game(str(loser.id),  "coinflip", ch.bet, "lose", profit_l)
            guild = interaction.guild
            for m in [ch.creator, opponent]:
                member = guild.get_member(m.id)
                if member: await update_wager_roles(self.cog.bot, guild, member)
            house_p = int(ch.bet * edge / 100)
            rb_pct  = float(await self.cog.bot.db.get_config("rakeback_pct") or "20")
            rb_amt  = int(house_p * rb_pct / 100)
            if rb_amt > 0:
                await self.cog.bot.db.add_rakeback(str(loser.id), rb_amt)
            referrer = await self.cog.bot.db.get_referrer(str(loser.id))
            if referrer and house_p > 0:
                await self.cog.bot.db.add_affiliate_earnings(referrer, str(loser.id), int(house_p * 0.10))
            # Streak tracking (PvP)
            w_streak = await self.cog.bot.db.get_coinflip_streak(str(winner.id))
            await self.cog.bot.db.set_coinflip_streak(str(winner.id), w_streak + 1)
            await self.cog.bot.db.set_coinflip_streak(str(loser.id), 0)
            # Event check for winner
            new_streak = w_streak + 1
            is_new = await self.cog.bot.db.update_event_record(
                "coinflip_streak", new_streak, str(winner.id), winner.display_name)
            if is_new:
                ch_id = await self.cog.bot.db.get_config("event_channel")
                if ch_id:
                    for g in self.cog.bot.guilds:
                        ev_ch = g.get_channel(int(ch_id))
                        if ev_ch:
                            ev = discord.Embed(
                                title="🏆 New Coinflip Streak Record!",
                                description=f"**{winner.display_name}** is on a **{new_streak} win streak**!",
                                color=COLOR_GOLD)
                            try: await ev_ch.send(embed=ev)
                            except Exception: pass

        payout_shown = int(round(ch.bet * 2 * payout_f, 0))
        embed = discord.Embed(title="🪙 Coinflip — Result", color=COLOR_GOLD)
        embed.add_field(name="Result", value=f"**{SIDE_LABEL[result]}**", inline=False)
        embed.add_field(name=f"{SIDE_EMOJI[ch.creator_side]} {ch.creator.display_name}",
                        value=SIDE_LABEL[ch.creator_side], inline=True)
        embed.add_field(name=f"{SIDE_EMOJI[ch.opponent_side]} {'Bot' if vs_bot else opponent.display_name}",
                        value=SIDE_LABEL[ch.opponent_side], inline=True)
        embed.add_field(name="🏆 Winner", value=f"{winner_name} wins {fmt_gems(payout_shown)}", inline=False)

        await asyncio.sleep(0.3)
        try: await interaction.edit_original_response(embed=embed, view=self)
        except Exception: pass
        await asyncio.sleep(5)
        try:
            if ch.message: await ch.message.delete()
        except Exception: pass


def _build_challenge_embed(challenge: CoinflipChallenge):
    embed = discord.Embed(title="🪙 Coinflip — Open Challenge", color=COLOR_PURPLE)
    embed.description = (
        f"{challenge.creator.mention} bets {fmt_gems(challenge.bet)}\n"
        f"Chose **{SIDE_LABEL[challenge.creator_side]}**\n\n"
        f"Who takes **{SIDE_LABEL[challenge.opponent_side]}**?\n\n"
        f"Press **⚔️ Join** to play or **🤖 Call Bot** for an instant game"
    )
    embed.add_field(name="Bet",   value=fmt_gems(challenge.bet),     inline=True)
    embed.add_field(name="Prize", value=fmt_gems(challenge.bet * 2), inline=True)
    embed.set_thumbnail(url=challenge.creator.display_avatar.url)
    embed.set_footer(text="Expires in 10 minutes if nobody joins")
    return embed


class Coinflip(commands.Cog):
    def __init__(self, bot):
        self.bot               = bot
        self.active_challenges = {}

    @app_commands.command(name="coinflip", description="Create a coinflip challenge")
    @app_commands.describe(bet="Gems to wager")
    async def coinflip(self, interaction: discord.Interaction, bet: str):
        if not await check_linked(interaction): return
        amount = parse_amount(str(bet))
        if not amount or amount <= 0:
            return await interaction.response.send_message(embed=error_embed("Invalid bet."), ephemeral=True)
        if interaction.user.id in self.active_challenges:
            ch  = self.active_challenges[interaction.user.id]
            msg = ch.message
            link = f"https://discord.com/channels/{interaction.guild_id}/{msg.channel.id}/{msg.id}" if msg else ""
            return await interaction.response.send_message(
                embed=error_embed(f"You already have an open challenge.{' [View](' + link + ')' if link else ''}"),
                ephemeral=True)
        if not await check_balance(interaction, amount): return
        await self.bot.db.remove_balance(str(interaction.user.id), amount)
        await self.bot.db.add_wager(str(interaction.user.id), amount)
        await self.bot.db.reduce_wager_requirement(str(interaction.user.id), amount)
        embed = discord.Embed(title="🪙 Coinflip — Choose Your Side",
                              description=f"Bet: **{fmt_gems(amount)}**\n\nWhich side are you on?",
                              color=COLOR_PURPLE)
        view  = ChooseSideView(self, amount, interaction.user, interaction.channel, interaction.guild)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Coinflip(bot))
