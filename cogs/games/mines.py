# cogs/games/mines.py — Mines 5x5 (English, reveal at end, Play Again / Change Bet)

import discord
from discord.ext import commands
from discord import app_commands
import random
_rng = random.SystemRandom()
from utils import (
    parse_amount, check_linked, check_balance, fmt_gems,
    error_embed, update_wager_roles,
    COLOR_GOLD, COLOR_ERROR, COLOR_INFO
)

GRID_SIZE = 25


class MinesGame:
    def __init__(self, bot, cog, player, bet, num_mines, house_edge):
        self.bot        = bot
        self.cog        = cog
        self.player     = player
        self.player_id  = player.id
        self.bet        = bet
        self.num_mines  = num_mines
        self.house_edge = house_edge
        self.revealed   = [False] * GRID_SIZE
        self.is_mine    = [False] * GRID_SIZE
        self.game_over  = False
        self.message    = None
        for pos in _rng.sample(range(GRID_SIZE), num_mines):
            self.is_mine[pos] = True
        self.safe_total = GRID_SIZE - num_mines

    def safe_count(self):
        return sum(1 for i in range(GRID_SIZE) if self.revealed[i] and not self.is_mine[i])

    def calc_multiplier(self, safe_revealed):
        if safe_revealed == 0: return 1.0
        prob = 1.0
        rem_cells, rem_mines = GRID_SIZE, self.num_mines
        for _ in range(safe_revealed):
            safe_rem = rem_cells - rem_mines
            if safe_rem <= 0: break
            prob *= safe_rem / rem_cells
            rem_cells -= 1
        if prob <= 0: return 1.0
        return round(max(1.01, (1.0 / prob) * (1 - self.house_edge / 100)), 2)

    def build_embed(self, result_text=None):
        safe = self.safe_count()
        mult = self.calc_multiplier(safe)
        pot  = int(round(self.bet * mult, 0))
        color = (COLOR_GOLD if result_text and "💰" in result_text
                 else COLOR_ERROR if result_text else COLOR_INFO)
        embed = discord.Embed(title=f"💣 Mines — {self.player.display_name}", color=color)
        embed.add_field(name="Bet",        value=fmt_gems(self.bet),     inline=True)
        embed.add_field(name="Mines",      value=f"💥 {self.num_mines}", inline=True)
        embed.add_field(name="Gems",       value=f"💎 {safe}",           inline=True)
        embed.add_field(name="Multiplier", value=f"x{mult:.2f}",         inline=True)
        embed.add_field(name="Cashout",    value=fmt_gems(pot),           inline=True)
        if result_text:
            embed.add_field(name="Result", value=result_text, inline=False)
        if not self.game_over and not result_text:
            embed.set_footer(text="Click a 💎 to cash out • ⬜ to reveal")
        return embed


class MinesView(discord.ui.View):
    def __init__(self, game: MinesGame, reveal_all=False):
        super().__init__(timeout=600)
        self.game = game
        self._build_grid(reveal_all)

    def _build_grid(self, reveal_all=False):
        for i in range(GRID_SIZE):
            row_num = i // 5
            if reveal_all:
                # Show everything
                if self.game.is_mine[i]:
                    btn = discord.ui.Button(label="💥", style=discord.ButtonStyle.danger,
                                            row=row_num, custom_id=f"mine_{i}", disabled=True)
                elif self.game.revealed[i]:
                    btn = discord.ui.Button(label="💎", style=discord.ButtonStyle.success,
                                            row=row_num, custom_id=f"gem_{i}", disabled=True)
                else:
                    btn = discord.ui.Button(label="⬜", style=discord.ButtonStyle.secondary,
                                            row=row_num, custom_id=f"safe_{i}", disabled=True)
            elif not self.game.revealed[i]:
                btn = discord.ui.Button(label="⬜", style=discord.ButtonStyle.secondary,
                                        row=row_num, custom_id=f"reveal_{i}",
                                        disabled=self.game.game_over)
                btn.callback = self._make_reveal(i)
            elif self.game.is_mine[i]:
                btn = discord.ui.Button(label="💥", style=discord.ButtonStyle.danger,
                                        row=row_num, custom_id=f"mine_{i}", disabled=True)
            else:
                btn = discord.ui.Button(label="💎", style=discord.ButtonStyle.success,
                                        row=row_num, custom_id=f"cashout_{i}",
                                        disabled=self.game.game_over)
                btn.callback = self._cashout_cb()
            self.add_item(btn)

    def _make_reveal(self, index: int):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.game.player_id:
                return await interaction.response.send_message("Not your game.", ephemeral=True)
            await self.game.reveal(interaction, index)
        return callback

    def _cashout_cb(self):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.game.player_id:
                return await interaction.response.send_message("Not your game.", ephemeral=True)
            await self.game.cashout(interaction)
        return callback

    async def on_timeout(self):
        self.game.cog.active_games.pop(self.game.player_id, None)
        self.game.game_over = True
        for item in self.children: item.disabled = True
        try: await self.game.message.edit(view=self)
        except Exception: pass


class MinesEndView(discord.ui.View):
    def __init__(self, cog, user, bet, num_mines):
        super().__init__(timeout=120)
        self.cog       = cog
        self.user      = user
        self.bet       = bet
        self.num_mines = num_mines

    @discord.ui.button(label="🎲 Play Again", style=discord.ButtonStyle.primary)
    async def play_again(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user.id:
            return await interaction.response.send_message("Not your game.", ephemeral=True)
        if interaction.user.id in self.cog.active_games:
            return await interaction.response.send_message("Already have active game.", ephemeral=True)
        bal = await self.cog.bot.db.get_balance(str(self.user.id))
        if bal < self.bet:
            for item in self.children: item.disabled = True
            return await interaction.response.edit_message(
                embed=discord.Embed(description="❌ Insufficient balance.", color=COLOR_ERROR), view=self)
        await _start_mines(self.cog, interaction, self.user, self.bet, self.num_mines, edit=True)

    @discord.ui.button(label="✏️ Change Bet", style=discord.ButtonStyle.secondary)
    async def change_bet(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user.id:
            return await interaction.response.send_message("Not your game.", ephemeral=True)
        await interaction.response.send_modal(MinesChangeBetModal(self.cog, self.user, self.num_mines))

    async def on_timeout(self):
        for item in self.children: item.disabled = True


class MinesChangeBetModal(discord.ui.Modal, title="💣 Change Bet"):
    new_bet = discord.ui.TextInput(label="New amount", placeholder="e.g. 500k, 1m", required=True)

    def __init__(self, cog, user, num_mines):
        super().__init__()
        self.cog       = cog
        self.user      = user
        self.num_mines = num_mines

    async def on_submit(self, interaction: discord.Interaction):
        amount = parse_amount(self.new_bet.value)
        if not amount or amount <= 0:
            return await interaction.response.send_message(embed=error_embed("Invalid amount."), ephemeral=True)
        bal = await self.cog.bot.db.get_balance(str(self.user.id))
        if bal < amount:
            return await interaction.response.send_message(
                embed=error_embed(f"Insufficient balance. You have {fmt_gems(bal)}"), ephemeral=True)
        await _start_mines(self.cog, interaction, self.user, amount, self.num_mines, edit=True)


async def _start_mines(cog, interaction, user, bet, num_mines, edit=False):
    if user.id in cog.active_games:
        return
    await cog.bot.db.remove_balance(str(user.id), bet)
    await cog.bot.db.add_wager(str(user.id), bet)
    await cog.bot.db.reduce_wager_requirement(str(user.id), bet)
    edge = await cog.bot.db.get_house_edge("mines")
    game = MinesGame(cog.bot, cog, user, bet, num_mines, edge)
    cog.active_games[user.id] = game
    embed = game.build_embed()
    view  = MinesView(game)
    if edit:
        await interaction.response.edit_message(embed=embed, view=view)
    else:
        await interaction.response.send_message(embed=embed, view=view)
    game.message = await interaction.original_response()


async def _reveal(self, interaction, index):
    if self.game_over or self.revealed[index]:
        return await interaction.response.defer()
    self.revealed[index] = True
    if self.is_mine[index]:
        self.game_over = True
        self.cog.active_games.pop(self.player_id, None)
        # Rakeback
        rb_pct = float(await self.bot.db.get_config("rakeback_pct") or "20")
        rb_amt = int(self.bet * rb_pct / 100)
        if rb_amt > 0:
            await self.bot.db.add_rakeback(str(self.player_id), rb_amt)
        # Affiliates
        edge_pct = await self.bot.db.get_house_edge("mines")
        house_p  = int(self.bet * edge_pct / 100)
        referrer = await self.bot.db.get_referrer(str(self.player_id))
        if referrer and house_p > 0:
            await self.bot.db.add_affiliate_earnings(referrer, str(self.player_id), int(house_p * 0.10))
        await self.bot.db.log_game(str(self.player_id), "mines", self.bet, "lose", -self.bet)
        member = interaction.guild.get_member(self.player_id)
        if member:
            await update_wager_roles(self.bot, interaction.guild, member)
        embed = self.build_embed("💥 Mine! Game over.")
        view  = MinesView(self, reveal_all=True)
        end_view = MinesEndView(self.cog, self.player, self.bet, self.num_mines)
        # Stack: grid (disabled) on top, end buttons below — use a combined approach
        # Since Discord only allows one view, we'll show the reveal grid then the end view
        await interaction.response.edit_message(embed=embed, view=view)
        # Edit again after brief pause to show end buttons
        import asyncio
        await asyncio.sleep(1.0)
        try:
            await interaction.edit_original_response(embed=embed, view=end_view)
        except Exception:
            pass
    else:
        safe = self.safe_count()
        if safe == self.safe_total:
            await _cashout(self, interaction)
        else:
            embed = self.build_embed()
            view  = MinesView(self)
            await interaction.response.edit_message(embed=embed, view=view)


async def _cashout(self, interaction):
    if self.game_over:
        return await interaction.response.defer()
    self.game_over = True
    self.cog.active_games.pop(self.player_id, None)
    safe   = self.safe_count()
    mult   = self.calc_multiplier(safe)
    payout = int(round(self.bet * mult, 0))
    profit = payout - self.bet
    await self.bot.db.add_balance(str(self.player_id), payout)
    result = "win" if profit > 0 else "tie"
    await self.bot.db.log_game(str(self.player_id), "mines", self.bet, result, profit)
    member = interaction.guild.get_member(self.player_id)
    if member:
        await update_wager_roles(self.bot, interaction.guild, member)

    # Check event record
    bot = self.bot
    is_new_record = await bot.db.update_event_record("mines_mult", mult, str(self.player_id), self.player.display_name)
    if is_new_record:
        ch_id = await bot.db.get_config("event_channel")
        if ch_id:
            for guild in bot.guilds:
                ch = guild.get_channel(int(ch_id))
                if ch:
                    embed_ev = discord.Embed(
                        title="🏆 New Mines Record!",
                        description=f"**{self.player.display_name}** hit **x{mult:.2f}** in Mines!",
                        color=COLOR_GOLD
                    )
                    try: await ch.send(embed=embed_ev)
                    except Exception: pass

    new_bal = await bot.db.get_balance(str(self.player_id))
    embed = self.build_embed(f"💰 Cashed out {fmt_gems(payout)} (x{mult:.2f})")
    embed.set_footer(text=f"Balance: {fmt_gems(new_bal)}")
    view_reveal = MinesView(self, reveal_all=True)
    end_view    = MinesEndView(self.cog, self.player, self.bet, self.num_mines)
    await interaction.response.edit_message(embed=embed, view=view_reveal)
    import asyncio
    await asyncio.sleep(1.0)
    try:
        await interaction.edit_original_response(embed=embed, view=end_view)
    except Exception:
        pass

MinesGame.reveal  = _reveal
MinesGame.cashout = _cashout


class Mines(commands.Cog):
    def __init__(self, bot):
        self.bot          = bot
        self.active_games = {}

    @app_commands.command(name="mines", description="Reveal gems without hitting a mine")
    @app_commands.describe(bet="Gems to wager", mines="Number of mines (1-20)")
    async def mines(self, interaction: discord.Interaction, bet: str, mines: int):
        if not await check_linked(interaction): return
        amount = parse_amount(str(bet))
        if not amount or amount <= 0:
            return await interaction.response.send_message(embed=error_embed("Invalid bet."), ephemeral=True)
        if not (1 <= mines <= 20):
            return await interaction.response.send_message(embed=error_embed("Mines must be 1-20."), ephemeral=True)
        if interaction.user.id in self.active_games:
            return await interaction.response.send_message(embed=error_embed("You already have an active game."), ephemeral=True)
        if not await check_balance(interaction, amount): return
        await _start_mines(self, interaction, interaction.user, amount, mines, edit=False)


async def setup(bot):
    await bot.add_cog(Mines(bot))
