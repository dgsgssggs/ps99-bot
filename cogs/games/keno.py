# ============================================================
# cogs/games/keno.py — Juego de Keno
# ============================================================
# El jugador elige entre 1 y 10 números del 1 al 40.
# El bot sortea 20 números aleatorios.
# El pago depende de cuántos números acertó.
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

# ── Tabla de pagos de Keno ────────────────────────────────────
# key: (números elegidos, números acertados) → multiplicador
# Esto define cuánto paga cada combinación posible.
KENO_PAYOUTS = {
    # Si eliges 1 número
    (1, 1): 3.0,                            # Acertaste el único número
    # Si eliges 2 números
    (2, 1): 1.0, (2, 2): 5.0,
    # Si eliges 3 números
    (3, 2): 2.0, (3, 3): 10.0,
    # Si eliges 4 números
    (4, 2): 1.0, (4, 3): 4.0, (4, 4): 20.0,
    # Si eliges 5 números
    (5, 3): 2.0, (5, 4): 8.0, (5, 5): 50.0,
    # Si eliges 6 números
    (6, 3): 1.0, (6, 4): 4.0, (6, 5): 15.0, (6, 6): 100.0,
    # Si eliges 7 números
    (7, 4): 2.0, (7, 5): 7.0, (7, 6): 30.0, (7, 7): 200.0,
    # Si eliges 8 números
    (8, 5): 3.0, (8, 6): 12.0, (8, 7): 50.0, (8, 8): 500.0,
    # Si eliges 9 números
    (9, 5): 2.0, (9, 6): 8.0, (9, 7): 25.0, (9, 8): 200.0, (9, 9): 1000.0,
    # Si eliges 10 números
    (10, 5): 1.0, (10, 6): 5.0, (10, 7): 15.0, (10, 8): 80.0, (10, 9): 500.0, (10, 10): 2000.0,
}

class Keno(commands.Cog):
    """Módulo del juego Keno."""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="keno", description="Juega al Keno — elige números y gana según aciertos")
    @app_commands.describe(
        apuesta="Cantidad de gemas a apostar",
        numeros="Tus números del 1 al 40, separados por espacio. Ej: 3 7 15 22 31"
    )
    async def keno(self, interaction: discord.Interaction, apuesta: int, numeros: str):
        """
        Juego de Keno:
        - El jugador elige entre 1 y 10 números del 1 al 40.
        - El bot sortea 20 números.
        - El pago depende de cuántos acertó.
        """
        if not await check_linked(interaction):
            return

        if apuesta <= 0:
            await interaction.response.send_message(
                embed=error_embed("La apuesta debe ser mayor a 0."), ephemeral=True
            )
            return

        # ── Parsea los números elegidos por el jugador ───────
        try:
            chosen = list(set(int(n) for n in numeros.split()))  # Sin duplicados
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed(
                    "Formato inválido. Escribe los números separados por espacio.\n"
                    "Ejemplo: `/keno apuesta:1000 numeros:3 7 15 22 31`"
                ),
                ephemeral=True
            )
            return

        # Valida que todos los números estén entre 1 y 40
        if not all(1 <= n <= 40 for n in chosen):
            await interaction.response.send_message(
                embed=error_embed("Todos los números deben estar entre 1 y 40."), ephemeral=True
            )
            return

        # Valida la cantidad de números elegidos
        if not (1 <= len(chosen) <= 10):
            await interaction.response.send_message(
                embed=error_embed("Debes elegir entre 1 y 10 números."), ephemeral=True
            )
            return

        if not await check_balance(interaction, apuesta):
            return

        user_id = str(interaction.user.id)

        # Descuenta la apuesta
        await self.bot.db.remove_balance(user_id, apuesta)
        await self.bot.db.add_wager(user_id, apuesta)

        # ── Sorteo del Keno ──────────────────────────────────
        # El sistema sortea 20 números aleatorios del 1 al 40
        drawn = sorted(_rng.sample(range(1, 41), 20))

        # Cuenta los aciertos
        hits = [n for n in chosen if n in drawn]    # Números acertados
        num_hits = len(hits)                         # Cantidad acertada

        # ── Calcula el payout ────────────────────────────────
        key = (len(chosen), num_hits)               # Busca la combinación en la tabla
        edge = await self.bot.db.get_house_edge("keno")

        if key in KENO_PAYOUTS:
            base_multiplier = KENO_PAYOUTS[key]     # Multiplicador base
            multiplier = base_multiplier * (1 - edge / 100)  # Ajustado por house edge
            payout     = int(apuesta * multiplier)  # Ganancias
            profit     = payout - apuesta
            won        = True
        else:
            # No hay pago para esta combinación (pocos aciertos)
            multiplier = 0.0
            payout     = 0
            profit     = -apuesta
            won        = False

        # Aplica el resultado al balance
        if payout > 0:
            await self.bot.db.add_balance(user_id, payout)

        # Rakeback: % del beneficio de la casa (edge%), no del total apostado
        if profit < 0:
            edge_pct_kn  = await self.bot.db.get_house_edge("keno")
            house_profit = int(apuesta * edge_pct_kn / 100)
            rakeback_pct = float(await self.bot.db.get_config("rakeback_pct") or "20")
            rakeback_amt = int(house_profit * rakeback_pct / 100)
            if rakeback_amt > 0:
                await self.bot.db.add_rakeback(user_id, rakeback_amt)

        # Registra en logs
        db_result = "win" if profit > 0 else ("tie" if profit == 0 else "lose")
        await self.bot.db.log_game(user_id, "keno", apuesta, db_result, profit)

        # Actualiza roles de wager
        member = interaction.guild.get_member(interaction.user.id)
        if member:
            await update_wager_roles(self.bot, interaction.guild, member)

        # ── Construye el embed ───────────────────────────────
        color = COLOR_GOLD if won and payout > 0 else COLOR_ERROR

        embed = discord.Embed(
            title=f"🎰 Keno — {interaction.user.display_name}",
            color=color
        )

        # Formatea los números elegidos (resalta aciertos)
        chosen_display = " ".join(
            f"**[{n}]**" if n in drawn else str(n)    # Negrita si acertó
            for n in sorted(chosen)
        )
        embed.add_field(name=f"Tus Números ({len(chosen)})", value=chosen_display, inline=False)

        # Formatea los 20 números sorteados (resalta aciertos)
        drawn_display = " ".join(
            f"**{n}**" if n in chosen else str(n)      # Negrita si coincide con el jugador
            for n in drawn
        )
        embed.add_field(name="Números Sorteados (20)", value=drawn_display, inline=False)

        # Resultado
        embed.add_field(name="Aciertos",      value=f"✅ {num_hits} / {len(chosen)}",  inline=True)
        embed.add_field(name="Multiplicador", value=f"x{multiplier:.2f}",               inline=True)
        embed.add_field(name="Apuesta",       value=fmt_gems(apuesta),                  inline=True)

        if payout > 0:
            embed.add_field(name="Ganancia", value=f"✅ +{fmt_gems(payout)}", inline=False)
        else:
            embed.add_field(name="Resultado", value=f"❌ Perdiste {fmt_gems(apuesta)}", inline=False)

        # Saldo actualizado
        new_bal = await self.bot.db.get_balance(user_id)
        embed.set_footer(text=f"Saldo actual: {fmt_gems(new_bal)}")

        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(Keno(bot))
