# ============================================================
# cogs/codes.py — Comandos de códigos de canje
# ============================================================
# /code create  → crea un código (solo owner/admin de Railway)
# /code redeem  → canjea un código (cualquier usuario linkeado)
# /code delete  → elimina un código (solo owner/admin)
# /code list    → lista todos los códigos (solo owner/admin)
#
# Los permisos se leen de las variables de entorno de Railway:
#   OWNER_IDS=123456,789012
#   ADMIN_IDS=111111,222222
# ============================================================

import discord
from discord.ext import commands
from discord import app_commands
from utils import (
    is_owner, is_admin,
    check_linked, fmt_gems,
    error_embed, success_embed,
    COLOR_GOLD, COLOR_INFO, COLOR_ERROR
)


class Codes(commands.Cog):
    """Módulo de códigos de canje de gemas."""

    def __init__(self, bot):
        self.bot = bot

    # Grupo de subcomandos /code
    code_group = app_commands.Group(name="code", description="Gestión de códigos de canje")

    # ── /code create ──────────────────────────────────────────
    @code_group.command(name="create", description="[OWNER/ADMIN] Crea un código de canje")
    @app_commands.describe(
        codigo="El código a crear (ej: VERANO2025)",
        gemas="Cuántas gemas da al canjearlo",
        usos="Número de veces que se puede canjear"
    )
    async def create(
        self,
        interaction: discord.Interaction,
        codigo: str,
        gemas: int,
        usos: int
    ):
        """
        Crea un código que inyecta gemas al sistema sin descontarlas de nadie.
        Solo accesible por los IDs en OWNER_IDS o ADMIN_IDS en Railway.
        """
        # Verifica permisos — lee directamente las variables de Railway
        if not is_admin(interaction.user.id):
            await interaction.response.send_message(
                embed=error_embed(
                    "No tienes permisos para crear códigos.\n"
                    "Solo los owners y admins configurados en Railway pueden hacerlo."
                ),
                ephemeral=True
            )
            return

        if gemas <= 0:
            await interaction.response.send_message(
                embed=error_embed("Las gemas deben ser mayor a 0."), ephemeral=True
            )
            return

        if usos <= 0:
            await interaction.response.send_message(
                embed=error_embed("Los usos deben ser mayor a 0."), ephemeral=True
            )
            return

        # El código se guarda en mayúsculas siempre
        codigo = codigo.upper().strip()

        # Comprueba que el código no exista ya
        existing = await self.bot.db.get_code(codigo)
        if existing:
            await interaction.response.send_message(
                embed=error_embed(f"El código **{codigo}** ya existe."), ephemeral=True
            )
            return

        await self.bot.db.create_code(
            code       = codigo,
            gems       = gemas,
            total_uses = usos,
            created_by = str(interaction.user.id)
        )

        embed = discord.Embed(title="✅ Código Creado", color=COLOR_GOLD)
        embed.add_field(name="Código",  value=f"`{codigo}`",      inline=True)
        embed.add_field(name="Gemas",   value=fmt_gems(gemas),    inline=True)
        embed.add_field(name="Usos",    value=f"{usos}x",         inline=True)
        embed.set_footer(text=f"Creado por {interaction.user.display_name}")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /code redeem ──────────────────────────────────────────
    @code_group.command(name="redeem", description="Canjea un código para obtener gemas")
    @app_commands.describe(codigo="El código a canjear")
    async def redeem(self, interaction: discord.Interaction, codigo: str):
        """
        Canjea un código y añade gemas a tu balance.
        Cada usuario solo puede canjear el mismo código una vez.
        """
        if not await check_linked(interaction):
            return

        codigo  = codigo.upper().strip()
        user_id = str(interaction.user.id)

        # Comprueba que el código existe
        code_data = await self.bot.db.get_code(codigo)
        if not code_data:
            await interaction.response.send_message(
                embed=error_embed("Código inválido o inexistente."),
                ephemeral=True
            )
            return

        # Comprueba que tiene usos disponibles
        if code_data["used_count"] >= code_data["total_uses"]:
            await interaction.response.send_message(
                embed=error_embed("Este código ya ha agotado todos sus usos."),
                ephemeral=True
            )
            return

        # Comprueba que este usuario no lo haya canjeado ya
        already = await self.bot.db.has_redeemed(codigo, user_id)
        if already:
            await interaction.response.send_message(
                embed=error_embed("Ya has canjeado este código anteriormente."),
                ephemeral=True
            )
            return

        # Canjea el código — añade gemas SIN descontar de nadie (dinero externo)
        gems_received = await self.bot.db.redeem_code(codigo, user_id)

        if gems_received == 0:
            await interaction.response.send_message(
                embed=error_embed("No se pudo canjear el código. Inténtalo de nuevo."),
                ephemeral=True
            )
            return

        new_bal = await self.bot.db.get_balance(user_id)
        uses_left = code_data["total_uses"] - code_data["used_count"] - 1

        embed = discord.Embed(
            title="🎁 ¡Código Canjeado!",
            description=f"Has recibido {fmt_gems(gems_received)}",
            color=COLOR_GOLD
        )
        embed.add_field(name="Código",     value=f"`{codigo}`",          inline=True)
        embed.add_field(name="Recibido",   value=fmt_gems(gems_received), inline=True)
        embed.add_field(name="Usos restantes", value=str(max(0, uses_left)), inline=True)
        embed.set_footer(text=f"Saldo actual: {fmt_gems(new_bal)}")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /code delete ──────────────────────────────────────────
    @code_group.command(name="delete", description="[OWNER/ADMIN] Elimina un código")
    @app_commands.describe(codigo="El código a eliminar")
    async def delete(self, interaction: discord.Interaction, codigo: str):
        """Elimina un código del sistema. Solo owner/admin."""
        if not is_admin(interaction.user.id):
            await interaction.response.send_message(
                embed=error_embed("Sin permisos."), ephemeral=True
            )
            return

        codigo = codigo.upper().strip()

        existing = await self.bot.db.get_code(codigo)
        if not existing:
            await interaction.response.send_message(
                embed=error_embed(f"El código `{codigo}` no existe."), ephemeral=True
            )
            return

        await self.bot.db.delete_code(codigo)

        await interaction.response.send_message(
            embed=success_embed("Código Eliminado", f"El código `{codigo}` ha sido eliminado."),
            ephemeral=True
        )

    # ── /code list ────────────────────────────────────────────
    @code_group.command(name="list", description="[OWNER/ADMIN] Lista todos los códigos activos")
    async def list_codes(self, interaction: discord.Interaction):
        """Muestra todos los códigos con sus estadísticas. Solo owner/admin."""
        if not is_admin(interaction.user.id):
            await interaction.response.send_message(
                embed=error_embed("Sin permisos."), ephemeral=True
            )
            return

        codes = await self.bot.db.list_codes()

        if not codes:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="No hay códigos creados.",
                    color=COLOR_INFO
                ),
                ephemeral=True
            )
            return

        embed = discord.Embed(title="🎁 Códigos Activos", color=COLOR_INFO)

        for code in codes[:20]:         # Máximo 20 para no superar el límite de Discord
            remaining = code["total_uses"] - code["used_count"]
            embed.add_field(
                name=f"`{code['code']}`",
                value=(
                    f"💎 {fmt_gems(code['gems'])}\n"
                    f"Usos: {code['used_count']}/{code['total_uses']} "
                    f"({remaining} restantes)"
                ),
                inline=True
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Codes(bot))
