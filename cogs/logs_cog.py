# ============================================================
# cogs/logs_cog.py — Comandos de historial y logs
# ============================================================
# /logs <usuario>           → historial de juegos
# /logs <usuario> deposits  → historial de transacciones
# ============================================================

import discord
from discord.ext import commands
from discord import app_commands
from utils import fmt_gems, fmt, COLOR_INFO, COLOR_PURPLE, error_embed
from datetime import datetime

# Cantidad de entradas a mostrar por página
PAGE_SIZE = 10

class Logs(commands.Cog):
    """Módulo de logs — historial de juegos y transacciones."""

    def __init__(self, bot):
        self.bot = bot

    # ── /logs ─────────────────────────────────────────────────
    @app_commands.command(name="logs", description="Ver el historial de juegos o transacciones de un usuario")
    @app_commands.describe(
        usuario="El usuario a consultar (deja vacío para verte a ti)",
        tipo="'games' para juegos, 'deposits' para transacciones"
    )
    async def logs(
        self,
        interaction: discord.Interaction,
        usuario: discord.Member = None,
        tipo: str = "games"
    ):
        """Muestra el historial de juegos o transacciones del usuario indicado."""
        db = self.bot.db

        # Si no se especifica usuario, usa el propio
        target = usuario or interaction.user
        target_id = str(target.id)

        tipo = tipo.lower()                         # Normaliza a minúsculas

        if tipo == "games":
            # ── HISTORIAL DE JUEGOS ───────────────────────────
            logs_data = await db.get_game_logs(target_id, limit=PAGE_SIZE)

            embed = discord.Embed(
                title=f"🎲 Historial de Juegos — {target.display_name}",
                color=COLOR_PURPLE
            )

            if not logs_data:
                embed.description = "No hay partidas registradas."
            else:
                lines = []
                for entry in logs_data:
                    # Determina el emoji según el resultado
                    result_emoji = {
                        "win":  "✅",
                        "lose": "❌",
                        "tie":  "🤝"
                    }.get(entry["result"], "❓")

                    profit = entry["profit"]           # Ganancia neta
                    profit_str = f"+{fmt_gems(profit)}" if profit >= 0 else fmt_gems(profit)

                    # Formatea cada línea del historial
                    line = (
                        f"{result_emoji} **{entry['game'].capitalize()}** — "
                        f"Apuesta: {fmt_gems(entry['bet'])} | "
                        f"Resultado: {profit_str}"
                    )
                    lines.append(line)

                embed.description = "\n".join(lines)   # Junta todas las líneas

            embed.set_footer(text=f"Mostrando últimas {PAGE_SIZE} partidas")
            embed.set_thumbnail(url=target.display_avatar.url)

        elif tipo == "deposits":
            # ── HISTORIAL DE TRANSACCIONES ────────────────────
            txs = await db.get_user_transactions(target_id, limit=PAGE_SIZE)

            embed = discord.Embed(
                title=f"💳 Historial de Transacciones — {target.display_name}",
                color=COLOR_INFO
            )

            if not txs:
                embed.description = "No hay transacciones registradas."
            else:
                lines = []
                for tx in txs:
                    # Emoji según tipo de transacción
                    type_emoji = "📥" if tx["type"] == "deposit" else "📤"

                    # Emoji según estado
                    status_emoji = {
                        "confirmed": "✅",
                        "pending":   "⏳",
                        "rejected":  "❌"
                    }.get(tx["status"], "❓")

                    # Agente que procesó (si hay)
                    agent_str = f"<@{tx['agent_id']}>" if tx["agent_id"] else "—"

                    # Fecha formateada
                    ts = tx["timestamp"][:10] if tx["timestamp"] else "—"

                    line = (
                        f"{type_emoji} {status_emoji} **{tx['type'].capitalize()}** — "
                        f"{fmt_gems(tx['amount'])} | Agente: {agent_str} | {ts}"
                    )
                    lines.append(line)

                embed.description = "\n".join(lines)

            embed.set_footer(text=f"Mostrando últimas {PAGE_SIZE} transacciones")
            embed.set_thumbnail(url=target.display_avatar.url)

        else:
            # Tipo no reconocido
            await interaction.response.send_message(
                embed=error_embed("Tipo inválido. Usa 'games' o 'deposits'."),
                ephemeral=True
            )
            return

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Logs(bot))
