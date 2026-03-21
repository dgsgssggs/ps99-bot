# ============================================================
# cogs/games/mines.py — Minas con grid 5x5 de botones reales
# ============================================================
# - Grid de 5x5 = 25 botones, uno por casilla
# - Clic en casilla oculta ⬜ → la destapa
# - Si es mina 💥 → pierde
# - Si es gema 💎 → sigue jugando
# - Para hacer CASH OUT → pulsa una 💎 ya revelada
# - Footer siempre dice: "Pulsa una 💎 para cobrar"
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

GRID_SIZE = 25                          # 5x5 = 25 casillas


class MinesGame:
    """Estado de una partida activa de Minas."""

    def __init__(self, bot, cog, player, bet, num_mines, house_edge):
        self.bot        = bot
        self.cog        = cog
        self.player     = player
        self.player_id  = player.id
        self.bet        = bet
        self.num_mines  = num_mines
        self.house_edge = house_edge
        self.revealed   = [False] * GRID_SIZE   # True = casilla destapada
        self.is_mine    = [False] * GRID_SIZE   # True = hay mina aquí
        self.game_over  = False
        self.message    = None

        # Coloca las minas en posiciones aleatorias únicas
        for pos in _rng.sample(range(GRID_SIZE), num_mines):
            self.is_mine[pos] = True

        self.safe_total = GRID_SIZE - num_mines

    def safe_count(self):
        """Cuántas gemas (casillas seguras) ha destapado el jugador."""
        return sum(
            1 for i in range(GRID_SIZE)
            if self.revealed[i] and not self.is_mine[i]
        )

    def calc_multiplier(self, safe_revealed):
        """
        Multiplicador basado en la probabilidad de sobrevivir hasta este punto.
        Ajustado por el house edge configurado.
        """
        if safe_revealed == 0:
            return 1.0

        prob      = 1.0
        rem_cells = GRID_SIZE
        rem_mines = self.num_mines

        for _ in range(safe_revealed):
            safe_rem = rem_cells - rem_mines
            if safe_rem <= 0:
                break
            prob      *= safe_rem / rem_cells
            rem_cells -= 1

        if prob <= 0:
            return 1.0

        fair = 1.0 / prob
        adj  = fair * (1 - self.house_edge / 100)
        return round(max(1.01, adj), 2)

    def build_embed(self, result_text=None):
        """Construye el embed informativo con stats de la partida."""
        safe = self.safe_count()
        mult = self.calc_multiplier(safe)
        pot  = int(round(self.bet * mult, 0))

        if result_text:
            color = COLOR_GOLD if "💰" in result_text else COLOR_ERROR
        else:
            color = COLOR_INFO

        embed = discord.Embed(
            title=f"💣 Minas — {self.player.display_name}",
            color=color
        )
        embed.add_field(name="Apuesta",         value=fmt_gems(self.bet),       inline=True)
        embed.add_field(name="Minas",           value=f"💥 {self.num_mines}",   inline=True)
        embed.add_field(name="Gemas",           value=f"💎 {safe}",             inline=True)
        embed.add_field(name="Multiplicador",   value=f"x{mult:.2f}",           inline=True)
        embed.add_field(name="Cobrarías",       value=fmt_gems(pot),            inline=True)

        if result_text:
            embed.add_field(name="Resultado", value=result_text, inline=False)

        if not self.game_over and not result_text:
            # Texto pequeño en el footer indicando cómo cobrar
            embed.set_footer(text="Pulsa una 💎 ya revelada para cobrar • ⬜ para destapar")

        return embed


class MinesView(discord.ui.View):
    """
    Vista con el grid completo de 5x5 botones.
    - ⬜ = casilla oculta, clickeable para destapar
    - 💎 = gema encontrada, clickeable para COBRAR
    - 💥 = mina (solo aparece al perder)
    Discord permite máximo 25 botones en 5 filas de 5.
    """

    def __init__(self, game: MinesGame):
        super().__init__(timeout=600)
        self.game = game
        self._build_grid()

    def _build_grid(self):
        """Construye dinámicamente los 25 botones del grid."""
        for i in range(GRID_SIZE):
            row_num = i // 5            # Fila del botón: 0, 1, 2, 3, 4

            if not self.game.revealed[i]:
                # Casilla oculta — botón gris, se puede pulsar
                btn = discord.ui.Button(
                    label="⬜",
                    style=discord.ButtonStyle.secondary,
                    row=row_num,
                    custom_id=f"reveal_{i}",
                    disabled=self.game.game_over
                )
                btn.callback = self._make_reveal_callback(i)

            elif self.game.is_mine[i]:
                # Mina revelada — botón rojo desactivado
                btn = discord.ui.Button(
                    label="💥",
                    style=discord.ButtonStyle.danger,
                    row=row_num,
                    custom_id=f"mine_{i}",
                    disabled=True
                )

            else:
                # Gema revelada — botón verde, pulsar = COBRAR
                btn = discord.ui.Button(
                    label="💎",
                    style=discord.ButtonStyle.success,
                    row=row_num,
                    custom_id=f"cashout_{i}",
                    disabled=self.game.game_over
                )
                btn.callback = self._make_cashout_callback()

            self.add_item(btn)

    def _make_reveal_callback(self, index: int):
        """Genera el callback para destapar la casilla en la posición index."""
        async def callback(interaction: discord.Interaction):
            # Solo el jugador puede interactuar con sus botones
            if interaction.user.id != self.game.player_id:
                await interaction.response.send_message(
                    "Esta no es tu partida.", ephemeral=True
                )
                return
            await self.game.reveal(interaction, index)
        return callback

    def _make_cashout_callback(self):
        """Genera el callback para cobrar al pulsar una 💎 revelada."""
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.game.player_id:
                await interaction.response.send_message(
                    "Esta no es tu partida.", ephemeral=True
                )
                return
            await self.game.cashout(interaction)
        return callback

    async def on_timeout(self):
        """Tiempo agotado: limpia la partida."""
        self.game.cog.active_games.pop(self.game.player_id, None)
        self.game.game_over = True
        for item in self.children:
            item.disabled = True
        try:
            await self.game.message.edit(view=self)
        except Exception:
            pass


# ── Añade los métodos de lógica a MinesGame ───────────────────

async def _reveal(self, interaction: discord.Interaction, index: int):
    """Destapa la casilla en la posición index."""
    if self.game_over or self.revealed[index]:
        await interaction.response.defer()
        return

    self.revealed[index] = True         # Marca como destapada

    if self.is_mine[index]:
        # ── MINA — pierde ─────────────────────────────────────
        self.game_over = True
        self.cog.active_games.pop(self.player_id, None)

        # Acumula rakeback
        rakeback_pct = float(await self.bot.db.get_config("rakeback_pct") or "20")
        rakeback_amt = int(self.bet * rakeback_pct / 100)
        if rakeback_amt > 0:
            await self.bot.db.add_rakeback(str(self.player_id), rakeback_amt)

        await self.bot.db.log_game(
            str(self.player_id), "mines", self.bet, "lose", -self.bet
        )
        member = interaction.guild.get_member(self.player_id)
        if member:
            await update_wager_roles(self.bot, interaction.guild, member)

        result_text = f"💥 ¡Mina! Perdiste {fmt_gems(self.bet)}"
        embed = self.build_embed(result_text)
        view  = MinesView(self)         # Reconstruye con todo revelado
        await interaction.response.edit_message(embed=embed, view=view)

    else:
        # ── GEMA — continúa ───────────────────────────────────
        safe = self.safe_count()

        if safe == self.safe_total:
            # Destapó todas las seguras — cobra automáticamente
            await _cashout(self, interaction)
        else:
            # Actualiza el embed y el grid
            embed = self.build_embed()
            view  = MinesView(self)
            await interaction.response.edit_message(embed=embed, view=view)


async def _cashout(self, interaction: discord.Interaction):
    """Cobra las ganancias acumuladas al pulsar una 💎."""
    if self.game_over:
        await interaction.response.defer()
        return

    self.game_over = True
    self.cog.active_games.pop(self.player_id, None)

    safe   = self.safe_count()
    mult   = self.calc_multiplier(safe)
    payout = int(round(self.bet * mult, 0))
    profit = payout - self.bet

    await self.bot.db.add_balance(str(self.player_id), payout)

    result = "win" if profit > 0 else "tie"
    await self.bot.db.log_game(
        str(self.player_id), "mines", self.bet, result, profit
    )

    member = interaction.guild.get_member(self.player_id)
    if member:
        await update_wager_roles(self.bot, interaction.guild, member)

    new_bal = await self.bot.db.get_balance(str(self.player_id))

    result_text = f"💰 ¡Cobrado! {fmt_gems(payout)} (x{mult:.2f})"
    embed = self.build_embed(result_text)
    embed.set_footer(text=f"Saldo actual: {fmt_gems(new_bal)}")
    view  = MinesView(self)             # Reconstruye con todo desactivado
    await interaction.response.edit_message(embed=embed, view=view)


# Adjunta los métodos a la clase
MinesGame.reveal  = _reveal
MinesGame.cashout = _cashout


# ── COG ───────────────────────────────────────────────────────
class Mines(commands.Cog):

    def __init__(self, bot):
        self.bot          = bot
        self.active_games = {}          # {player_id: MinesGame}

    @app_commands.command(name="mines", description="Destapa gemas sin pisar una mina")
    @app_commands.describe(
        apuesta="Cantidad de gemas a apostar",
        minas="Número de minas (1-24)"
    )
    async def mines(self, interaction: discord.Interaction, apuesta: str, minas: int):
        if not await check_linked(interaction):
            return

        apuesta = parse_amount(str(apuesta))
        if not apuesta or apuesta <= 0:
            await interaction.response.send_message(
                embed=error_embed("La apuesta debe ser mayor a 0."), ephemeral=True
            )
            return

        # Máximo 20 minas para que siempre haya al menos 5 casillas seguras
        if not (1 <= minas <= 20):
            await interaction.response.send_message(
                embed=error_embed("El número de minas debe estar entre 1 y 20."), ephemeral=True
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
        await self.bot.db.remove_balance(user_id, apuesta)
        await self.bot.db.add_wager(user_id, apuesta)
        await self.bot.db.reduce_wager_requirement(user_id, apuesta)  # reduce wager req

        edge = await self.bot.db.get_house_edge("mines")
        game = MinesGame(self.bot, self, interaction.user, apuesta, minas, edge)
        self.active_games[interaction.user.id] = game

        embed = game.build_embed()
        view  = MinesView(game)

        await interaction.response.send_message(embed=embed, view=view)
        game.message = await interaction.original_response()


async def setup(bot):
    await bot.add_cog(Mines(bot))
