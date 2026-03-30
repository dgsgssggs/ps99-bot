# cogs/games/blackjack.py — Blackjack (English, animated dealing, Play Again / Change Bet)

import discord
from discord.ext import commands
from discord import app_commands
import random
_rng = random.SystemRandom()
import asyncio
from utils import (
    parse_amount, check_linked, check_balance, fmt_gems,
    error_embed, update_wager_roles,
    COLOR_GOLD, COLOR_ERROR, COLOR_INFO
)

SUITS = ["♠️", "♥️", "♦️", "♣️"]
RANKS = ["2","3","4","5","6","7","8","9","10","J","Q","K","A"]
CARD_VALUES = {"2":2,"3":3,"4":4,"5":5,"6":6,"7":7,"8":8,"9":9,
               "10":10,"J":10,"Q":10,"K":10,"A":11}

_HIGH_CARDS   = [("10","♠️"),("J","♠️"),("Q","♠️"),("K","♠️"),
                 ("10","♥️"),("J","♥️"),("Q","♥️"),("K","♥️")]
_DEALER_HELP  = [("6","♠️"),("7","♠️"),("8","♠️"),("9","♠️"),
                 ("6","♥️"),("7","♥️"),("8","♥️"),("9","♥️")]

def new_deck():
    deck = [(r,s) for s in SUITS for r in RANKS]
    _rng.shuffle(deck)
    return deck

def hand_value(hand):
    total, aces = 0, 0
    for rank, _ in hand:
        total += CARD_VALUES[rank]
        if rank == "A": aces += 1
    while total > 21 and aces > 0:
        total -= 10; aces -= 1
    return total

def fmt_hand(hand, hide_first=False):
    return "  ".join("🂠" if (hide_first and i==0) else f"{r}{s}"
                     for i,(r,s) in enumerate(hand))

def is_blackjack(hand):
    return len(hand) == 2 and hand_value(hand) == 21

def draw_rigged_player(deck, player_total):
    card = deck.pop(0)
    if 12 <= player_total <= 16 and _rng.random() < 0.18:
        card = _rng.choice(_HIGH_CARDS)
    return card

def draw_rigged_dealer(deck, dealer_total):
    card = deck.pop(0)
    if 14 <= dealer_total <= 17 and _rng.random() < 0.15:
        card = _rng.choice(_DEALER_HELP)
    return card


# ── Change bet modal ──────────────────────────────────────────
class ChangeBetModal(discord.ui.Modal, title="🃏 Change Bet"):
    new_bet = discord.ui.TextInput(label="New amount", placeholder="e.g. 500k, 1m, 2.5b", required=True)

    def __init__(self, cog, user, old_bet):
        super().__init__()
        self.cog     = cog
        self.user    = user
        self.old_bet = old_bet

    async def on_submit(self, interaction: discord.Interaction):
        amount = parse_amount(self.new_bet.value)
        if not amount or amount <= 0:
            await interaction.response.send_message(embed=error_embed("Invalid amount."), ephemeral=True)
            return
        bal = await self.cog.bot.db.get_balance(str(self.user.id))
        if bal < amount:
            await interaction.response.send_message(
                embed=error_embed(f"Insufficient balance. You have {fmt_gems(bal)}"), ephemeral=True)
            return
        await _start_game(self.cog, interaction, self.user, amount, edit=True)


# ── End-game view (Play Again / Change Bet) ───────────────────
class BlackjackEndView(discord.ui.View):
    def __init__(self, cog, user, bet):
        super().__init__(timeout=120)
        self.cog  = cog
        self.user = user
        self.bet  = bet

    @discord.ui.button(label="🃏 Play Again", style=discord.ButtonStyle.primary)
    async def play_again(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user.id:
            return await interaction.response.send_message("Not your game.", ephemeral=True)
        bal = await self.cog.bot.db.get_balance(str(self.user.id))
        if bal < self.bet:
            for item in self.children: item.disabled = True
            return await interaction.response.edit_message(
                embed=discord.Embed(description=f"❌ Insufficient balance.", color=COLOR_ERROR), view=self)
        await _start_game(self.cog, interaction, self.user, self.bet, edit=True)

    @discord.ui.button(label="✏️ Change Bet", style=discord.ButtonStyle.secondary)
    async def change_bet(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user.id:
            return await interaction.response.send_message("Not your game.", ephemeral=True)
        await interaction.response.send_modal(ChangeBetModal(self.cog, self.user, self.bet))

    async def on_timeout(self):
        for item in self.children: item.disabled = True


# ── In-game view (Hit / Stand) ────────────────────────────────
class BlackjackView(discord.ui.View):
    def __init__(self, game):
        super().__init__(timeout=600)
        self.game = game

    async def on_timeout(self):
        self.game.cog.active_games.pop(self.game.player_id, None)
        for item in self.children: item.disabled = True
        try: await self.game.message.edit(view=self)
        except Exception: pass

    @discord.ui.button(label="🃏 Hit", style=discord.ButtonStyle.primary)
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.game.player_id:
            return await interaction.response.send_message("Not your game.", ephemeral=True)
        for item in self.children: item.disabled = True
        loading = self.game.build_embed()
        loading.set_footer(text="🃏 Dealing...")
        await interaction.response.edit_message(embed=loading, view=self)
        await asyncio.sleep(0.5)
        pv = hand_value(self.game.player_hand)
        self.game.player_hand.append(draw_rigged_player(self.game.deck, pv))
        value = hand_value(self.game.player_hand)
        if value > 21:
            await self.game.end_game(interaction, "bust", via_edit=True)
        elif value == 21:
            await self.game.do_stand(interaction, via_edit=True)
        else:
            await interaction.edit_original_response(embed=self.game.build_embed(), view=BlackjackView(self.game))

    @discord.ui.button(label="🛑 Stand", style=discord.ButtonStyle.secondary)
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.game.player_id:
            return await interaction.response.send_message("Not your game.", ephemeral=True)
        await self.game.do_stand(interaction)


# ── Game state ────────────────────────────────────────────────
class BlackjackGame:
    def __init__(self, bot, cog, player, bet):
        self.bot       = bot
        self.cog       = cog
        self.player    = player
        self.player_id = player.id
        self.bet       = bet
        self.deck      = new_deck()
        self.message   = None
        self.player_hand = []
        self.dealer_hand = []

    def build_embed(self, show_dealer=False, result_text=None):
        pv = hand_value(self.player_hand) if self.player_hand else 0
        dv = hand_value(self.dealer_hand) if self.dealer_hand else 0
        if result_text:
            color = COLOR_GOLD if any(w in result_text for w in ["✅","🃏","🤝"]) else COLOR_ERROR
        else:
            color = COLOR_INFO
        embed = discord.Embed(title=f"🃏 Blackjack — {self.player.display_name}", color=color)
        embed.add_field(name=f"Your hand ({pv})", value=fmt_hand(self.player_hand) or "—", inline=False)
        embed.add_field(name=f"Dealer ({'?' if not show_dealer else dv})",
                        value=fmt_hand(self.dealer_hand, hide_first=not show_dealer) or "—", inline=False)
        embed.add_field(name="Bet", value=fmt_gems(self.bet), inline=True)
        if result_text:
            embed.add_field(name="Result", value=result_text, inline=False)
        return embed

    async def do_stand(self, interaction, via_edit=False):
        """Dealer plays with 0.5s delay per card."""
        # Show dealer's hidden card first
        embed = self.build_embed(show_dealer=True)
        embed.set_footer(text="Dealer playing...")
        if via_edit:
            await interaction.edit_original_response(embed=embed)
        else:
            for item in BlackjackView(self).children: pass  # just need to edit
            await interaction.response.edit_message(embed=embed, view=discord.ui.View())
        await asyncio.sleep(0.5)

        while hand_value(self.dealer_hand) < 17:
            dv = hand_value(self.dealer_hand)
            self.dealer_hand.append(draw_rigged_dealer(self.deck, dv))
            embed = self.build_embed(show_dealer=True)
            embed.set_footer(text="Dealer playing...")
            try:
                await interaction.edit_original_response(embed=embed)
            except Exception:
                pass
            await asyncio.sleep(0.5)

        pv = hand_value(self.player_hand)
        dv = hand_value(self.dealer_hand)
        if dv > 21 or pv > dv:   result = "win"
        elif pv < dv:             result = "lose"
        else:                     result = "tie"
        await self.end_game(interaction, result, via_edit=True)

    async def end_game(self, interaction, result, via_edit=False):
        db      = self.bot.db
        user_id = str(self.player_id)
        self.cog.active_games.pop(self.player_id, None)

        if result == "blackjack":
            profit    = int(round(self.bet * 1.5, 0))
            await db.add_balance(user_id, self.bet + profit)
            res_text  = f"🃏 BLACKJACK! You won {fmt_gems(profit)}"
            db_result = "win"
        elif result == "win":
            profit    = self.bet
            await db.add_balance(user_id, self.bet + profit)
            res_text  = f"✅ You won {fmt_gems(profit)}!"
            db_result = "win"
        elif result == "tie":
            await db.add_balance(user_id, self.bet)
            profit    = 0
            res_text  = "🤝 Push — bet returned"
            db_result = "tie"
        else:
            profit    = -self.bet
            res_text  = "❌ Bust" if result == "bust" else "❌ Dealer wins"
            db_result = "lose"
            # Rakeback
            edge_pct = await db.get_house_edge("blackjack")
            house_p  = int(self.bet * edge_pct / 100)
            rb_pct   = float(await db.get_config("rakeback_pct") or "20")
            rb_amt   = int(house_p * rb_pct / 100)
            if rb_amt > 0:
                await db.add_rakeback(user_id, rb_amt)
            # Affiliates
            referrer = await db.get_referrer(user_id)
            if referrer and house_p > 0:
                aff_cut = int(house_p * 0.10)
                if aff_cut > 0:
                    await db.add_affiliate_earnings(referrer, user_id, aff_cut)

        await db.log_game(user_id, "blackjack", self.bet, db_result, profit)
        member = interaction.guild.get_member(self.player_id)
        if member:
            await update_wager_roles(self.bot, interaction.guild, member)

        new_bal = await db.get_balance(user_id)
        embed   = self.build_embed(show_dealer=True, result_text=res_text)
        embed.set_footer(text=f"Balance: {fmt_gems(new_bal)}")
        end_view = BlackjackEndView(self.cog, self.player, self.bet)

        if via_edit:
            await interaction.edit_original_response(embed=embed, view=end_view)
        else:
            await interaction.response.edit_message(embed=embed, view=end_view)


# ── Helper: start / restart a game ───────────────────────────
async def _start_game(cog, interaction, user, bet, edit=False):
    if user.id in cog.active_games:
        return
    bal = await cog.bot.db.get_balance(str(user.id))
    if bal < bet:
        err = discord.Embed(description=f"❌ Insufficient balance.", color=COLOR_ERROR)
        if edit: return await interaction.response.edit_message(embed=err, view=None)
        return await interaction.response.send_message(embed=err, ephemeral=True)

    await cog.bot.db.remove_balance(str(user.id), bet)
    await cog.bot.db.add_wager(str(user.id), bet)
    await cog.bot.db.reduce_wager_requirement(str(user.id), bet)

    game = BlackjackGame(cog.bot, cog, user, bet)
    cog.active_games[user.id] = game

    # ── Animated deal ─────────────────────────────────────────
    # Start with empty hands, deal cards one by one with 0.5s delay
    loading = discord.Embed(title=f"🃏 Blackjack — {user.display_name}",
                            description="Shuffling...", color=COLOR_INFO)
    if edit:
        await interaction.response.edit_message(embed=loading, view=None)
    else:
        await interaction.response.send_message(embed=loading)
    game.message = await interaction.original_response()
    await asyncio.sleep(0.3)

    deck = game.deck
    # Deal: player1, dealer1(hidden), player2, dealer2(hidden)
    for step in range(4):
        card = deck.pop(0)
        if step == 0:   game.player_hand.append(card)
        elif step == 1: game.dealer_hand.append(card)
        elif step == 2: game.player_hand.append(draw_rigged_player(deck, hand_value(game.player_hand)))
        elif step == 3: game.dealer_hand.append(card)
        await asyncio.sleep(0.5)
        try:
            await game.message.edit(embed=game.build_embed(show_dealer=False))
        except Exception:
            pass

    # Check natural blackjack
    if is_blackjack(game.player_hand):
        cog.active_games.pop(user.id, None)
        await asyncio.sleep(0.3)
        await game.end_game(interaction, "blackjack", via_edit=True)
        return

    view = BlackjackView(game)
    await game.message.edit(embed=game.build_embed(), view=view)


# ── Cog ───────────────────────────────────────────────────────
class Blackjack(commands.Cog):
    def __init__(self, bot):
        self.bot          = bot
        self.active_games = {}

    @app_commands.command(name="blackjack", description="Play a hand of Blackjack")
    @app_commands.describe(bet="Gems to wager (e.g. 500k, 1m, 2.5b)")
    async def blackjack(self, interaction: discord.Interaction, bet: str):
        if not await check_linked(interaction): return
        amount = parse_amount(str(bet))
        if not amount or amount <= 0:
            return await interaction.response.send_message(embed=error_embed("Invalid bet."), ephemeral=True)
        if interaction.user.id in self.active_games:
            return await interaction.response.send_message(
                embed=error_embed("You already have an active game."), ephemeral=True)
        if not await check_balance(interaction, amount): return
        await _start_game(self, interaction, interaction.user, amount, edit=False)


async def setup(bot):
    await bot.add_cog(Blackjack(bot))
