# cogs/games/towers.py — Juego de Torres
# ============================================================
# Una torre de 8 pisos. Cada piso tiene N columnas con 1 mina.
# El jugador elige columna en cada piso. Si acierta sube.
# Si pisa la mina pierde todo. Puede cobrar en cualquier piso.
# Dificultades: Fácil (4 col, 1 mina), Normal (3 col, 1 mina),
#               Difícil (2 col, 1 mina), Extremo (2 col, 2 minas no → 1 safe)
# House edge aplicado al multiplicador.
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

FLOORS     = 8      # Pisos totales
DIFF_COLS  = {"easy": 4, "normal": 3, "hard": 2}   # columnas por piso
DIFF_NAMES = {"easy": "Fácil (1/4)", "normal": "Normal (1/3)", "hard": "Difícil (1/2)"}

def tower_multiplier(floor: int, cols: int, edge: float) -> float:
    """
    Multiplicador acumulado al llegar al piso `floor`.
    Prob de sobrevivir floor pisos = ((cols-1)/cols)^floor
    Fair payout = 1/prob. Adjusted = fair * (1-edge/100)
    """
    if floor == 0:
        return 1.0
    survive_prob = ((cols - 1) / cols) ** floor
    if survive_prob <= 0:
        return 1.0
    fair = 1.0 / survive_prob
    return round(fair * (1 - edge / 100), 2)


class TowerGame:
    def __init__(self, player, bet: int, cols: int, edge: float):
        self.player    = player
        self.player_id = player.id
        self.bet       = bet
        self.cols      = cols
        self.edge      = edge
        self.floor     = 0          # Piso actual (0 = base)
        self.mine_positions = []    # Posición de la mina en cada piso (0-indexed)
        self.revealed  = {}         # {floor: col_elegida}
        self.alive     = True
        self.cashed    = False
        self.message   = None
        self.cog       = None

        # Pre-genera minas para todos los pisos
        for _ in range(FLOORS):
            self.mine_positions.append(_rng.randint(0, cols - 1))

    def current_mult(self) -> float:
        return tower_multiplier(self.floor, self.cols, self.edge)

    def next_mult(self) -> float:
        return tower_multiplier(self.floor + 1, self.cols, self.edge)

    def build_embed(self, result_text: str = None) -> discord.Embed:
        pot     = int(round(self.bet * self.current_mult(), 0))
        nxt_pot = int(round(self.bet * self.next_mult(), 0))

        color = COLOR_PURPLE
        if result_text:
            color = COLOR_GOLD if "cobrado" in result_text.lower() or "✅" in result_text else COLOR_ERROR

        embed = discord.Embed(title=f"🏰 Torres — {self.player.display_name}", color=color)
        embed.add_field(name="Apuesta",       value=fmt_gems(self.bet),                    inline=True)
        embed.add_field(name="Piso",          value=f"**{self.floor}/{FLOORS}**",           inline=True)
        embed.add_field(name="Mult actual",   value=f"x{self.current_mult():.2f}",          inline=True)
        embed.add_field(name="Si cobras",     value=fmt_gems(pot),                          inline=True)
        if self.alive and self.floor < FLOORS:
            embed.add_field(name="Siguiente piso", value=fmt_gems(nxt_pot),                inline=True)

        # Visual tower (muestra pisos completados)
        tower_str = ""
        for f in range(FLOORS, 0, -1):
            if f > self.floor:
                tower_str += f"Piso {f}: {'🟦 ' * self.cols}\n"
            elif f == self.floor and self.alive and not self.cashed:
                tower_str += f"Piso {f}: **← AQUÍ**\n"
            else:
                chosen = self.revealed.get(f)
                mine   = self.mine_positions[f - 1]
                row    = ""
                for c in range(self.cols):
                    if not self.alive and f == self.floor and c == mine:
                        row += "💥"
                    elif chosen == c:
                        row += "✅"
                    elif not self.alive and c == mine:
                        row += "💣"
                    else:
                        row += "⬛"
                tower_str += f"Piso {f}: {row}\n"

        embed.add_field(name="Torre", value=tower_str or "—", inline=False)

        if result_text:
            embed.add_field(name="Resultado", value=result_text, inline=False)
        return embed


class TowerView(discord.ui.View):
    def __init__(self, game: TowerGame):
        super().__init__(timeout=300)
        self.game = game
        self._build_buttons()

    def _build_buttons(self):
        self.clear_items()
        if not self.game.alive or self.game.cashed or self.game.floor >= FLOORS:
            return

        cols = self.game.cols
        labels = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
        for c in range(cols):
            btn = discord.ui.Button(
                label=f"Columna {c+1}",
                emoji=labels[c],
                style=discord.ButtonStyle.primary,
                custom_id=f"tower_col_{c}"
            )
            btn.callback = self._make_col_callback(c)
            self.add_item(btn)

        cashout_btn = discord.ui.Button(
            label=f"💰 Cobrar x{self.game.current_mult():.2f}",
            style=discord.ButtonStyle.success,
            custom_id="tower_cashout"
        )
        cashout_btn.callback = self._cashout_callback
        self.add_item(cashout_btn)

    def _make_col_callback(self, col: int):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.game.player_id:
                await interaction.response.send_message("Esta no es tu partida.", ephemeral=True)
                return
            await self._pick(interaction, col)
        return callback

    async def _pick(self, interaction: discord.Interaction, col: int):
        game = self.game
        mine = game.mine_positions[game.floor]  # piso actual (0-indexed)

        game.revealed[game.floor + 1] = col     # guarda para visualización
        game.floor += 1

        if col == mine:
            # Hit mine
            game.alive = False
            game.cog.active_games.pop(game.player_id, None)

            # Rakeback
            edge_pct = game.edge
            house_p  = int(game.bet * edge_pct / 100)
            rb_pct   = float(await game.cog.bot.db.get_config("rakeback_pct") or "20")
            rb_amt   = int(house_p * rb_pct / 100)
            if rb_amt > 0:
                await game.cog.bot.db.add_rakeback(str(game.player_id), rb_amt)

            await game.cog.bot.db.log_game(str(game.player_id), "towers", game.bet, "lose", -game.bet)
            member = interaction.guild.get_member(game.player_id)
            if member:
                await update_wager_roles(game.cog.bot, interaction.guild, member)

            for item in self.children:
                item.disabled = True
            embed = game.build_embed("💥 ¡Pisaste una mina! Perdiste todo.")
            await interaction.response.edit_message(embed=embed, view=self)

        elif game.floor >= FLOORS:
            # Completed all floors - auto cashout
            await self._do_cashout(interaction)

        else:
            # Safe - continue
            self._build_buttons()
            embed = game.build_embed(f"✅ ¡Seguro! Sube al piso {game.floor}")
            await interaction.response.edit_message(embed=embed, view=self)

    async def _cashout_callback(self, interaction: discord.Interaction):
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

        for item in self.children:
            item.disabled = True
        new_bal = await game.cog.bot.db.get_balance(str(game.player_id))
        embed   = game.build_embed(f"✅ Cobrado {fmt_gems(payout)} (x{mult:.2f})")
        embed.set_footer(text=f"Saldo: {fmt_gems(new_bal)}")
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        game = self.game
        game.cog.active_games.pop(game.player_id, None)
        # Return bet if they haven't climbed at all, otherwise auto-cashout
        if game.floor > 0 and game.alive:
            mult   = game.current_mult()
            payout = int(round(game.bet * mult, 0))
            await game.cog.bot.db.add_balance(str(game.player_id), payout)
        elif game.floor == 0:
            await game.cog.bot.db.add_balance(str(game.player_id), game.bet)
        for item in self.children:
            item.disabled = True
        try:
            embed = game.build_embed("⏰ Tiempo agotado — se cobró automáticamente")
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
        dificultad="Dificultad de la torre"
    )
    @app_commands.choices(dificultad=[
        app_commands.Choice(name="Fácil   — 4 columnas, 1 mina (75% por piso)",  value="easy"),
        app_commands.Choice(name="Normal  — 3 columnas, 1 mina (67% por piso)",  value="normal"),
        app_commands.Choice(name="Difícil — 2 columnas, 1 mina (50% por piso)",  value="hard"),
    ])
    async def towers(self, interaction: discord.Interaction, apuesta: str, dificultad: str = "normal"):
        if not await check_linked(interaction):
            return

        apuesta = parse_amount(str(apuesta))
        if not apuesta or apuesta <= 0:
            await interaction.response.send_message(embed=error_embed("Apuesta inválida."), ephemeral=True)
            return

        if interaction.user.id in self.active_games:
            await interaction.response.send_message(
                embed=error_embed("Ya tienes una partida de Torres activa."), ephemeral=True
            )
            return

        if not await check_balance(interaction, apuesta):
            return

        user_id = str(interaction.user.id)
        await self.bot.db.remove_balance(user_id, apuesta)
        await self.bot.db.add_wager(user_id, apuesta)
        await self.bot.db.reduce_wager_requirement(user_id, apuesta)

        edge = await self.bot.db.get_house_edge("towers")
        cols = DIFF_COLS.get(dificultad, 3)

        game      = TowerGame(interaction.user, apuesta, cols, edge)
        game.cog  = self
        self.active_games[interaction.user.id] = game

        embed = game.build_embed()
        view  = TowerView(game)
        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()
        game.message = msg


async def setup(bot):
    await bot.add_cog(Towers(bot))
