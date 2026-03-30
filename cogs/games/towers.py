# ============================================================
# cogs/games/towers.py — Torres (estilo Mines con grid de botones)
# ============================================================
# Visual: 4 filas de botones visibles a la vez + fila de cashout.
#
# Ventana deslizante:
#   Row 0 (top)  → piso actual+2  (futuro,   🔒)
#   Row 1        → piso actual+1  (futuro,   🔒)
#   Row 2        → piso actual    (ACTIVO,   ⬜)
#   Row 3        → piso actual-1  (pasado,   ✅/⬛) o suelo (🟩)
#   Row 4        → 💰 Cobrar
#
# Cuando el jugador sube un piso, la ventana se desplaza:
#   el piso completado queda en row 3, el nuevo piso activo en row 2.
#
# Pisos: 8  |  Dificultades: fácil 4 col, normal 3 col, difícil 2 col
# ============================================================

import discord
from discord.ext import commands
from discord import app_commands
import random
_rng = random.SystemRandom()
from utils import (
    parse_amount, check_linked, check_balance,
    fmt_gems, error_embed, update_wager_roles,
    COLOR_GOLD, COLOR_ERROR, COLOR_INFO, COLOR_PURPLE
)

FLOORS    = 8
DIFF_COLS = {"easy": 4, "normal": 3, "hard": 2}


def tower_multiplier(floors_cleared: int, cols: int, edge: float) -> float:
    """
    Multiplicador acumulado tras completar `floors_cleared` pisos.
    P(sobrevivir N pisos) = ((cols-1)/cols)^N
    Payout = (1/P) * (1-edge/100)
    """
    if floors_cleared == 0:
        return 1.0
    p = ((cols - 1) / cols) ** floors_cleared
    if p <= 0:
        return 1.0
    return round((1.0 / p) * (1 - edge / 100), 2)


class TowerGame:
    """
    Estado de la partida.
    self.floor  = número de pisos COMPLETADOS (0 = ninguno).
    Para elegir el siguiente piso, el jugador está en floor+1.
    """

    def __init__(self, player, bet: int, cols: int, edge: float):
        self.player    = player
        self.player_id = player.id
        self.bet       = bet
        self.cols      = cols
        self.edge      = edge
        self.floor     = 0          # pisos completados
        self.alive     = True
        self.cashed    = False
        self.message   = None
        self.cog       = None

        # Pre-genera mina para cada piso (índice 1 = piso 1, ..., 8 = piso 8)
        self.mine = {f: _rng.randint(0, cols - 1) for f in range(1, FLOORS + 1)}
        # Historial de columnas elegidas: {piso: col_elegida}
        self.choices: dict[int, int] = {}

    def current_mult(self) -> float:
        return tower_multiplier(self.floor, self.cols, self.edge)

    def next_mult(self) -> float:
        return tower_multiplier(self.floor + 1, self.cols, self.edge)

    def build_embed(self, result_text: str = None) -> discord.Embed:
        pot = int(round(self.bet * self.current_mult(), 0))

        if result_text and ("💥" in result_text or "❌" in result_text):
            color = COLOR_ERROR
        elif result_text and ("✅" in result_text or "💰" in result_text):
            color = COLOR_GOLD
        else:
            color = COLOR_PURPLE

        active_floor = self.floor + 1
        diff_names   = {"easy": "Fácil", "normal": "Normal", "hard": "Difícil"}

        embed = discord.Embed(title=f"🏰 Torres — {self.player.display_name}", color=color)
        embed.add_field(name="Apuesta",     value=fmt_gems(self.bet),                inline=True)
        embed.add_field(name="Piso",        value=f"**{self.floor}/{FLOORS}**",       inline=True)
        embed.add_field(name="Multiplicador", value=f"x{self.current_mult():.2f}",   inline=True)
        embed.add_field(name="💰 Si cobras", value=fmt_gems(pot),                    inline=True)

        if self.alive and not self.cashed and self.floor < FLOORS:
            nxt = int(round(self.bet * self.next_mult(), 0))
            embed.add_field(name="Siguiente piso", value=fmt_gems(nxt),              inline=True)

        if result_text:
            embed.add_field(name="Resultado", value=result_text, inline=False)

        return embed


class TowerView(discord.ui.View):
    """
    Grid deslizante de 4 filas:
      Row 0 → piso active+2 (locked)
      Row 1 → piso active+1 (locked)
      Row 2 → piso active   (ACTIVE — ⬜ clickeable)
      Row 3 → piso active-1 (cleared: ✅/⬛) o suelo (🟩)
      Row 4 → 💰 Cobrar
    """

    def __init__(self, game: TowerGame):
        super().__init__(timeout=300)
        self.game = game
        self._rebuild()

    def _rebuild(self):
        self.clear_items()
        game = self.game

        if not game.alive or game.cashed:
            return

        active = game.floor + 1   # piso a elegir ahora
        cols   = game.cols

        # Calcula qué piso va en cada fila (row 0 = arriba = más alto visualmente)
        floor_in_row = {
            0: active + 2,   # futuro  (2 arriba del activo)
            1: active + 1,   # futuro  (1 arriba del activo)
            2: active,       # ACTIVO
            3: active - 1,   # pasado o suelo (0 = suelo)
        }

        for row_idx, floor_num in floor_in_row.items():
            if floor_num > FLOORS:
                # Piso inexistente — fila vacía (no añade botones)
                continue

            if floor_num < 0:
                continue

            if floor_num == 0:
                # Suelo — tiles verdes desactivados como referencia visual
                for c in range(cols):
                    btn = discord.ui.Button(
                        label    = "🟩",
                        style    = discord.ButtonStyle.secondary,
                        disabled = True,
                        row      = row_idx,
                        custom_id= f"ground_{c}"
                    )
                    self.add_item(btn)

            elif floor_num < active:
                # Piso ya completado — muestra resultado
                chosen = game.choices.get(floor_num)
                for c in range(cols):
                    if c == chosen:
                        label = "✅"
                        style = discord.ButtonStyle.success
                    else:
                        label = "⬛"
                        style = discord.ButtonStyle.secondary
                    btn = discord.ui.Button(
                        label    = label,
                        style    = style,
                        disabled = True,
                        row      = row_idx,
                        custom_id= f"done_{floor_num}_{c}"
                    )
                    self.add_item(btn)

            elif floor_num == active:
                # Piso activo — botones clickeables
                for c in range(cols):
                    btn = discord.ui.Button(
                        label    = "⬜",
                        style    = discord.ButtonStyle.primary,
                        row      = row_idx,
                        custom_id= f"pick_{floor_num}_{c}"
                    )
                    btn.callback = self._make_pick(c)
                    self.add_item(btn)

            else:
                # Piso futuro — bloqueado
                for c in range(cols):
                    btn = discord.ui.Button(
                        label    = "🔒",
                        style    = discord.ButtonStyle.secondary,
                        disabled = True,
                        row      = row_idx,
                        custom_id= f"future_{floor_num}_{c}"
                    )
                    self.add_item(btn)

        # Cashout (row 4)
        pot = int(round(game.bet * game.current_mult(), 0))
        cashout_btn = discord.ui.Button(
            label    = f"💰 Cobrar  {fmt_gems(pot)}  ·  x{game.current_mult():.2f}",
            style    = discord.ButtonStyle.success,
            row      = 4,
            custom_id= "tower_cashout"
        )
        cashout_btn.callback = self._cashout_cb
        self.add_item(cashout_btn)

    def _make_pick(self, col: int):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.game.player_id:
                await interaction.response.send_message("Esta no es tu partida.", ephemeral=True)
                return
            await self._pick(interaction, col)
        return callback

    async def _pick(self, interaction: discord.Interaction, col: int):
        game         = self.game
        active_floor = game.floor + 1
        mine_col     = game.mine[active_floor]

        game.choices[active_floor] = col

        if col == mine_col:
            # 💥 Mina
            game.alive = False
            game.cog.active_games.pop(game.player_id, None)

            # Rakeback
            house_p = int(game.bet * game.edge / 100)
            rb_pct  = float(await game.cog.bot.db.get_config("rakeback_pct") or "20")
            rb_amt  = int(house_p * rb_pct / 100)
            if rb_amt > 0:
                await game.cog.bot.db.add_rakeback(str(game.player_id), rb_amt)

            await game.cog.bot.db.log_game(str(game.player_id), "towers", game.bet, "lose", -game.bet)
            member = interaction.guild.get_member(game.player_id)
            if member:
                await update_wager_roles(game.cog.bot, interaction.guild, member)

            # Revela la mina en el botón clickeado → muestra 💥 en embed
            result_text = f"💥 **¡Mina en piso {active_floor}!** Perdiste {fmt_gems(game.bet)}"
            # Marca el botón del piso activo para mostrar ✅/💥
            game.choices[active_floor] = col  # ya guardado

            # Reconstruye con todo desactivado + muestra resultado
            self._show_explosion(active_floor, col, mine_col)
            embed = game.build_embed(result_text)
            await interaction.response.edit_message(embed=embed, view=self)

        else:
            # ✅ Seguro — sube un piso
            game.floor += 1

            if game.floor >= FLOORS:
                # Completó todos los pisos — cobra automáticamente
                await self._do_cashout(interaction)
                return

            self._rebuild()
            result_text = f"✅ Piso {active_floor} superado · Multiplicador: x{game.current_mult():.2f}"
            embed = game.build_embed(result_text)
            await interaction.response.edit_message(embed=embed, view=self)

    def _show_explosion(self, exploded_floor: int, chosen: int, mine: int):
        """Reconstruye la vista mostrando la explosión y desactivando todo."""
        self.clear_items()
        game   = self.game
        active = exploded_floor
        cols   = game.cols

        floor_in_row = {
            0: active + 2,
            1: active + 1,
            2: active,       # este es el piso donde explotó
            3: active - 1,
        }

        for row_idx, floor_num in floor_in_row.items():
            if floor_num > FLOORS or floor_num < 0:
                continue

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
                # Muestra la explosión
                for c in range(cols):
                    if c == mine:
                        label = "💥"
                        style = discord.ButtonStyle.danger
                    elif c == chosen and chosen != mine:
                        label = "✅"
                        style = discord.ButtonStyle.success
                    else:
                        label = "⬛"
                        style = discord.ButtonStyle.secondary
                    self.add_item(discord.ui.Button(label=label, style=style, disabled=True,
                                                     row=row_idx, custom_id=f"exp_{c}"))
            else:
                for c in range(cols):
                    self.add_item(discord.ui.Button(label="🔒", style=discord.ButtonStyle.secondary,
                                                     disabled=True, row=row_idx, custom_id=f"fut_{floor_num}_{c}"))

    async def _cashout_cb(self, interaction: discord.Interaction):
        if interaction.user.id != self.game.player_id:
            await interaction.response.send_message("Esta no es tu partida.", ephemeral=True)
            return
        await self._do_cashout(interaction)

    async def _do_cashout(self, interaction: discord.Interaction):
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

        new_bal = await game.cog.bot.db.get_balance(str(game.player_id))
        result_text = f"💰 **Cobrado x{mult:.2f}** — {fmt_gems(payout)} (+{fmt_gems(profit)})"

        self._rebuild()
        # Desactiva todos los botones excepto los ya desactivados
        for item in self.children:
            item.disabled = True

        embed = game.build_embed(result_text)
        embed.set_footer(text=f"Saldo: {fmt_gems(new_bal)}")
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        game = self.game
        game.cog.active_games.pop(game.player_id, None)

        # Auto-cobrar si hay multiplicador ganado, devolver si no
        if game.floor > 0 and game.alive:
            payout = int(round(game.bet * game.current_mult(), 0))
            await game.cog.bot.db.add_balance(str(game.player_id), payout)
        elif game.floor == 0:
            await game.cog.bot.db.add_balance(str(game.player_id), game.bet)

        for item in self.children:
            item.disabled = True
        try:
            embed = game.build_embed("⏰ Tiempo agotado — cobrado automáticamente")
            if game.message:
                await game.message.edit(embed=embed, view=self)
        except Exception:
            pass


class Towers(commands.Cog):
    def __init__(self, bot):
        self.bot          = bot
        self.active_games = {}

    @app_commands.command(name="towers", description="Sube la torre evitando las minas en cada piso")
    @app_commands.describe(
        apuesta="Gemas a apostar",
        dificultad="Columnas por piso (más columnas = más fácil)"
    )
    @app_commands.choices(dificultad=[
        app_commands.Choice(name="Fácil   — 4 columnas (75% por piso)", value="easy"),
        app_commands.Choice(name="Normal  — 3 columnas (67% por piso)", value="normal"),
        app_commands.Choice(name="Difícil — 2 columnas (50% por piso)", value="hard"),
    ])
    async def towers(self, interaction: discord.Interaction, apuesta: str, dificultad: str = "normal"):
        if not await check_linked(interaction):
            return

        amount = parse_amount(str(apuesta))
        if not amount or amount <= 0:
            await interaction.response.send_message(embed=error_embed("Apuesta inválida."), ephemeral=True)
            return

        if interaction.user.id in self.active_games:
            await interaction.response.send_message(
                embed=error_embed("Ya tienes una partida de Torres activa."), ephemeral=True
            )
            return

        if not await check_balance(interaction, amount):
            return

        user_id = str(interaction.user.id)
        await self.bot.db.remove_balance(user_id, amount)
        await self.bot.db.add_wager(user_id, amount)
        await self.bot.db.reduce_wager_requirement(user_id, amount)

        edge = await self.bot.db.get_house_edge("towers")
        cols = DIFF_COLS.get(dificultad, 3)

        game     = TowerGame(interaction.user, amount, cols, edge)
        game.cog = self
        self.active_games[interaction.user.id] = game

        embed = game.build_embed()
        view  = TowerView(game)
        await interaction.response.send_message(embed=embed, view=view)
        game.message = await interaction.original_response()


async def setup(bot):
    await bot.add_cog(Towers(bot))
