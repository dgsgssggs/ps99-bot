# ============================================================
# cogs/games/hilo.py — Juego Hi-Lo (Mayor/Menor)
# ============================================================
# FIX: Los botones muestran el multiplicador TOTAL resultante
# (no el factor por turno). Así "→ x3.12" en el botón significa
# que si ganas TENDRÁS x3.12 acumulado total.
#
# Lógica:
#   self.multiplier  → acumulado actual (empieza en 1.0)
#   round_factor()   → factor de este turno (p.ej. 2.06)
#   resultado        → self.multiplier * round_factor()
#   Botón muestra    → "x{resultado}" (el TOTAL que tendrías)
# ============================================================

import discord
from discord.ext import commands
from discord import app_commands
import random
_rng = random.SystemRandom()
from utils import (
    parse_amount,
    check_linked, check_balance, fmt_gems,
    error_embed, update_wager_roles,
    COLOR_GOLD, COLOR_ERROR, COLOR_INFO, COLOR_PURPLE
)

CARD_NAMES = {
    1: "A", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6",
    7: "7", 8: "8", 9: "9", 10: "10", 11: "J", 12: "Q", 13: "K"
}
CARD_SUITS = ["♠️", "♥️", "♦️", "♣️"]

def random_card() -> tuple:
    return _rng.randint(1, 13), _rng.choice(CARD_SUITS)

def card_name(value: int, suit: str) -> str:
    return f"{CARD_NAMES[value]}{suit}"

def round_factor(card_val: int, choice: str, edge: float):
    """
    Factor multiplicador para este turno.
    Si ganas, tu acumulado se multiplica por este valor.
    Retorna None si la jugada es imposible (A→Menor, K→Mayor).
    """
    win_cards = (13 - card_val) if choice == "hi" else (card_val - 1)
    if win_cards <= 0:
        return None
    prob = win_cards / 13
    return round((1.0 / prob) * (1 - edge / 100), 2)

def win_prob_pct(card_val: int, choice: str) -> str:
    win_cards = (13 - card_val) if choice == "hi" else (card_val - 1)
    if win_cards <= 0:
        return "0%"
    return f"{win_cards / 13 * 100:.0f}%"


class HiLoView(discord.ui.View):
    """
    Botones con el multiplicador TOTAL resultante visible.
    Si estás en x2.00 y el turno da factor x2.06,
    el botón muestra "→ x4.12" (no "→ x2.06").
    """

    def __init__(self, game):
        super().__init__(timeout=600)
        self.game = game
        self._update_buttons()

    def _update_buttons(self):
        v           = self.game.current_val
        edge        = self.game.house_edge
        accumulated = self.game.multiplier
        self.clear_items()

        # Botón Mayor
        hi_f   = round_factor(v, "hi", edge)
        hi_res = round(accumulated * hi_f, 2) if hi_f else None
        hi_btn = discord.ui.Button(
            label     = (f"🔼 Mayor  {win_prob_pct(v,'hi')}  →  x{hi_res:.2f}"
                         if hi_res else "🔼 Mayor (imposible)"),
            style     = discord.ButtonStyle.primary if hi_f else discord.ButtonStyle.secondary,
            disabled  = hi_f is None,
            custom_id = "hilo_hi"
        )
        hi_btn.callback = self._hi_callback
        self.add_item(hi_btn)

        # Botón Menor
        lo_f   = round_factor(v, "lo", edge)
        lo_res = round(accumulated * lo_f, 2) if lo_f else None
        lo_btn = discord.ui.Button(
            label     = (f"🔽 Menor  {win_prob_pct(v,'lo')}  →  x{lo_res:.2f}"
                         if lo_res else "🔽 Menor (imposible)"),
            style     = discord.ButtonStyle.primary if lo_f else discord.ButtonStyle.secondary,
            disabled  = lo_f is None,
            custom_id = "hilo_lo"
        )
        lo_btn.callback = self._lo_callback
        self.add_item(lo_btn)

        # Botón Cobrar (desactivado en el primer turno antes de jugar)
        pot = int(round(self.game.bet * accumulated, 0))
        cashout_btn = discord.ui.Button(
            label     = f"💰 Cobrar {fmt_gems(pot)}",
            style     = discord.ButtonStyle.success,
            custom_id = "hilo_cashout",
            disabled  = self.game.round == 1
        )
        cashout_btn.callback = self._cashout_callback
        self.add_item(cashout_btn)

    async def _hi_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.game.player_id:
            await interaction.response.send_message("Esta no es tu partida.", ephemeral=True)
            return
        await self.game.play_round(interaction, "hi")

    async def _lo_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.game.player_id:
            await interaction.response.send_message("Esta no es tu partida.", ephemeral=True)
            return
        await self.game.play_round(interaction, "lo")

    async def _cashout_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.game.player_id:
            await interaction.response.send_message("Esta no es tu partida.", ephemeral=True)
            return
        await self.game.cashout(interaction)

    async def on_timeout(self):
        """10 min — auto-cobra si hay multiplicador, devuelve apuesta si no."""
        game = self.game
        game.cog.active_games.pop(game.player_id, None)
        if game.multiplier > 1.0:
            payout = int(round(game.bet * game.multiplier, 0))
            await game.bot.db.add_balance(str(game.player_id), payout)
        elif game.round == 1:
            await game.bot.db.add_balance(str(game.player_id), game.bet)
        for item in self.children:
            item.disabled = True
        try:
            if game.message:
                payout = int(round(game.bet * game.multiplier, 0))
                embed = discord.Embed(
                    title="⏰ Hi-Lo — Tiempo agotado",
                    description=f"Partida expirada. Cobrado automáticamente: {fmt_gems(payout)}",
                    color=0x95a5a6
                )
                await game.message.edit(embed=embed, view=self)
        except Exception:
            pass


class HiLoGame:
    """
    Partida de Hi-Lo.

    Multiplicadores:
      self.multiplier  = acumulado (starts at 1.0)
      round_factor()   = factor del turno según probabilidad con edge
      on win:          self.multiplier *= round_factor()
      payout:          bet * self.multiplier
    """

    def __init__(self, bot, cog, player: discord.Member, bet: int, house_edge: float):
        self.bot         = bot
        self.cog         = cog
        self.player      = player
        self.player_id   = player.id
        self.bet         = bet
        self.house_edge  = house_edge
        self.current_val, self.current_suit = random_card()
        self.multiplier  = 1.0
        self.round       = 1
        self.message     = None

    def build_embed(self, prev_card=None, next_card=None, result=None) -> discord.Embed:
        v   = self.current_val
        acc = self.multiplier
        edge = self.house_edge

        if result and "❌" in result:
            color = COLOR_ERROR
        elif result and ("✅" in result or "cobrado" in result.lower()):
            color = COLOR_GOLD
        else:
            color = COLOR_PURPLE

        pot = int(round(self.bet * acc, 0))
        embed = discord.Embed(title=f"🎴 Hi-Lo  —  {self.player.display_name}", color=color)
        embed.add_field(name="Carta actual",          value=f"## {card_name(v, self.current_suit)}", inline=True)
        embed.add_field(name="Turno",                 value=f"**#{self.round}**",   inline=True)
        embed.add_field(name="Multiplicador",         value=f"**x{acc:.2f}**",      inline=True)
        embed.add_field(name="Apuesta original",      value=fmt_gems(self.bet),     inline=True)
        embed.add_field(name="💰 Si cobras ahora",    value=fmt_gems(pot),          inline=True)
        embed.add_field(name="\u200b",                value="\u200b",               inline=True)

        # Si ganas mayor/menor → muestra el total que tendrías
        hi_f = round_factor(v, "hi", edge)
        lo_f = round_factor(v, "lo", edge)
        if hi_f:
            hi_total = round(acc * hi_f, 2)
            hi_pot   = int(round(self.bet * hi_total, 0))
            embed.add_field(
                name=f"🔼 Si Mayor ({win_prob_pct(v,'hi')}) → x{hi_total:.2f}",
                value=fmt_gems(hi_pot), inline=True
            )
        if lo_f:
            lo_total = round(acc * lo_f, 2)
            lo_pot   = int(round(self.bet * lo_total, 0))
            embed.add_field(
                name=f"🔽 Si Menor ({win_prob_pct(v,'lo')}) → x{lo_total:.2f}",
                value=fmt_gems(lo_pot), inline=True
            )

        if prev_card and next_card:
            embed.add_field(name="Turno anterior", value=f"{prev_card} → **{next_card}**", inline=False)
        if result:
            embed.add_field(name="Estado", value=result, inline=False)

        return embed

    async def play_round(self, interaction: discord.Interaction, choice: str):
        prev_val  = self.current_val
        prev_card = card_name(self.current_val, self.current_suit)

        new_val, new_suit = random_card()
        next_card = card_name(new_val, new_suit)

        won = (new_val > prev_val) if choice == "hi" else (new_val < prev_val)
        if new_val == prev_val:
            won = False   # Empate = pierde

        if won:
            factor           = round_factor(prev_val, choice, self.house_edge)
            self.multiplier  = round(self.multiplier * factor, 2)
            self.current_val  = new_val
            self.current_suit = new_suit
            self.round       += 1

            pot         = int(round(self.bet * self.multiplier, 0))
            result_text = f"✅ **¡Correcto!** x{self.multiplier:.2f} acumulado → cobrarías {fmt_gems(pot)}"
            view  = HiLoView(self)
            embed = self.build_embed(prev_card, next_card, result_text)
            await interaction.response.edit_message(embed=embed, view=view)

        else:
            self.cog.active_games.pop(self.player_id, None)

            # Rakeback
            edge_pct     = await self.bot.db.get_house_edge("hilo")
            house_profit = int(self.bet * edge_pct / 100)
            rakeback_pct = float(await self.bot.db.get_config("rakeback_pct") or "20")
            rakeback_amt = int(house_profit * rakeback_pct / 100)
            if rakeback_amt > 0:
                await self.bot.db.add_rakeback(str(self.player_id), rakeback_amt)

            await self.bot.db.log_game(str(self.player_id), "hilo", self.bet, "lose", -self.bet)
            member = interaction.guild.get_member(self.player_id)
            if member:
                await update_wager_roles(self.bot, interaction.guild, member)

            result_text = (
                f"❌ **¡Incorrecto!** La carta era **{next_card}**.\n"
                f"Perdiste {fmt_gems(self.bet)} (llegaste a x{self.multiplier:.2f})"
            )
            self.current_val  = new_val
            self.current_suit = new_suit

            view = HiLoView(self)
            for item in view.children:
                item.disabled = True
            embed = self.build_embed(prev_card, next_card, result_text)
            await interaction.response.edit_message(embed=embed, view=view)

    async def cashout(self, interaction: discord.Interaction):
        self.cog.active_games.pop(self.player_id, None)

        payout = int(round(self.bet * self.multiplier, 0))
        profit = payout - self.bet

        await self.bot.db.add_balance(str(self.player_id), payout)
        result = "win" if profit > 0 else "tie"
        await self.bot.db.log_game(str(self.player_id), "hilo", self.bet, result, profit)

        member = interaction.guild.get_member(self.player_id)
        if member:
            await update_wager_roles(self.bot, interaction.guild, member)

        new_bal = await self.bot.db.get_balance(str(self.player_id))
        embed = discord.Embed(
            title="💰 Hi-Lo — Cobrado",
            description=(
                f"Apostaste {fmt_gems(self.bet)} · Multiplicador final: **x{self.multiplier:.2f}**\n"
                f"**Cobras: {fmt_gems(payout)}** · Ganancia: {fmt_gems(profit)}"
            ),
            color=COLOR_GOLD
        )
        embed.set_footer(text=f"Saldo: {fmt_gems(new_bal)}")
        view = HiLoView(self)
        for item in view.children:
            item.disabled = True
        await interaction.response.edit_message(embed=embed, view=view)


class HiLo(commands.Cog):

    def __init__(self, bot):
        self.bot          = bot
        self.active_games = {}

    @app_commands.command(name="hilo", description="Adivina si la siguiente carta será mayor o menor")
    @app_commands.describe(apuesta="Gemas a apostar (ej: 500k, 1m, 2.5b)")
    async def hilo(self, interaction: discord.Interaction, apuesta: str):
        if not await check_linked(interaction):
            return

        amount = parse_amount(str(apuesta))
        if not amount or amount <= 0:
            await interaction.response.send_message(
                embed=error_embed("Apuesta inválida. Usa K/M/B (ej: 500k, 1m, 2.5b)"),
                ephemeral=True
            )
            return

        if interaction.user.id in self.active_games:
            await interaction.response.send_message(
                embed=error_embed("Ya tienes una partida activa de Hi-Lo."), ephemeral=True
            )
            return

        if not await check_balance(interaction, amount):
            return

        user_id = str(interaction.user.id)
        await self.bot.db.remove_balance(user_id, amount)
        await self.bot.db.add_wager(user_id, amount)
        await self.bot.db.reduce_wager_requirement(user_id, amount)

        edge = await self.bot.db.get_house_edge("hilo")
        game = HiLoGame(self.bot, self, interaction.user, amount, edge)
        self.active_games[interaction.user.id] = game

        embed = game.build_embed()
        view  = HiLoView(game)
        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()
        game.message = msg


async def setup(bot):
    await bot.add_cog(HiLo(bot))
