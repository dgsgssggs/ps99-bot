# ============================================================
# cogs/games/hilo.py — Juego Hi-Lo (Mayor/Menor)
# ============================================================
# Se revela una carta. El jugador adivina si la siguiente
# será mayor (Hi) o menor (Lo). Juego de rondas continuas.
# El multiplicador sube con cada acierto consecutivo.
# ============================================================

import discord
from discord.ext import commands
from discord import app_commands
import random
import secrets
_rng = random.SystemRandom()
from utils import (
    apply_rakeback,
    check_linked, check_balance, fmt_gems,
    error_embed, update_wager_roles,
    COLOR_GOLD, COLOR_ERROR, COLOR_INFO
)

# ── Baraja simplificada (valores del 1 al 13) ─────────────────
# 1=As, 11=J, 12=Q, 13=K
CARD_NAMES = {
    1: "A", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6",
    7: "7", 8: "8", 9: "9", 10: "10", 11: "J", 12: "Q", 13: "K"
}

CARD_SUITS = ["♠️", "♥️", "♦️", "♣️"]        # Palos de la baraja

def random_card() -> tuple:
    """Genera una carta aleatoria: (valor, palo)."""
    value = _rng.randint(1, 13)
    suit  = _rng.choice(CARD_SUITS)
    return value, suit

def card_name(value: int, suit: str) -> str:
    """Formatea la carta como texto: ej. 'A♠️' o '10♥️'"""
    return f"{CARD_NAMES[value]}{suit}"


# ── Vista de botones Hi-Lo ────────────────────────────────────
class HiLoView(discord.ui.View):
    """Botones: Mayor (Hi), Menor (Lo) y Cobrar."""

    def __init__(self, game):
        super().__init__(timeout=120)
        self.game = game

    async def on_timeout(self):
        """Tiempo agotado: limpia la partida."""
        self.game.cog.active_games.pop(self.game.player_id, None)
        for item in self.children:
            item.disabled = True
        try:
            await self.game.message.edit(view=self)
        except Exception:
            pass

    @discord.ui.button(label="🔼 Mayor (Hi)", style=discord.ButtonStyle.primary)
    async def hi(self, interaction: discord.Interaction, button: discord.ui.Button):
        """El jugador apuesta que la siguiente carta es mayor."""
        if interaction.user.id != self.game.player_id:
            await interaction.response.send_message("Esta no es tu partida.", ephemeral=True)
            return
        await self.game.play_round(interaction, "hi")

    @discord.ui.button(label="🔽 Menor (Lo)", style=discord.ButtonStyle.primary)
    async def lo(self, interaction: discord.Interaction, button: discord.ui.Button):
        """El jugador apuesta que la siguiente carta es menor."""
        if interaction.user.id != self.game.player_id:
            await interaction.response.send_message("Esta no es tu partida.", ephemeral=True)
            return
        await self.game.play_round(interaction, "lo")

    @discord.ui.button(label="💰 Cobrar", style=discord.ButtonStyle.success)
    async def cashout(self, interaction: discord.Interaction, button: discord.ui.Button):
        """El jugador cobra sus ganancias actuales."""
        if interaction.user.id != self.game.player_id:
            await interaction.response.send_message("Esta no es tu partida.", ephemeral=True)
            return
        await self.game.cashout(interaction)


# ── Clase de partida Hi-Lo ────────────────────────────────────
class HiLoGame:
    """Representa una partida activa de Hi-Lo."""

    def __init__(self, bot, cog, player: discord.Member, bet: int, house_edge: float):
        self.bot         = bot
        self.cog         = cog                  # Referencia al cog para limpiar active_games
        self.player      = player
        self.player_id   = player.id
        self.bet         = bet
        self.house_edge  = house_edge
        self.current_val, self.current_suit = random_card()
        self.multiplier  = 1.0
        self.round       = 1

    def build_embed(self, prev_card: str = None, next_card: str = None, result: str = None):
        """Construye el embed del estado actual del juego."""
        color = COLOR_INFO if not result else (COLOR_GOLD if "ganaste" in result.lower() else COLOR_ERROR)

        embed = discord.Embed(
            title=f"🎴 Hi-Lo — {self.player.display_name}",
            color=color
        )

        # Carta actual que el jugador ve
        embed.add_field(
            name="Carta Actual",
            value=f"**{card_name(self.current_val, self.current_suit)}**",
            inline=True
        )

        embed.add_field(name="Ronda",        value=f"#{self.round}",                       inline=True)
        embed.add_field(name="Multiplicador", value=f"x{self.multiplier:.2f}",             inline=True)
        embed.add_field(name="Apuesta Base",  value=fmt_gems(self.bet),                    inline=True)

        # Ganancia potencial actual
        potential = int(self.bet * self.multiplier)
        embed.add_field(name="Ganancia Potencial", value=fmt_gems(potential),              inline=True)

        # Muestra la carta anterior y la nueva si aplica
        if prev_card and next_card:
            embed.add_field(
                name="Resultado de la Ronda",
                value=f"{prev_card} → {next_card}",
                inline=False
            )

        if result:
            embed.add_field(name="Estado", value=result, inline=False)

        return embed

    async def play_round(self, interaction: discord.Interaction, choice: str):
        """Juega una ronda. choice='hi' o 'lo'."""
        prev_val  = self.current_val               # Valor de la carta anterior
        prev_card = card_name(self.current_val, self.current_suit)

        # Genera la siguiente carta
        new_val, new_suit = random_card()
        next_card = card_name(new_val, new_suit)

        # Determina si ganó la ronda
        if choice == "hi":
            won = new_val > prev_val               # La nueva es mayor
        else:
            won = new_val < prev_val               # La nueva es menor

        # Si son iguales (empate), pierde automáticamente
        if new_val == prev_val:
            won = False

        if won:
            # Sube el multiplicador con cada acierto (ajustado por house edge)
            edge_factor    = 1 - (self.house_edge / 100)     # Factor de reducción
            self.multiplier += 0.5 * edge_factor              # +0.5x ajustado por edge
            self.current_val  = new_val                       # Actualiza la carta actual
            self.current_suit = new_suit
            self.round       += 1                             # Siguiente ronda

            result_text = f"✅ ¡Correcto! Multiplicador: x{self.multiplier:.2f}"
            embed = self.build_embed(prev_card, next_card, result_text)
            view  = HiLoView(self)
            await interaction.response.edit_message(embed=embed, view=view)

        else:
            # Pierde — LIMPIA active_games inmediatamente
            self.cog.active_games.pop(self.player_id, None)

            self.current_val  = new_val
            self.current_suit = new_suit
            result_text = f"❌ ¡Incorrecto! Perdiste {fmt_gems(self.bet)}"

            embed = self.build_embed(prev_card, next_card, result_text)
            view  = HiLoView(self)
            for item in view.children:
                item.disabled = True

            # Rakeback sobre el beneficio de la casa
            edge_pct_hl  = await self.bot.db.get_house_edge("hilo")
            house_profit = int(self.bet * edge_pct_hl / 100)
            rakeback_pct = float(await self.bot.db.get_config("rakeback_pct") or "20")
            rakeback_amt = int(house_profit * rakeback_pct / 100)
            if rakeback_amt > 0:
                await self.bot.db.add_rakeback(str(self.player_id), rakeback_amt)

            await self.bot.db.log_game(str(self.player_id), "hilo", self.bet, "lose", -self.bet)

            member = interaction.guild.get_member(self.player_id)
            if member:
                await update_wager_roles(self.bot, interaction.guild, member)

            await interaction.response.edit_message(embed=embed, view=view)

    async def cashout(self, interaction: discord.Interaction):
        """El jugador cobra sus ganancias acumuladas."""
        # Limpia active_games al cobrar
        self.cog.active_games.pop(self.player_id, None)

        payout  = int(self.bet * self.multiplier)
        profit  = payout - self.bet

        await self.bot.db.add_balance(str(self.player_id), payout)

        # Registra en logs
        result  = "win" if profit >= 0 else "tie"
        await self.bot.db.log_game(
            str(self.player_id), "hilo", self.bet, result, profit
        )

        # Actualiza roles
        member = interaction.guild.get_member(self.player_id)
        if member:
            await update_wager_roles(self.bot, interaction.guild, member)

        embed = discord.Embed(
            title="💰 Hi-Lo — Cobrado",
            description=(
                f"Apostaste {fmt_gems(self.bet)} y cobras {fmt_gems(payout)}.\n"
                f"Ganancia: {fmt_gems(profit)}"
            ),
            color=COLOR_GOLD
        )

        view = HiLoView(self)
        for item in view.children:
            item.disabled = True

        await interaction.response.edit_message(embed=embed, view=view)


# ── COG DE HI-LO ──────────────────────────────────────────────
class HiLo(commands.Cog):
    """Módulo del juego Hi-Lo."""

    def __init__(self, bot):
        self.bot          = bot
        self.active_games = {}                 # Partidas activas por usuario

    @app_commands.command(name="hilo", description="Adivina si la siguiente carta será mayor o menor")
    @app_commands.describe(apuesta="Cantidad de gemas a apostar")
    async def hilo(self, interaction: discord.Interaction, apuesta: int):
        """Inicia una partida de Hi-Lo."""
        if not await check_linked(interaction):
            return

        if apuesta <= 0:
            await interaction.response.send_message(
                embed=error_embed("La apuesta debe ser mayor a 0."), ephemeral=True
            )
            return

        if interaction.user.id in self.active_games:
            await interaction.response.send_message(
                embed=error_embed("Ya tienes una partida activa."), ephemeral=True
            )
            return

        if not await check_balance(interaction, apuesta):
            return

        user_id = str(interaction.user.id)

        # Descuenta la apuesta
        await self.bot.db.remove_balance(user_id, apuesta)
        await self.bot.db.add_wager(user_id, apuesta)

        # Obtiene el house edge
        edge = await self.bot.db.get_house_edge("hilo")

        # Crea la partida pasando self (cog) para limpiar active_games
        game = HiLoGame(self.bot, self, interaction.user, apuesta, edge)
        self.active_games[interaction.user.id] = game

        embed = game.build_embed()
        view  = HiLoView(game)

        await interaction.response.send_message(embed=embed, view=view)

        # Limpia la partida al terminar
        def on_done():
            self.active_games.pop(interaction.user.id, None)
        game.on_done = on_done


async def setup(bot):
    await bot.add_cog(HiLo(bot))
