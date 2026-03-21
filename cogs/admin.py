# ============================================================
# cogs/admin.py — Comandos de administración del bot
# ============================================================
# Solo accesibles por el OWNER configurado en .env
# Incluye: house edge, agentes, canales, roles de wager
# ============================================================

import discord
from discord.ext import commands
from discord import app_commands
from utils import (
    is_owner, fmt_gems, fmt,
    error_embed, success_embed,
    COLOR_INFO, COLOR_SUCCESS, COLOR_ERROR
)

# ── Juegos válidos para configurar house edge ─────────────────
VALID_GAMES = ["blackjack", "dice", "hilo", "coinflip", "mines", "keno"]

class Admin(commands.Cog):
    """Módulo de administración — solo para el owner del servidor."""

    def __init__(self, bot):
        self.bot = bot

    # ── Verificación de owner ─────────────────────────────────
    def owner_check(self, interaction: discord.Interaction) -> bool:
        """Retorna True solo si el usuario es el owner configurado."""
        return is_owner(interaction.user.id)

    # ── /sethouseedge ─────────────────────────────────────────
    @app_commands.command(
        name="sethouseedge",
        description="[OWNER] Configura el house edge de un juego"
    )
    @app_commands.describe(
        juego="Nombre del juego (blackjack, dice, hilo, coinflip, mines, keno)",
        porcentaje="House edge en % (ej: 5 = 5%)"
    )
    async def sethouseedge(self, interaction: discord.Interaction, juego: str, porcentaje: float):
        """Configura la ventaja de la casa para un juego específico."""
        if not self.owner_check(interaction):
            await interaction.response.send_message(
                embed=error_embed("Solo el owner puede usar este comando."), ephemeral=True
            )
            return

        # Valida que el nombre del juego sea correcto
        juego = juego.lower()
        if juego not in VALID_GAMES:
            await interaction.response.send_message(
                embed=error_embed(
                    f"Juego inválido. Opciones: {', '.join(VALID_GAMES)}"
                ),
                ephemeral=True
            )
            return

        # Valida que el porcentaje sea razonable
        if not (0 <= porcentaje <= 50):
            await interaction.response.send_message(
                embed=error_embed("El porcentaje debe estar entre 0 y 50."), ephemeral=True
            )
            return

        # Guarda el house edge en la base de datos
        await self.bot.db.set_house_edge(juego, porcentaje)

        await interaction.response.send_message(
            embed=success_embed(
                "House Edge Actualizado",
                f"**{juego.capitalize()}**: `{porcentaje}%` de ventaja para la casa."
            ),
            ephemeral=True
        )

    # ── /setagentlimit ────────────────────────────────────────
    @app_commands.command(
        name="setagentlimit",
        description="[OWNER] Asigna un límite de depósitos a un agente"
    )
    @app_commands.describe(
        agente="El miembro a quien asignar como agente",
        cantidad="Límite máximo que puede procesar en depósitos"
    )
    async def setagentlimit(self, interaction: discord.Interaction, agente: discord.Member, cantidad: int):
        """Define cuántas gemas puede procesar un agente antes de necesitar recarga."""
        if not self.owner_check(interaction):
            await interaction.response.send_message(
                embed=error_embed("Solo el owner puede usar este comando."), ephemeral=True
            )
            return

        if cantidad <= 0:
            await interaction.response.send_message(
                embed=error_embed("La cantidad debe ser mayor a 0."), ephemeral=True
            )
            return

        # Guarda o actualiza el límite del agente
        await self.bot.db.set_agent_limit(str(agente.id), cantidad)

        await interaction.response.send_message(
            embed=success_embed(
                "Límite de Agente Configurado",
                f"**{agente.display_name}** puede procesar hasta {fmt_gems(cantidad)} en depósitos."
            )
        )

    # ── /resetagent ───────────────────────────────────────────
    @app_commands.command(
        name="resetagent",
        description="[OWNER] Resetea el límite usado de un agente"
    )
    @app_commands.describe(agente="El agente a resetear")
    async def resetagent(self, interaction: discord.Interaction, agente: discord.Member):
        """Resetea el contador de uso del agente a 0 (recarga su límite)."""
        if not self.owner_check(interaction):
            await interaction.response.send_message(
                embed=error_embed("Solo el owner puede usar este comando."), ephemeral=True
            )
            return

        # Verifica que existe como agente
        agent_data = await self.bot.db.get_agent(str(agente.id))
        if not agent_data:
            await interaction.response.send_message(
                embed=error_embed(f"{agente.display_name} no es un agente registrado."),
                ephemeral=True
            )
            return

        # Resetea el límite usado
        await self.bot.db.reset_agent_limit(str(agente.id))

        await interaction.response.send_message(
            embed=success_embed(
                "Agente Reseteado",
                f"El límite de **{agente.display_name}** ha sido recargado a {fmt_gems(agent_data['limit_total'])}."
            )
        )

    # ── /agentstatus ──────────────────────────────────────────
    @app_commands.command(
        name="agentstatus",
        description="[OWNER] Ver el estado de límite de un agente"
    )
    @app_commands.describe(agente="El agente a consultar")
    async def agentstatus(self, interaction: discord.Interaction, agente: discord.Member):
        """Muestra el límite total, usado y disponible de un agente."""
        if not self.owner_check(interaction):
            await interaction.response.send_message(
                embed=error_embed("Solo el owner puede usar este comando."), ephemeral=True
            )
            return

        data = await self.bot.db.get_agent(str(agente.id))
        if not data:
            await interaction.response.send_message(
                embed=error_embed(f"{agente.display_name} no es un agente."), ephemeral=True
            )
            return

        total     = data["limit_total"]            # Límite total asignado
        used      = data["limit_used"]             # Lo que ya usó
        available = total - used                   # Lo que le queda

        embed = discord.Embed(
            title=f"📊 Estado de Agente: {agente.display_name}",
            color=COLOR_INFO
        )
        embed.add_field(name="Límite Total",     value=fmt_gems(total),     inline=True)
        embed.add_field(name="Usado",            value=fmt_gems(used),      inline=True)
        embed.add_field(name="Disponible",       value=fmt_gems(available), inline=True)
        embed.set_thumbnail(url=agente.display_avatar.url)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /setchannel ───────────────────────────────────────────
    @app_commands.command(
        name="setchannel",
        description="[OWNER] Configura un canal del bot"
    )
    @app_commands.describe(
        tipo="Tipo de canal",
        canal="El canal a configurar"
    )
    @app_commands.choices(tipo=[
        app_commands.Choice(name="deposit  — Tickets de depósito",   value="deposit"),
        app_commands.Choice(name="withdraw — Tickets de retiro",      value="withdraw"),
        app_commands.Choice(name="log      — Logs del sistema",       value="log"),
        app_commands.Choice(name="coinflip — Canal de coinflips",     value="coinflip"),
        app_commands.Choice(name="rain     — Canal de rains",         value="rain"),
        app_commands.Choice(name="codes    — Canal de códigos",       value="codes"),
    ])
    async def setchannel(self, interaction: discord.Interaction, tipo: str, canal: discord.TextChannel):
        """Configura los canales usados por el bot."""
        if not self.owner_check(interaction):
            await interaction.response.send_message(
                embed=error_embed("Solo el owner puede usar este comando."), ephemeral=True
            )
            return

        # Mapa de tipos de canal a claves de configuración
        channel_map = {
            "deposit":  "deposit_channel",
            "withdraw": "withdraw_channel",
            "log":      "log_channel",
            "coinflip": "coinflip_channel",
            "codes":    "codes_channel",      # Canal donde se postean los códigos canjeados
            "rain":     "rain_channel"        # Canal donde se postean las rains
        }

        tipo = tipo.lower()
        if tipo not in channel_map:
            await interaction.response.send_message(
                embed=error_embed(f"Tipo inválido. Opciones: {', '.join(channel_map.keys())}"),
                ephemeral=True
            )
            return

        # Guarda el ID del canal en la configuración
        await self.bot.db.set_config(channel_map[tipo], str(canal.id))

        await interaction.response.send_message(
            embed=success_embed(
                "Canal Configurado",
                f"Canal **{tipo}** establecido en {canal.mention}."
            )
        )

    # ── /setcategory ──────────────────────────────────────────
    @app_commands.command(
        name="setcategory",
        description="[OWNER] Categoría donde se crearán los tickets de depósito/retiro"
    )
    @app_commands.describe(
        tipo="Tipo: deposit o withdraw",
        categoria="La categoría de Discord donde se crearán los canales"
    )
    async def setcategory(self, interaction: discord.Interaction, tipo: str, categoria: discord.CategoryChannel):
        """
        Configura la categoría donde el bot creará canales privados de ticket.
        Cada depósito/retiro abre su propio canal privado dentro de esta categoría.
        """
        if not self.owner_check(interaction):
            await interaction.response.send_message(
                embed=error_embed("Solo el owner puede usar este comando."), ephemeral=True
            )
            return

        tipo = tipo.lower()
        if tipo not in ("deposit", "withdraw"):
            await interaction.response.send_message(
                embed=error_embed("Tipo inválido. Usa 'deposit' o 'withdraw'."), ephemeral=True
            )
            return

        # Guarda el ID de la categoría en la config
        await self.bot.db.set_config(f"{tipo}_category", str(categoria.id))

        await interaction.response.send_message(
            embed=success_embed(
                "Categoría Configurada",
                f"Los tickets de **{tipo}** se crearán en la categoría **{categoria.name}**.\n"
                f"Cada ticket será un canal privado solo visible para el usuario y los agentes."
            )
        )

    # ── /setagentrole ─────────────────────────────────────────
    @app_commands.command(
        name="setagentrole",
        description="[OWNER] Configura el rol de agentes"
    )
    @app_commands.describe(rol="El rol que identifica a los agentes")
    async def setagentrole(self, interaction: discord.Interaction, rol: discord.Role):
        """Configura qué rol recibe el ping en los tickets de depósito/retiro."""
        if not self.owner_check(interaction):
            await interaction.response.send_message(
                embed=error_embed("Solo el owner puede usar este comando."), ephemeral=True
            )
            return

        await self.bot.db.set_config("agent_role", str(rol.id))

        await interaction.response.send_message(
            embed=success_embed("Rol de Agentes", f"Los agentes serán identificados con {rol.mention}.")
        )

    # ── /addwagerrole ─────────────────────────────────────────
    @app_commands.command(
        name="addwagerrole",
        description="[OWNER] Añade un rol automático por wager"
    )
    @app_commands.describe(
        cantidad="Gemas apostadas requeridas para obtener el rol",
        rol="El rol a asignar cuando se alcanza la cantidad"
    )
    async def addwagerrole(self, interaction: discord.Interaction, cantidad: int, rol: discord.Role):
        """Configura un rol que se asigna automáticamente al alcanzar cierto wager."""
        if not self.owner_check(interaction):
            await interaction.response.send_message(
                embed=error_embed("Solo el owner puede usar este comando."), ephemeral=True
            )
            return

        await self.bot.db.add_wager_role(cantidad, str(rol.id))

        await interaction.response.send_message(
            embed=success_embed(
                "Rol de Wager Añadido",
                f"Al apostar {fmt_gems(cantidad)}, se asignará el rol {rol.mention}."
            )
        )

    # ── /removewagerrole ──────────────────────────────────────
    @app_commands.command(
        name="removewagerrole",
        description="[OWNER] Elimina un rol de wager automático"
    )
    @app_commands.describe(rol="El rol a eliminar del sistema de wager")
    async def removewagerrole(self, interaction: discord.Interaction, rol: discord.Role):
        """Elimina un rol del sistema de wager automático."""
        if not self.owner_check(interaction):
            await interaction.response.send_message(
                embed=error_embed("Solo el owner puede usar este comando."), ephemeral=True
            )
            return

        await self.bot.db.remove_wager_role(str(rol.id))

        await interaction.response.send_message(
            embed=success_embed("Rol Eliminado", f"El rol {rol.mention} fue removido del sistema de wager.")
        )

    # ── /setbalance ───────────────────────────────────────────
    @app_commands.command(
        name="setbalance",
        description="[OWNER] Establece el balance de un usuario"
    )
    @app_commands.describe(
        usuario="El usuario a modificar",
        cantidad="Nuevo balance (reemplaza el actual)"
    )
    async def setbalance(self, interaction: discord.Interaction, usuario: discord.Member, cantidad: int):
        """Fuerza el balance de un usuario a una cantidad específica (para correcciones)."""
        if not self.owner_check(interaction):
            await interaction.response.send_message(
                embed=error_embed("Solo el owner puede usar este comando."), ephemeral=True
            )
            return

        db = self.bot.db
        # Obtiene el balance actual y calcula la diferencia
        current = await db.get_balance(str(usuario.id))
        diff    = cantidad - current                   # Diferencia a añadir o quitar

        if diff > 0:
            await db.add_balance(str(usuario.id), diff)     # Añade la diferencia
        elif diff < 0:
            await db.remove_balance(str(usuario.id), abs(diff))  # Quita la diferencia

        await interaction.response.send_message(
            embed=success_embed(
                "Balance Modificado",
                f"**{usuario.display_name}** ahora tiene {fmt_gems(cantidad)}."
            )
        )

    # ── /houseedges ───────────────────────────────────────────
    @app_commands.command(
        name="houseedges",
        description="[OWNER] Ver todos los house edges configurados"
    )
    async def houseedges(self, interaction: discord.Interaction):
        """Muestra una lista de todos los house edges actuales."""
        if not self.owner_check(interaction):
            await interaction.response.send_message(
                embed=error_embed("Solo el owner puede usar este comando."), ephemeral=True
            )
            return

        embed = discord.Embed(title="🎲 House Edges Configurados", color=COLOR_INFO)

        # Muestra el house edge de cada juego
        for game in VALID_GAMES:
            edge = await self.bot.db.get_house_edge(game)
            embed.add_field(
                name=f"🎮 {game.capitalize()}",
                value=f"`{edge}%`",
                inline=True
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)


    # ── /setrakeback ──────────────────────────────────────────
    @app_commands.command(
        name="setrakeback",
        description="[OWNER] Configura el porcentaje de rakeback"
    )
    @app_commands.describe(porcentaje="Porcentaje de rakeback (0-50). Por defecto: 20")
    async def setrakeback(self, interaction: discord.Interaction, porcentaje: float):
        """Configura qué % de cada pérdida se devuelve como rakeback."""
        if not self.owner_check(interaction):
            await interaction.response.send_message(
                embed=error_embed("Solo el owner puede usar este comando."), ephemeral=True
            )
            return

        if not (0 <= porcentaje <= 50):
            await interaction.response.send_message(
                embed=error_embed("El porcentaje debe estar entre 0 y 50."), ephemeral=True
            )
            return

        await self.bot.db.set_config("rakeback_pct", str(porcentaje))

        await interaction.response.send_message(
            embed=success_embed(
                "Rakeback Configurado",
                f"Los jugadores recibirán el **{porcentaje}%** de sus pérdidas como rakeback.\n"
                f"Se acumula automáticamente y se reclama con `/rakeback`."
            )
        )


    # ── /setcodesrole ─────────────────────────────────────────
    @app_commands.command(
        name="setcodesrole",
        description="[OWNER] Rol que recibe ping cuando se canjea un código"
    )
    @app_commands.describe(rol="El rol a mencionar")
    async def setcodesrole(self, interaction: discord.Interaction, rol: discord.Role):
        if not self.owner_check(interaction):
            await interaction.response.send_message(
                embed=error_embed("Solo el owner puede usar este comando."), ephemeral=True
            )
            return
        await self.bot.db.set_config("codes_role", str(rol.id))
        await interaction.response.send_message(
            embed=success_embed("Rol de Códigos", f"{rol.mention} recibirá ping al canjear códigos.")
        )

    # ── /setrainrole ───────────────────────────────────────────
    @app_commands.command(
        name="setrainrole",
        description="[OWNER] Rol que recibe ping cuando hay una rain"
    )
    @app_commands.describe(rol="El rol a mencionar")
    async def setrainrole(self, interaction: discord.Interaction, rol: discord.Role):
        if not self.owner_check(interaction):
            await interaction.response.send_message(
                embed=error_embed("Solo el owner puede usar este comando."), ephemeral=True
            )
            return
        await self.bot.db.set_config("rain_role", str(rol.id))
        await interaction.response.send_message(
            embed=success_embed("Rol de Rain", f"{rol.mention} recibirá ping en cada rain.")
        )

    # ── /setwagerrequirement ───────────────────────────────────
    @app_commands.command(
        name="clearwager",
        description="[OWNER] Limpia el wager requirement de un usuario"
    )
    @app_commands.describe(usuario="El usuario al que limpiar el wager")
    async def clearwager(self, interaction: discord.Interaction, usuario: discord.Member):
        if not self.owner_check(interaction):
            await interaction.response.send_message(
                embed=error_embed("Solo el owner puede usar este comando."), ephemeral=True
            )
            return
        await self.bot.db.reduce_wager_requirement(str(usuario.id), 999_999_999_999)
        await interaction.response.send_message(
            embed=success_embed("Wager Limpiado", f"Se eliminó el wager requirement de {usuario.mention}.")
        )


# ── Función de carga del módulo ───────────────────────────────
async def setup(bot):
    await bot.add_cog(Admin(bot))
