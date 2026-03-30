# cogs/games/towers.py — Towers (English, sliding grid, event support)

import discord
from discord.ext import commands
from discord import app_commands
import random
_rng = random.SystemRandom()
from utils import (
    parse_amount, check_linked, check_balance,
    fmt_gems, error_embed, update_wager_roles,
    COLOR_GOLD, COLOR_ERROR, COLOR_PURPLE
)

FLOORS    = 8
DIFF_COLS = {"easy": 4, "normal": 3, "hard": 2}


def tower_multiplier(floors_cleared, cols, edge):
    if floors_cleared == 0: return 1.0
    p = ((cols - 1) / cols) ** floors_cleared
    if p <= 0: return 1.0
    return round((1.0 / p) * (1 - edge / 100), 2)


class TowerGame:
    def __init__(self, player, bet, cols, edge):
        self.player    = player
        self.player_id = player.id
        self.bet       = bet
        self.cols      = cols
        self.edge      = edge
        self.floor     = 0
        self.alive     = True
        self.cashed    = False
        self.message   = None
        self.cog       = None
        self.mine      = {f: _rng.randint(0, cols - 1) for f in range(1, FLOORS + 1)}
        self.choices   = {}

    def current_mult(self): return tower_multiplier(self.floor, self.cols, self.edge)
    def next_mult(self):    return tower_multiplier(self.floor + 1, self.cols, self.edge)

    def build_embed(self, result_text=None):
        pot  = int(round(self.bet * self.current_mult(), 0))
        if result_text and ("💥" in result_text or "❌" in result_text):
            color = COLOR_ERROR
        elif result_text and ("✅" in result_text or "💰" in result_text):
            color = COLOR_GOLD
        else:
            color = COLOR_PURPLE
        embed = discord.Embed(title=f"🏰 Towers — {self.player.display_name}", color=color)
        embed.add_field(name="Bet",          value=fmt_gems(self.bet),               inline=True)
        embed.add_field(name="Floor",        value=f"**{self.floor}/{FLOORS}**",     inline=True)
        embed.add_field(name="Multiplier",   value=f"x{self.current_mult():.2f}",   inline=True)
        embed.add_field(name="💰 Cashout",   value=fmt_gems(pot),                   inline=True)
        if self.alive and not self.cashed and self.floor < FLOORS:
            nxt = int(round(self.bet * self.next_mult(), 0))
            embed.add_field(name="Next floor", value=fmt_gems(nxt), inline=True)
        if result_text:
            embed.add_field(name="Result", value=result_text, inline=False)
        return embed


class TowerView(discord.ui.View):
    def __init__(self, game: TowerGame):
        super().__init__(timeout=300)
        self.game = game
        self._rebuild()

    def _rebuild(self):
        self.clear_items()
        game   = self.game
        if not game.alive or game.cashed: return
        active = game.floor + 1
        cols   = game.cols
        floor_in_row = {0: active+2, 1: active+1, 2: active, 3: active-1}
        for row_idx, floor_num in floor_in_row.items():
            if floor_num > FLOORS or floor_num < 0: continue
            if floor_num == 0:
                for c in range(cols):
                    self.add_item(discord.ui.Button(label="🟩", style=discord.ButtonStyle.secondary,
                                                     disabled=True, row=row_idx, custom_id=f"ground_{c}"))
            elif floor_num < active:
                ch = game.choices.get(floor_num)
                for c in range(cols):
                    label = "✅" if c == ch else "⬛"
                    style = discord.ButtonStyle.success if c == ch else discord.ButtonStyle.secondary
                    self.add_item(discord.ui.Button(label=label, style=style, disabled=True,
                                                     row=row_idx, custom_id=f"done_{floor_num}_{c}"))
            elif floor_num == active:
                for c in range(cols):
                    btn = discord.ui.Button(label="⬜", style=discord.ButtonStyle.primary,
                                            row=row_idx, custom_id=f"pick_{floor_num}_{c}")
                    btn.callback = self._make_pick(c)
                    self.add_item(btn)
            else:
                for c in range(cols):
                    self.add_item(discord.ui.Button(label="🔒", style=discord.ButtonStyle.secondary,
                                                     disabled=True, row=row_idx, custom_id=f"fut_{floor_num}_{c}"))
        pot = int(round(game.bet * game.current_mult(), 0))
        cashout_btn = discord.ui.Button(
            label=f"💰 Cashout  {fmt_gems(pot)}  ·  x{game.current_mult():.2f}",
            style=discord.ButtonStyle.success, row=4, custom_id="tower_cashout")
        cashout_btn.callback = self._cashout_cb
        self.add_item(cashout_btn)

    def _make_pick(self, col):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.game.player_id:
                return await interaction.response.send_message("Not your game.", ephemeral=True)
            await self._pick(interaction, col)
        return callback

    async def _pick(self, interaction, col):
        game         = self.game
        active_floor = game.floor + 1
        mine_col     = game.mine[active_floor]
        game.choices[active_floor] = col

        if col == mine_col:
            game.alive = False
            game.cog.active_games.pop(game.player_id, None)
            house_p = int(game.bet * game.edge / 100)
            rb_pct  = float(await game.cog.bot.db.get_config("rakeback_pct") or "20")
            rb_amt  = int(house_p * rb_pct / 100)
            if rb_amt > 0:
                await game.cog.bot.db.add_rakeback(str(game.player_id), rb_amt)
            referrer = await game.cog.bot.db.get_referrer(str(game.player_id))
            if referrer and house_p > 0:
                await game.cog.bot.db.add_affiliate_earnings(referrer, str(game.player_id), int(house_p * 0.10))
            await game.cog.bot.db.log_game(str(game.player_id), "towers", game.bet, "lose", -game.bet)
            member = interaction.guild.get_member(game.player_id)
            if member:
                await update_wager_roles(game.cog.bot, interaction.guild, member)
            self._show_explosion(active_floor, col, mine_col)
            embed = game.build_embed(f"💥 Mine on floor {active_floor}!")
            await interaction.response.edit_message(embed=embed, view=self)
            # Show end buttons after brief pause
            import asyncio
            await asyncio.sleep(1.0)
            end_view = TowerEndView(game.cog, game.player, game.bet, game.cols, game.edge)
            try: await interaction.edit_original_response(embed=embed, view=end_view)
            except Exception: pass
        elif game.floor + 1 >= FLOORS:
            game.floor += 1
            await self._do_cashout(interaction)
        else:
            game.floor += 1
            self._rebuild()
            embed = game.build_embed(f"✅ Floor {active_floor} cleared!")
            await interaction.response.edit_message(embed=embed, view=self)

    def _show_explosion(self, exploded_floor, chosen, mine):
        self.clear_items()
        game   = self.game
        active = exploded_floor
        cols   = game.cols
        floor_in_row = {0: active+2, 1: active+1, 2: active, 3: active-1}
        for row_idx, floor_num in floor_in_row.items():
            if floor_num > FLOORS or floor_num < 0: continue
            if floor_num == 0:
                for c in range(cols):
                    self.add_item(discord.ui.Button(label="🟩", style=discord.ButtonStyle.secondary,
                                                     disabled=True, row=row_idx, custom_id=f"gnd_{c}"))
            elif floor_num < active:
                ch = game.choices.get(floor_num)
                for c in range(cols):
                    label = "✅" if c == ch else "⬛"
                    style = discord.ButtonStyle.success if c == ch else discord.ButtonStyle.secondary
                    self.add_item(discord.ui.Button(label=label, style=style, disabled=True,
                                                     row=row_idx, custom_id=f"d_{floor_num}_{c}"))
            elif floor_num == active:
                for c in range(cols):
                    if c == mine:   label, style = "💥", discord.ButtonStyle.danger
                    elif c == chosen: label, style = "✅", discord.ButtonStyle.success
                    else:           label, style = "⬛", discord.ButtonStyle.secondary
                    self.add_item(discord.ui.Button(label=label, style=style, disabled=True,
                                                     row=row_idx, custom_id=f"exp_{c}"))
            else:
                for c in range(cols):
                    self.add_item(discord.ui.Button(label="🔒", style=discord.ButtonStyle.secondary,
                                                     disabled=True, row=row_idx, custom_id=f"fut_{floor_num}_{c}"))

    async def _cashout_cb(self, interaction):
        if interaction.user.id != self.game.player_id:
            return await interaction.response.send_message("Not your game.", ephemeral=True)
        await self._do_cashout(interaction)

    async def _do_cashout(self, interaction):
        game = self.game
        game.cashed = True
        game.cog.active_games.pop(game.player_id, None)
        mult   = game.current_mult()
        payout = int(round(game.bet * mult, 0))
        profit = payout - game.bet
        await game.cog.bot.db.add_balance(str(game.player_id), payout)
        await game.cog.bot.db.log_game(str(game.player_id), "towers", game.bet, "win", profit)
        member = interaction.guild.get_member(game.player_id)
        if member:
            await update_wager_roles(game.cog.bot, interaction.guild, member)
        # Event check
        is_new = await game.cog.bot.db.update_event_record(
            "towers_mult", mult, str(game.player_id), game.player.display_name)
        if is_new:
            ch_id = await game.cog.bot.db.get_config("event_channel")
            if ch_id:
                for guild in game.cog.bot.guilds:
                    ch = guild.get_channel(int(ch_id))
                    if ch:
                        ev = discord.Embed(title="🏆 New Towers Record!",
                                           description=f"**{game.player.display_name}** reached **x{mult:.2f}** in Towers!",
                                           color=COLOR_GOLD)
                        try: await ch.send(embed=ev)
                        except Exception: pass
        new_bal = await game.cog.bot.db.get_balance(str(game.player_id))
        embed   = game.build_embed(f"💰 Cashed out x{mult:.2f} — {fmt_gems(payout)}")
        embed.set_footer(text=f"Balance: {fmt_gems(new_bal)}")
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)
        import asyncio; await asyncio.sleep(1.0)
        end_view = TowerEndView(game.cog, game.player, game.bet, game.cols, game.edge)
        try: await interaction.edit_original_response(embed=embed, view=end_view)
        except Exception: pass

    async def on_timeout(self):
        game = self.game
        game.cog.active_games.pop(game.player_id, None)
        if game.floor > 0 and game.alive:
            payout = int(round(game.bet * game.current_mult(), 0))
            await game.cog.bot.db.add_balance(str(game.player_id), payout)
        elif game.floor == 0:
            await game.cog.bot.db.add_balance(str(game.player_id), game.bet)
        for item in self.children: item.disabled = True
        try:
            embed = game.build_embed("⏰ Timed out — cashed out automatically")
            if game.message: await game.message.edit(embed=embed, view=self)
        except Exception: pass


class TowerEndView(discord.ui.View):
    def __init__(self, cog, user, bet, cols, edge):
        super().__init__(timeout=120)
        self.cog  = cog
        self.user = user
        self.bet  = bet
        self.cols = cols
        self.edge = edge

    @discord.ui.button(label="🎲 Play Again", style=discord.ButtonStyle.primary)
    async def play_again(self, interaction, button):
        if interaction.user.id != self.user.id:
            return await interaction.response.send_message("Not your game.", ephemeral=True)
        if interaction.user.id in self.cog.active_games:
            return await interaction.response.send_message("Already have active game.", ephemeral=True)
        bal = await self.cog.bot.db.get_balance(str(self.user.id))
        if bal < self.bet:
            for item in self.children: item.disabled = True
            return await interaction.response.edit_message(
                embed=discord.Embed(description="❌ Insufficient balance.", color=COLOR_ERROR), view=self)
        await _start_towers(self.cog, interaction, self.user, self.bet, self.cols, edit=True)

    @discord.ui.button(label="✏️ Change Bet", style=discord.ButtonStyle.secondary)
    async def change_bet(self, interaction, button):
        if interaction.user.id != self.user.id:
            return await interaction.response.send_message("Not your game.", ephemeral=True)
        await interaction.response.send_modal(TowerChangeBetModal(self.cog, self.user, self.cols))

    async def on_timeout(self):
        for item in self.children: item.disabled = True


class TowerChangeBetModal(discord.ui.Modal, title="🏰 Change Bet"):
    new_bet = discord.ui.TextInput(label="New amount", placeholder="e.g. 500k, 1m", required=True)

    def __init__(self, cog, user, cols):
        super().__init__()
        self.cog  = cog
        self.user = user
        self.cols = cols

    async def on_submit(self, interaction):
        amount = parse_amount(self.new_bet.value)
        if not amount or amount <= 0:
            return await interaction.response.send_message(embed=error_embed("Invalid amount."), ephemeral=True)
        bal = await self.cog.bot.db.get_balance(str(self.user.id))
        if bal < amount:
            return await interaction.response.send_message(
                embed=error_embed(f"Insufficient balance. You have {fmt_gems(bal)}"), ephemeral=True)
        await _start_towers(self.cog, interaction, self.user, amount, self.cols, edit=True)


async def _start_towers(cog, interaction, user, bet, cols, edit=False):
    if user.id in cog.active_games: return
    await cog.bot.db.remove_balance(str(user.id), bet)
    await cog.bot.db.add_wager(str(user.id), bet)
    await cog.bot.db.reduce_wager_requirement(str(user.id), bet)
    edge = await cog.bot.db.get_house_edge("towers")
    game = TowerGame(user, bet, cols, edge)
    game.cog = cog
    cog.active_games[user.id] = game
    embed = game.build_embed()
    view  = TowerView(game)
    if edit:
        await interaction.response.edit_message(embed=embed, view=view)
    else:
        await interaction.response.send_message(embed=embed, view=view)
    game.message = await interaction.original_response()


class Towers(commands.Cog):
    def __init__(self, bot):
        self.bot          = bot
        self.active_games = {}

    @app_commands.command(name="towers", description="Climb the tower avoiding mines on each floor")
    @app_commands.describe(bet="Gems to wager", difficulty="Tower difficulty")
    @app_commands.choices(difficulty=[
        app_commands.Choice(name="Easy   — 4 columns", value="easy"),
        app_commands.Choice(name="Normal — 3 columns", value="normal"),
        app_commands.Choice(name="Hard   — 2 columns", value="hard"),
    ])
    async def towers(self, interaction, bet: str, difficulty: str = "normal"):
        if not await check_linked(interaction): return
        amount = parse_amount(str(bet))
        if not amount or amount <= 0:
            return await interaction.response.send_message(embed=error_embed("Invalid bet."), ephemeral=True)
        if interaction.user.id in self.active_games:
            return await interaction.response.send_message(embed=error_embed("Already have active game."), ephemeral=True)
        if not await check_balance(interaction, amount): return
        cols = DIFF_COLS.get(difficulty, 3)
        await _start_towers(self, interaction, interaction.user, amount, cols, edit=False)


async def setup(bot):
    await bot.add_cog(Towers(bot))
