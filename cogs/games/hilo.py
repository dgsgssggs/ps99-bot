# ============================================================
# cogs/games/hilo.py — Juego Hi-Lo (Mayor/Menor)
# ============================================================
# Multiplicador basado en probabilidad real de cada carta.
# Hi con un 2 vale mucho menos que Hi con un Q.
# Cada ronda multiplica el acumulado por el multiplicador
# de la siguiente apuesta (no es plano +0.5x).
# ============================================================

import discord
from discord.ext import commands
from discord import app_commands
import random
import secrets
_rng = random.SystemRandom()
from utils import (
    parse_amount,
    check_linked, check_balance, fmt_gems,
    error_embed, update_wager_roles,
    COLOR_GOLD, COLOR_ERROR, COLOR_INFO
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

def round_multiplier(card_val: int, choice: str, edge: float) -> float:
    """
    Calcula el multiplicador de una ronda basado en la probabilidad real.
    Hay 13 cartas posibles (1-13). Empates pierden.
    Hi con K = imposible (mult infinito → botón desactivado).
    Lo con A = imposible (mult infinito → botón desactivado).
    """
    if choice == "hi":
        win_cards = 13 - card_val          # cartas estrictamente mayores
    else:
        win_cards = card_val - 1           # cartas estrictamente menores

    if win_cards <= 0:
        return None                        # Imposible — botón desactivado

    prob = win_cards / 13
    fair = 1.0 / prob
    return round(fair * (1 - edge / 100), 2)

def win_prob_pct(card_val: int, choice: str) -> str:
    """Devuelve la probabilidad en % para mostrar en el embed."""
    if choice == "hi":
        win_cards = 13 - card_val
    else:
        win_cards = card_val - 1
    if win_cards <= 0:
        return "0%"
    return f"{win_cards/13*100:.0f}%"


class HiLoView(discord.ui.View):
    """Botones Hi / Lo / Cobrar con probabilidades dinámicas."""

    def __init__(self, game):
        super().__init__(timeout=600)
        self.game = game
        self._update_buttons()

    def _update_buttons(self):
        """Actualiza etiquetas y disponibilidad de botones según la carta actual."""
        v     = self.game.current_val
        edge  = self.game.house_edge
        self.clear_items()

        # Botón Higher
        hi_mult = round_multiplier(v, "hi", edge)
        hi_btn  = discord.ui.Button(
            label     = f"🔼 Mayor  {win_prob_pct(v, 'hi')}  →  x{hi_mult:.2f}" if hi_mult else "🔼 Mayor (imposible)",
            style     = discord.ButtonStyle.primary if hi_mult else discord.ButtonStyle.secondary,
            disabled  = hi_mult is None,
            custom_id = "hilo_hi"
        )
        hi_btn.callback = self._hi_callback
        self.add_item(hi_btn)

        # Botón Lower
        lo_mult = round_multiplier(v, "lo", edge)
        lo_btn  = discord.ui.Button(
            label     = f"🔽 Menor  {win_prob_pct(v, 'lo')}  →  x{lo_mult:.2f}" if lo_mult else "🔽 Menor (imposible)",
            style     = discord.ButtonStyle.primary if lo_mult else discord.ButtonStyle.secondary,
            disabled  = lo_mult is None,
            custom_id = "hilo_lo"
        )
        lo_btn.callback = self._lo_callback
        self.add_item(lo_btn)

        # Botón Cobrar
        cashout_btn = discord.ui.Button(
            label     = "💰 Cobrar",
            style     = discord.ButtonStyle.success,
            custom_id = "hilo_cashout"
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
        """10 min sin actividad — termina la partida y devuelve la apuesta."""
        self.game.cog.active_games.pop(self.game.player_id, None)
        # Devuelve la apuesta si el jugador no cobró
        if self.game.bet > 0:
            await self.game.bot.db.add_balance(str(self.game.player_id), self.game.bet)
        for item in self.children:
            item.disabled = True
        try:
            msg = self.game.message
            if msg:
                embed = discord.Embed(
                    title="⏰ Hi-Lo — Tiempo agotado",
                    description="La partida expiró por inactividad. Se devuelve tu apuesta.",
                    color=0x95a5a6
                )
                await msg.edit(embed=embed, view=self)
        except Exception:
            pass


class HiLoGame:
    """Partida de Hi-Lo con multiplicadores basados en probabilidad real."""

    def __init__(self, bot, cog, player: discord.Member, bet: int, house_edge: float):
        self.bot         = bot
        self.cog         = cog
        self.player      = player
        self.player_id   = player.id
        self.bet         = bet
        self.house_edge  = house_edge
        self.current_val, self.current_suit = random_card()
        self.multiplier  = 1.0       # Multiplicador acumulado
        self.round       = 1
        self.message     = None

    def build_embed(self, prev_card=None, next_card=None, result=None):
        v     = self.current_val
        edge  = self.house_edge
        color = COLOR_INFO if not result else (COLOR_GOLD if "cobrado" in result.lower() or "correcto" in result.lower() else COLOR_ERROR)

        embed = discord.Embed(title=f"🎴 Hi-Lo — {self.player.display_name}", color=color)
        embed.add_field(name="Carta actual",    value=f"**{card_name(v, self.current_suit)}**", inline=True)
        embed.add_field(name="Ronda",           value=f"#{self.round}",                         inline=True)
        embed.add_field(name="Multiplicador",   value=f"x{self.multiplier:.2f}",                inline=True)
        embed.add_field(name="Apuesta base",    value=fmt_gems(self.bet),                        inline=True)

        pot = int(round(self.bet * self.multiplier, 0))
        embed.add_field(name="Si cobras ahora", value=fmt_gems(pot),                            inline=True)

        # Mostrar siguiente payout si acierta Hi o Lo
        hi_mult = round_multiplier(v, "hi", edge)
        lo_mult = round_multiplier(v, "lo", edge)
        if hi_mult:
            next_hi = int(round(self.bet * self.multiplier * hi_mult, 0))
            embed.add_field(name=f"Si acierta Mayor ({win_prob_pct(v, 'hi')})", value=fmt_gems(next_hi), inline=True)
        if lo_mult:
            next_lo = int(round(self.bet * self.multiplier * lo_mult, 0))
            embed.add_field(name=f"Si acierta Menor ({win_prob_pct(v, 'lo')})", value=fmt_gems(next_lo), inline=True)

        if prev_card and next_card:
            embed.add_field(name="Resultado ronda", value=f"{prev_card} → {next_card}", inline=False)
        if result:
            embed.add_field(name="Estado", value=result, inline=False)

        return embed

    async def play_round(self, interaction: discord.Interaction, choice: str):
        prev_val  = self.current_val
        prev_card = card_name(self.current_val, self.current_suit)

        new_val, new_suit = random_card()
        next_card = card_name(new_val, new_suit)

        if choice == "hi":
            won = new_val > prev_val
        else:
            won = new_val < prev_val

        # Empate siempre pierde
        if new_val == prev_val:
            won = False

        if won:
            # Multiplica el acumulado por el multiplicador de esta ronda
            r_mult          = round_multiplier(prev_val, choice, self.house_edge)
            self.multiplier = round(self.multiplier * r_mult, 2)
            self.current_val  = new_val
            self.current_suit = new_suit
            self.round       += 1

            result_text = f"✅ ¡Correcto! Multiplicador acumulado: x{self.multiplier:.2f}"
            view  = HiLoView(self)
            embed = self.build_embed(prev_card, next_card, result_text)
            await interaction.response.edit_message(embed=embed, view=view)

        else:
            # Pierde — limpia y da rakeback
            self.cog.active_games.pop(self.player_id, None)

            result_text = f"❌ ¡Incorrecto! Perdiste {fmt_gems(self.bet)}"
            view  = HiLoView(self)
            for item in view.children:
                item.disabled = True

            # Rakeback: % del house_profit, no del total apostado
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

            self.current_val  = new_val
            self.current_suit = new_suit
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

        embed = discord.Embed(
            title="💰 Hi-Lo — Cobrado",
            description=f"Apostaste {fmt_gems(self.bet)} y cobras {fmt_gems(payout)}.\nGanancia neta: {fmt_gems(profit)}",
            color=COLOR_GOLD
        )
        view = HiLoView(self)
        for item in view.children:
            item.disabled = True
        await interaction.response.edit_message(embed=embed, view=view)


class HiLo(commands.Cog):

    def __init__(self, bot):
        self.bot          = bot
        self.active_games = {}

    @app_commands.command(name="hilo", description="Adivina si la siguiente carta será mayor o menor")
    @app_commands.describe(apuesta="Cantidad de gemas a apostar")
    async def hilo(self, interaction: discord.Interaction, apuesta: str):
        if not await check_linked(interaction):
            return

        apuesta = parse_amount(str(apuesta))
        if not apuesta or apuesta <= 0:
            await interaction.response.send_message(
                embed=error_embed("La apuesta debe ser mayor a 0. Usa K/M/B"), ephemeral=True
            )
            return

        if interaction.user.id in self.active_games:
            await interaction.response.send_message(
                embed=error_embed("Ya tienes una partida activa de Hi-Lo."), ephemeral=True
            )
            return

        if not await check_balance(interaction, apuesta):
            return

        user_id = str(interaction.user.id)
        await self.bot.db.remove_balance(user_id, apuesta)
        await self.bot.db.add_wager(user_id, apuesta)
        await self.bot.db.reduce_wager_requirement(user_id, apuesta)

        edge = await self.bot.db.get_house_edge("hilo")
        game = HiLoGame(self.bot, self, interaction.user, apuesta, edge)
        self.active_games[interaction.user.id] = game

        embed = game.build_embed()
        view  = HiLoView(game)

        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()
        game.message = msg


async def setup(bot):
    await bot.add_cog(HiLo(bot))
