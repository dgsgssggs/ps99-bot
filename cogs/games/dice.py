# ============================================================
# cogs/games/dice.py — Juego de Dados
# ============================================================
# El jugador elige un número (1-6) o un rango.
# Se lanza el dado y si acierta, gana multiplicado.
# El house edge ajusta el payout para favorecer a la casa.
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
    COLOR_GOLD, COLOR_ERROR, COLOR_PURPLE
)

class Dice(commands.Cog):
    """Módulo del juego de Dados."""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="dice", description="Lanza el dado y apuesta por un número")
    @app_commands.describe(
        apuesta="Cantidad de gemas a apostar",
        numero="Número exacto del 1 al 6 (deja vacío para apostar alto/bajo)",
        alto_bajo="Elige 'alto' (4-6) o 'bajo' (1-3)"
    )
    async def dice(
        self,
        interaction: discord.Interaction,
        apuesta: int,
        numero: int = None,
        alto_bajo: str = None
    ):
        """
        Juego de dados:
        - Número exacto (1-6): paga 5x (menos house edge)
        - Alto (4,5,6) o Bajo (1,2,3): paga ~2x
        """
        if not await check_linked(interaction):
            return

        if apuesta <= 0:
            await interaction.response.send_message(
                embed=error_embed("La apuesta debe ser mayor a 0."), ephemeral=True
            )
            return

        # Valida que se eligió una opción
        if numero is None and alto_bajo is None:
            await interaction.response.send_message(
                embed=error_embed(
                    "Debes elegir un número (1-6) o alto/bajo.\n"
                    "Ejemplo: `/dice apuesta:1000 numero:4`"
                ),
                ephemeral=True
            )
            return

        # Valida el número exacto si se eligió
        if numero is not None and not (1 <= numero <= 6):
            await interaction.response.send_message(
                embed=error_embed("El número debe estar entre 1 y 6."), ephemeral=True
            )
            return

        # Valida alto/bajo si se eligió
        if alto_bajo is not None and alto_bajo.lower() not in ["alto", "bajo", "high", "low"]:
            await interaction.response.send_message(
                embed=error_embed("Elige 'alto' o 'bajo'."), ephemeral=True
            )
            return

        if not await check_balance(interaction, apuesta):
            return

        user_id = str(interaction.user.id)

        # Descuenta la apuesta
        await self.bot.db.remove_balance(user_id, apuesta)
        # Actualiza el wager total
        await self.bot.db.add_wager(user_id, apuesta)

        # Obtiene el house edge del dado
        edge = await self.bot.db.get_house_edge("dice")
        multiplier_factor = 1 - (edge / 100)    # Factor que reduce el pago

        # ── Lanza el dado ────────────────────────────────────
        result = _rng.randint(1, 6)              # Cryptographically secure

        # Emojis de dado para visualización
        dice_emojis = {1: "⚀", 2: "⚁", 3: "⚂", 4: "⚃", 5: "⚄", 6: "⚅"}
        dice_emoji  = dice_emojis[result]

        won    = False                           # ¿Ganó?
        payout = 0                               # Cuánto gana

        if numero is not None:
            # ── Número exacto ─────────────────────────────────
            # Paga 5x la apuesta (menos house edge) por acertar 1 de 6
            payout_multi = 5.0 * multiplier_factor
            won = (result == numero)
            choice_text = f"Número: **{numero}**"

        else:
            # ── Alto o Bajo ───────────────────────────────────
            # Paga ~2x la apuesta por acertar 1 de 2 opciones
            payout_multi = 1.9 * multiplier_factor  # Ligeramente bajo 2x por house edge
            ab = alto_bajo.lower()

            if ab in ["alto", "high"]:
                won = result >= 4                # 4, 5 o 6 = alto
                choice_text = "Alto (4-6)"
            else:
                won = result <= 3                # 1, 2 o 3 = bajo
                choice_text = "Bajo (1-3)"

        # ── Calcula el resultado ─────────────────────────────
        if won:
            payout = int(apuesta * payout_multi) # Ganancia calculada con el multiplicador
            await self.bot.db.add_balance(user_id, apuesta + payout)  # Devuelve apuesta + ganancia
            profit     = payout
            result_str = f"✅ ¡Ganaste {fmt_gems(payout)}!"
            color      = COLOR_GOLD
            db_result  = "win"
        else:
            profit     = -apuesta
            result_str = f"❌ Perdiste {fmt_gems(apuesta)}"
            color      = COLOR_ERROR
            db_result  = "lose"
            # Acumula rakeback al perder
            rakeback_pct = float(await self.bot.db.get_config("rakeback_pct") or "20")
            rakeback_amt = int(apuesta * rakeback_pct / 100)
            if rakeback_amt > 0:
                await self.bot.db.add_rakeback(user_id, rakeback_amt)

        # Registra en logs
        await self.bot.db.log_game(user_id, "dice", apuesta, db_result, profit)

        # Actualiza roles de wager
        member = interaction.guild.get_member(interaction.user.id)
        if member:
            await update_wager_roles(self.bot, interaction.guild, member)

        # ── Construye el embed ───────────────────────────────
        embed = discord.Embed(
            title=f"🎲 Dados — {interaction.user.display_name}",
            color=color
        )
        embed.add_field(name="Tu Elección",    value=choice_text,         inline=True)
        embed.add_field(name="Resultado",      value=f"{dice_emoji} **{result}**", inline=True)
        embed.add_field(name="Apuesta",        value=fmt_gems(apuesta),   inline=True)
        embed.add_field(name="Resultado",      value=result_str,          inline=False)

        # Muestra el saldo actualizado
        new_bal = await self.bot.db.get_balance(user_id)
        embed.set_footer(text=f"Saldo actual: {fmt_gems(new_bal)}")

        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(Dice(bot))
