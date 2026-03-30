# ============================================================
# cogs/games/hilo.py — Hi-Lo
# ============================================================
# Spec exacto:
#   1. Carta inicial aleatoria 1-13 (uniforme, baraja infinita)
#   2. Jugador elige Higher o Lower
#   3. Se saca otra carta 1-13 de forma independiente
#   4. Resolución:
#      - Higher → gana si next > current
#      - Lower  → gana si next < current
#      - Empate → PIERDE siempre (house rule)
#   5. Si gana: next card pasa a ser la current card, puede seguir o cobrar
#   6. Si pierde: fin, pierde la apuesta original
#
# Probabilidades (carta actual = X):
#   P(Higher) = (13 - X) / 13
#   P(Lower)  = (X - 1)  / 13
#   P(Empate) = 1/13      (siempre pierde)
#
# Payout con 5% house edge:
#   payout_mult_del_turno = (1 / P(win)) * 0.95
#   acumulado = producto de todos los turnos ganados
#   cobras    = apuesta_original * acumulado
#
# Edge cards:
#   Carta = 1 (As)  → Lower imposible (0 cartas menores) → botón desactivado
#   Carta = 13 (K)  → Higher imposible (0 cartas mayores) → botón desactivado
# ============================================================

import discord
from discord.ext import commands
from discord import app_commands
import random
_rng = random.SystemRandom()
from utils import (
    parse_amount, check_linked, check_balance, fmt_gems,
    error_embed, update_wager_roles,
    COLOR_GOLD, COLOR_ERROR, COLOR_PURPLE
)

# ── Cartas ────────────────────────────────────────────────────
CARD_NAMES = {
    1: "A", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6",
    7: "7", 8: "8", 9: "9", 10: "10", 11: "J", 12: "Q", 13: "K"
}
CARD_SUITS = ["♠️", "♥️", "♦️", "♣️"]


def draw_card() -> tuple[int, str]:
    """Saca una carta aleatoria e independiente (1-13 uniforme, baraja infinita)."""
    return _rng.randint(1, 13), _rng.choice(CARD_SUITS)


def card_str(val: int, suit: str) -> str:
    return f"**{CARD_NAMES[val]}{suit}**"


# ── Cálculos de probabilidad y payout ────────────────────────

def win_prob(current: int, choice: str) -> float:
    """P(Higher) = (13-X)/13  |  P(Lower) = (X-1)/13"""
    if choice == "hi":
        return (13 - current) / 13
    else:
        return (current - 1) / 13


def turn_factor(current: int, choice: str, edge_pct: float) -> float | None:
    """
    Multiplicador de un turno ganado.
    = (1 / P(win)) * (1 - edge/100)
    Retorna None si P(win) == 0 (jugada imposible).
    """
    p = win_prob(current, choice)
    if p <= 0:
        return None
    return round((1.0 / p) * (1 - edge_pct / 100), 4)


def prob_pct(current: int, choice: str) -> str:
    """Probabilidad en % redondeada para mostrar en botones."""
    p = win_prob(current, choice)
    return f"{p * 100:.0f}%"


# ── Vista (botones) ───────────────────────────────────────────

class HiLoView(discord.ui.View):
    """
    Botones: 🔼 Mayor | 🔽 Menor | 💰 Cobrar
    Los botones Higher/Lower muestran el multiplicador TOTAL
    que tendrías si ganas (acumulado * factor_del_turno).
    """

    def __init__(self, game: "HiLoGame"):
        super().__init__(timeout=600)
        self.game = game
        self._rebuild()

    def _rebuild(self):
        self.clear_items()
        g   = self.game
        acc = g.multiplier
        cur = g.current_val

        # Botón Mayor
        hi_f  = turn_factor(cur, "hi", g.edge)
        hi_res = round(acc * hi_f, 2) if hi_f else None
        hi_btn = discord.ui.Button(
            label    = (f"🔼 Mayor  {prob_pct(cur,'hi')}  →  x{hi_res:.2f}"
                        if hi_res else "🔼 Mayor (imposible — K)"),
            style    = discord.ButtonStyle.primary if hi_f else discord.ButtonStyle.secondary,
            disabled = hi_f is None,
            custom_id= "hilo_hi"
        )
        hi_btn.callback = self._hi
        self.add_item(hi_btn)

        # Botón Menor
        lo_f  = turn_factor(cur, "lo", g.edge)
        lo_res = round(acc * lo_f, 2) if lo_f else None
        lo_btn = discord.ui.Button(
            label    = (f"🔽 Menor  {prob_pct(cur,'lo')}  →  x{lo_res:.2f}"
                        if lo_res else "🔽 Menor (imposible — A)"),
            style    = discord.ButtonStyle.primary if lo_f else discord.ButtonStyle.secondary,
            disabled = lo_f is None,
            custom_id= "hilo_lo"
        )
        lo_btn.callback = self._lo
        self.add_item(lo_btn)

        # Botón Cobrar (desactivado en el turno 1, antes de ganar algo)
        pot = int(round(g.bet * acc, 0))
        cashout_btn = discord.ui.Button(
            label    = f"💰 Cobrar  {fmt_gems(pot)}",
            style    = discord.ButtonStyle.success,
            custom_id= "hilo_cashout",
            disabled = g.turn == 1
        )
        cashout_btn.callback = self._cashout
        self.add_item(cashout_btn)

    async def _hi(self, interaction: discord.Interaction):
        if interaction.user.id != self.game.player_id:
            return await interaction.response.send_message("Esta no es tu partida.", ephemeral=True)
        await self.game.resolve(interaction, "hi")

    async def _lo(self, interaction: discord.Interaction):
        if interaction.user.id != self.game.player_id:
            return await interaction.response.send_message("Esta no es tu partida.", ephemeral=True)
        await self.game.resolve(interaction, "lo")

    async def _cashout(self, interaction: discord.Interaction):
        if interaction.user.id != self.game.player_id:
            return await interaction.response.send_message("Esta no es tu partida.", ephemeral=True)
        await self.game.cashout(interaction)

    async def on_timeout(self):
        """10 min sin acción — cobra automáticamente lo acumulado."""
        g = self.game
        g.cog.active_games.pop(g.player_id, None)
        payout = int(round(g.bet * g.multiplier, 0)) if g.multiplier > 1.0 else g.bet
        await g.bot.db.add_balance(str(g.player_id), payout)
        for item in self.children:
            item.disabled = True
        try:
            if g.message:
                embed = discord.Embed(
                    title="⏰ Hi-Lo — Tiempo agotado",
                    description=f"Cobrado automáticamente: {fmt_gems(payout)}",
                    color=0x95a5a6
                )
                await g.message.edit(embed=embed, view=self)
        except Exception:
            pass


# ── Lógica del juego ──────────────────────────────────────────

class HiLoGame:
    """
    Estado de una partida de Hi-Lo.

    self.multiplier: acumulado desde el inicio (empieza en 1.0).
      Cada turno ganado → self.multiplier *= turn_factor()
      Cobrar → payout = bet * self.multiplier
    """

    def __init__(self, bot, cog, player: discord.Member, bet: int, edge: float):
        self.bot        = bot
        self.cog        = cog
        self.player     = player
        self.player_id  = player.id
        self.bet        = bet
        self.edge       = edge
        self.multiplier = 1.0
        self.turn       = 1          # turno actual (1 = primer turno, antes de jugar)
        self.message    = None

        # Carta inicial
        self.current_val, self.current_suit = draw_card()

    # ── Embed ─────────────────────────────────────────────────

    def build_embed(self, prev: str = None, nxt: str = None, status: str = None) -> discord.Embed:
        acc  = self.multiplier
        cur  = self.current_val
        pot  = int(round(self.bet * acc, 0))

        if status and "❌" in status:
            color = COLOR_ERROR
        elif status and ("✅" in status or "cobrado" in status.lower()):
            color = COLOR_GOLD
        else:
            color = COLOR_PURPLE

        embed = discord.Embed(title=f"🎴 Hi-Lo  —  {self.player.display_name}", color=color)

        embed.add_field(name="Carta actual",         value=f"# {CARD_NAMES[cur]}{self.current_suit}", inline=True)
        embed.add_field(name="Multiplicador",        value=f"**x{acc:.2f}**",                         inline=True)
        embed.add_field(name="💰 Si cobras ahora",   value=fmt_gems(pot),                              inline=True)
        embed.add_field(name="Apuesta original",     value=fmt_gems(self.bet),                         inline=True)

        # Payouts esperados de Mayor / Menor (multiplicador TOTAL resultante)
        hi_f = turn_factor(cur, "hi", self.edge)
        lo_f = turn_factor(cur, "lo", self.edge)
        if hi_f:
            hi_tot = round(acc * hi_f, 2)
            embed.add_field(
                name=f"🔼 Mayor ({prob_pct(cur,'hi')}) → x{hi_tot:.2f}",
                value=fmt_gems(int(round(self.bet * hi_tot, 0))),
                inline=True
            )
        if lo_f:
            lo_tot = round(acc * lo_f, 2)
            embed.add_field(
                name=f"🔽 Menor ({prob_pct(cur,'lo')}) → x{lo_tot:.2f}",
                value=fmt_gems(int(round(self.bet * lo_tot, 0))),
                inline=True
            )

        if prev and nxt:
            embed.add_field(name="Turno anterior", value=f"{prev} → {nxt}", inline=False)
        if status:
            embed.add_field(name="Estado", value=status, inline=False)

        return embed

    # ── Resolución de turno ───────────────────────────────────

    async def resolve(self, interaction: discord.Interaction, choice: str):
        """
        Spec:
          1. Guarda carta actual
          2. Saca nueva carta (independiente, 1-13 uniforme)
          3. Compara:
             - Higher: new > current → gana
             - Lower:  new < current → gana
             - Equal:  new == current → PIERDE (siempre)
          4. Si gana: acumula multiplicador, new card → current card, sigue
          5. Si pierde: fin
        """
        prev_val  = self.current_val
        prev_suit = self.current_suit
        prev_str  = card_str(prev_val, prev_suit)

        # Paso 2 — nueva carta independiente
        new_val, new_suit = draw_card()
        new_str = card_str(new_val, new_suit)

        # Paso 3 — resolver outcome (empate siempre pierde)
        if new_val == prev_val:
            won = False
            tie = True
        elif choice == "hi":
            won = new_val > prev_val
            tie = False
        else:
            won = new_val < prev_val
            tie = False

        if won:
            # Paso 4 — acumula y actualiza
            factor          = turn_factor(prev_val, choice, self.edge)
            self.multiplier = round(self.multiplier * factor, 4)
            # Redondea a 2 decimales para mostrar
            self.multiplier = round(self.multiplier, 2)
            self.current_val  = new_val
            self.current_suit = new_suit
            self.turn        += 1

            pot    = int(round(self.bet * self.multiplier, 0))
            status = f"✅ **¡Correcto!** Multiplicador acumulado: **x{self.multiplier:.2f}** → {fmt_gems(pot)}"
            view   = HiLoView(self)
            embed  = self.build_embed(prev_str, new_str, status)
            await interaction.response.edit_message(embed=embed, view=view)

        else:
            # Paso 5 — fin de partida
            self.cog.active_games.pop(self.player_id, None)

            tie_text = " (empate)" if tie else ""
            status = (
                f"❌ **Incorrecto{tie_text}.** La carta era {new_str}.\n"
                f"Perdiste {fmt_gems(self.bet)} · Llegaste a **x{self.multiplier:.2f}**"
            )

            # Rakeback
            edge_pct = await self.bot.db.get_house_edge("hilo")
            rb_pct   = float(await self.bot.db.get_config("rakeback_pct") or "20")
            rb_amt   = int(int(self.bet * edge_pct / 100) * rb_pct / 100)
            if rb_amt > 0:
                await self.bot.db.add_rakeback(str(self.player_id), rb_amt)

            await self.bot.db.log_game(str(self.player_id), "hilo", self.bet, "lose", -self.bet)
            member = interaction.guild.get_member(self.player_id)
            if member:
                await update_wager_roles(self.bot, interaction.guild, member)

            # Actualiza carta para el embed (muestra la carta perdedora)
            self.current_val  = new_val
            self.current_suit = new_suit

            view = HiLoView(self)
            for item in view.children:
                item.disabled = True

            embed = self.build_embed(prev_str, new_str, status)
            await interaction.response.edit_message(embed=embed, view=view)

    # ── Cobrar ────────────────────────────────────────────────

    async def cashout(self, interaction: discord.Interaction):
        self.cog.active_games.pop(self.player_id, None)

        payout = int(round(self.bet * self.multiplier, 0))
        profit = payout - self.bet

        await self.bot.db.add_balance(str(self.player_id), payout)
        await self.bot.db.log_game(
            str(self.player_id), "hilo", self.bet,
            "win" if profit > 0 else "tie", profit
        )

        member = interaction.guild.get_member(self.player_id)
        if member:
            await update_wager_roles(self.bot, interaction.guild, member)

        new_bal = await self.bot.db.get_balance(str(self.player_id))
        embed = discord.Embed(
            title="💰 Hi-Lo — Cobrado",
            description=(
                f"Apuesta: {fmt_gems(self.bet)} · Multiplicador final: **x{self.multiplier:.2f}**\n"
                f"**Cobras: {fmt_gems(payout)}** · Ganancia: {fmt_gems(profit)}"
            ),
            color=COLOR_GOLD
        )
        embed.set_footer(text=f"Saldo: {fmt_gems(new_bal)}")

        view = HiLoView(self)
        for item in view.children:
            item.disabled = True
        await interaction.response.edit_message(embed=embed, view=view)


# ── Cog ───────────────────────────────────────────────────────

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
        game.message = await interaction.original_response()


async def setup(bot):
    await bot.add_cog(HiLo(bot))
