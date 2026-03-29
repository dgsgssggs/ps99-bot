# ============================================================
# cogs/games/dice.py — Dados estilo casino (1-100)
# ============================================================
# Roll: número aleatorio del 1 al 100
# Umbral: 1-99. Ganas si roll <= umbral.
#   umbral=50 → 50/50 exacto
#   umbral=25 → 25% de ganar, mayor multiplicador
#   umbral=75 → 75% de ganar, menor multiplicador
# House edge: 5% por defecto (ajustable con /sethouseedge)
# Botón 🎲 Play Again: repite en el mismo mensaje sin abrir uno nuevo.
# ============================================================

import discord
from discord.ext import commands
from discord import app_commands
import random
_rng = random.SystemRandom()
from utils import (
    parse_amount, check_linked, check_balance,
    fmt_gems, error_embed, update_wager_roles,
    COLOR_GOLD, COLOR_ERROR, COLOR_INFO
)


def calc_multiplier(umbral: int, edge: float) -> float:
    """
    Multiplicador TOTAL del payout (incluye devolución de apuesta).
    payout = bet * multiplier
    Ej: umbral=50, edge=5% → multiplier = (100/50)*0.95 = 1.90
    """
    return round((100.0 / umbral) * (1 - edge / 100), 4)


class DiceView(discord.ui.View):
    def __init__(self, cog, user_id: int, apuesta: int, umbral: int):
        super().__init__(timeout=120)
        self.cog     = cog
        self.user_id = user_id
        self.apuesta = apuesta
        self.umbral  = umbral

    @discord.ui.button(label="🎲 Play Again", style=discord.ButtonStyle.primary)
    async def play_again(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Esta no es tu partida.", ephemeral=True)
            return

        bal = await self.cog.bot.db.get_balance(str(self.user_id))
        if bal < self.apuesta:
            button.disabled = True
            await interaction.response.edit_message(
                embed=discord.Embed(
                    description=f"❌ Saldo insuficiente para repetir. Tienes {fmt_gems(bal)} y necesitas {fmt_gems(self.apuesta)}.",
                    color=COLOR_ERROR
                ),
                view=self
            )
            return

        embed, view = await self.cog.roll(interaction.user, self.apuesta, self.umbral, interaction.guild)
        await interaction.response.edit_message(embed=embed, view=view)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class Dice(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def roll(self, user, apuesta: int, umbral: int, guild) -> tuple:
        """Ejecuta una tirada y devuelve (embed, view)."""
        user_id = str(user.id)
        await self.bot.db.remove_balance(user_id, apuesta)
        await self.bot.db.add_wager(user_id, apuesta)
        await self.bot.db.reduce_wager_requirement(user_id, apuesta)

        edge   = await self.bot.db.get_house_edge("dice")
        mult   = calc_multiplier(umbral, edge)
        result = _rng.randint(1, 100)
        won    = result <= umbral

        if won:
            payout = int(round(apuesta * mult, 0))
            profit = payout - apuesta
            await self.bot.db.add_balance(user_id, payout)
            color, res_text, db_res = COLOR_GOLD, f"✅ ¡Ganaste {fmt_gems(profit)}!", "win"
        else:
            profit  = -apuesta
            payout  = 0
            # Rakeback
            house_p = int(apuesta * edge / 100)
            rb_pct  = float(await self.bot.db.get_config("rakeback_pct") or "20")
            rb_amt  = int(house_p * rb_pct / 100)
            if rb_amt > 0:
                await self.bot.db.add_rakeback(user_id, rb_amt)
            color, res_text, db_res = COLOR_ERROR, f"❌ Perdiste {fmt_gems(apuesta)}", "lose"

        await self.bot.db.log_game(user_id, "dice", apuesta, db_res, profit)
        member = guild.get_member(user.id)
        if member:
            await update_wager_roles(self.bot, guild, member)

        new_bal = await self.bot.db.get_balance(user_id)

        # Barra visual — posición del roll y límite del umbral
        bar_len = 20
        roll_pos  = max(0, min(bar_len - 1, int((result - 1) / 99 * bar_len)))
        umbral_pos = max(0, min(bar_len - 1, int((umbral - 1) / 99 * bar_len)))
        bar = ""
        for i in range(bar_len):
            if i == roll_pos:
                bar += "🔴" if result > umbral else "🟢"
            elif i <= umbral_pos:
                bar += "🟩"
            else:
                bar += "⬛"

        win_chance = umbral  # P(win) = umbral/100 exacto
        embed = discord.Embed(title=f"🎲 Dice — {user.display_name}", color=color)
        embed.add_field(name="Umbral",        value=f"≤ **{umbral}** ({win_chance}% de ganar)",  inline=True)
        embed.add_field(name="Roll",          value=f"**{result}** / 100",                         inline=True)
        embed.add_field(name="Pago si gana",  value=f"x{mult:.4f} → {fmt_gems(int(round(apuesta*mult,0)))} ",    inline=True)
        embed.add_field(name="Apuesta",       value=fmt_gems(apuesta),                             inline=True)
        embed.add_field(name="Resultado",     value=res_text,                                       inline=True)
        embed.add_field(name="\u200b",        value=bar,                                            inline=False)
        embed.set_footer(text=f"Saldo: {fmt_gems(new_bal)} · House edge: {edge}%")

        return embed, DiceView(self, user.id, apuesta, umbral)

    @app_commands.command(name="dice", description="Lanza un dado 1-100 y elige tu umbral de victoria")
    @app_commands.describe(
        apuesta="Gemas a apostar (ej: 500k, 1m)",
        umbral="Ganas si el roll cae ≤ umbral (1-99). Ej: 50 = exacto 50/50"
    )
    async def dice(self, interaction: discord.Interaction, apuesta: str, umbral: int):
        if not await check_linked(interaction):
            return

        amount = parse_amount(str(apuesta))
        if not amount or amount <= 0:
            await interaction.response.send_message(
                embed=error_embed("Apuesta inválida. Usa K/M/B (ej: 500k, 1m)"), ephemeral=True
            )
            return

        if not (1 <= umbral <= 99):
            await interaction.response.send_message(
                embed=error_embed(
                    "El umbral debe ser entre 1 y 99.\n"
                    "• umbral=50 → 50% de ganar (50/50)\n"
                    "• umbral=25 → 25% de ganar (mayor multiplicador)\n"
                    "• umbral=75 → 75% de ganar (menor multiplicador)"
                ),
                ephemeral=True
            )
            return

        if not await check_balance(interaction, amount):
            return

        embed, view = await self.roll(interaction.user, amount, umbral, interaction.guild)
        await interaction.response.send_message(embed=embed, view=view)


async def setup(bot):
    await bot.add_cog(Dice(bot))
