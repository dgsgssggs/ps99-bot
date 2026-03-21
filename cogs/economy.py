# ============================================================
# cogs/economy.py — Sistema económico completo
# ============================================================
# Comandos: /link, /balance, /deposit, /withdraw
# También incluye botones para que los agentes confirmen
# depósitos y retiros desde los tickets generados.
# ============================================================

import discord                                  # Librería de Discord
import httpx                                    # Para verificar usuario de Roblox
from discord.ext import commands                # Comandos de discord.py
from discord import app_commands               # Slash commands
import os                                       # Variables de entorno
from utils import (
    parse_amount,
    check_linked, check_balance,
    fmt_gems, fmt,
    error_embed, success_embed,
    COLOR_INFO, COLOR_PURPLE, COLOR_SUCCESS, COLOR_ERROR
)

# ── Vista del botón Reclamar Rakeback ────────────────────────
class RakebackView(discord.ui.View):
    """Vista con el botón interactivo para reclamar el rakeback pendiente."""

    def __init__(self, user_id: str, amount: int, pct: float, db):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.amount  = amount
        self.pct     = pct
        self.db      = db
        # Desactiva el botón si no hay nada que reclamar
        if amount <= 0:
            for item in self.children:
                item.disabled = True

    @discord.ui.button(label="💸 Reclamar", style=discord.ButtonStyle.success)
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Reclama el rakeback y lo añade al balance del usuario."""
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("Este no es tu rakeback.", ephemeral=True)
            return

        # Recarga el amount por si cambió desde que se abrió el menú
        amount = await self.db.get_rakeback(self.user_id)
        if amount <= 0:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="❌ No tienes rakeback acumulado.",
                    color=0xE74C3C
                ),
                ephemeral=True
            )
            return

        # Ejecuta el claim: añade al balance y resetea a 0
        claimed = await self.db.claim_rakeback(self.user_id)
        new_bal = await self.db.get_balance(self.user_id)

        # Desactiva el botón tras reclamar
        button.disabled = True
        button.label    = "✅ Reclamado"

        embed = discord.Embed(
            title="✅ Rakeback Reclamado",
            description=(
                f"Se añadieron {fmt_gems(claimed)} a tu balance.\n"
                f"Porcentaje: **{self.pct}%** de tus pérdidas."
            ),
            color=0x2ECC71
        )
        embed.set_footer(text=f"Saldo actual: {fmt_gems(new_bal)}")
        await interaction.response.edit_message(embed=embed, view=self)


class CloseTicketView(discord.ui.View):
    """Botón para cerrar (eliminar) el canal de ticket tras procesar."""

    def __init__(self):
        super().__init__(timeout=300)   # 5 minutos para cerrar

    @discord.ui.button(label="🔒 Cerrar Ticket", style=discord.ButtonStyle.danger)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Elimina el canal del ticket."""
        await interaction.response.send_message("Cerrando ticket en 3 segundos...")
        import asyncio
        await asyncio.sleep(3)
        try:
            await interaction.channel.delete(reason=f"Ticket cerrado por {interaction.user.name}")
        except discord.Forbidden:
            await interaction.followup.send("Sin permisos para eliminar el canal.", ephemeral=True)


class ConfirmDepositView(discord.ui.View):
    """
    Vista con botones para confirmar o rechazar un depósito.
    Aparece en el canal de tickets de depósitos.
    """

    def __init__(self, tx_id: int, user_id: str, amount: int):
        super().__init__(timeout=None)          # Sin tiempo de expiración
        self.tx_id   = tx_id                   # ID de la transacción
        self.user_id = user_id                 # ID del usuario que depositó
        self.amount  = amount                  # Cantidad a depositar

    @discord.ui.button(label="✅ Confirmar Depósito", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Botón que confirma el depósito. Solo agentes con límite suficiente."""
        db = interaction.client.db                          # Base de datos

        # Verifica que quien confirma es un agente registrado
        agent = await db.get_agent(str(interaction.user.id))
        if not agent:
            await interaction.response.send_message(
                embed=error_embed("No tienes permisos de agente."),
                ephemeral=True
            )
            return

        # Verifica que el agente tiene límite suficiente
        ok = await db.use_agent_limit(str(interaction.user.id), self.amount)
        if not ok:
            available = agent["limit_total"] - agent["limit_used"]
            await interaction.response.send_message(
                embed=error_embed(
                    f"No tienes suficiente límite disponible.\n"
                    f"Límite disponible: {fmt_gems(available)}\n"
                    f"Se requieren: {fmt_gems(self.amount)}"
                ),
                ephemeral=True
            )
            return

        # Añade el saldo al usuario
        await db.add_balance(self.user_id, self.amount)

        # Añade wager requirement 1x del depósito
        await db.add_wager_requirement(self.user_id, self.amount)

        # Registra la transacción como confirmada
        await db.confirm_transaction(self.tx_id, str(interaction.user.id))

        # Registra en el canal de logs si está configurado
        log_channel_id = await db.get_config("log_channel")
        if log_channel_id:
            log_channel = interaction.guild.get_channel(int(log_channel_id))
            if log_channel:
                log_embed = discord.Embed(
                    title="📥 Depósito Confirmado",
                    color=COLOR_SUCCESS
                )
                log_embed.add_field(name="Usuario",    value=f"<@{self.user_id}>", inline=True)
                log_embed.add_field(name="Cantidad",   value=fmt_gems(self.amount), inline=True)
                log_embed.add_field(name="Agente",     value=interaction.user.mention, inline=True)
                log_embed.add_field(name="TX ID",      value=f"#{self.tx_id}", inline=True)
                await log_channel.send(embed=log_embed)

        # Actualiza el mensaje del ticket
        for child in self.children:
            child.disabled = True               # Desactiva todos los botones
        await interaction.message.edit(view=self)

        await interaction.response.send_message(
            embed=success_embed(
                "Depósito Confirmado",
                f"Se han añadido {fmt_gems(self.amount)} a <@{self.user_id}>."
            )
        )

        # Notifica al usuario por DM
        try:
            user = await interaction.client.fetch_user(int(self.user_id))
            await user.send(
                embed=success_embed(
                    "Depósito Confirmado",
                    f"Tu depósito de {fmt_gems(self.amount)} ha sido procesado. ✅"
                )
            )
        except Exception:
            pass                                # Ignora si no se pueden enviar DMs

    @discord.ui.button(label="❌ Rechazar", style=discord.ButtonStyle.red)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Botón para rechazar el depósito."""
        # Desactiva los botones
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)

        await interaction.response.send_message(
            embed=discord.Embed(
                description=f"❌ Depósito #{self.tx_id} rechazado por {interaction.user.mention}.",
                color=COLOR_ERROR
            )
        )


class ConfirmWithdrawView(discord.ui.View):
    """
    Vista con botones para confirmar o rechazar un retiro.
    Aparece en el canal de tickets de retiros.
    """

    def __init__(self, tx_id: int, user_id: str, amount: int):
        super().__init__(timeout=None)
        self.tx_id   = tx_id
        self.user_id = user_id
        self.amount  = amount

    @discord.ui.button(label="✅ Confirmar Retiro", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Confirma el retiro y descuenta el saldo."""
        db = interaction.client.db

        # Verifica que es agente
        agent = await db.get_agent(str(interaction.user.id))
        if not agent:
            await interaction.response.send_message(
                embed=error_embed("No tienes permisos de agente."), ephemeral=True
            )
            return

        # Confirma la transacción en la base de datos
        await db.confirm_transaction(self.tx_id, str(interaction.user.id))

        # Registra en logs
        log_channel_id = await db.get_config("log_channel")
        if log_channel_id:
            log_channel = interaction.guild.get_channel(int(log_channel_id))
            if log_channel:
                log_embed = discord.Embed(title="📤 Retiro Confirmado", color=COLOR_SUCCESS)
                log_embed.add_field(name="Usuario",  value=f"<@{self.user_id}>", inline=True)
                log_embed.add_field(name="Cantidad", value=fmt_gems(self.amount), inline=True)
                log_embed.add_field(name="Agente",   value=interaction.user.mention, inline=True)
                await log_channel.send(embed=log_embed)

        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)

        await interaction.response.send_message(
            embed=success_embed("Retiro Confirmado", f"Retiro de {fmt_gems(self.amount)} procesado.")
        )

    @discord.ui.button(label="❌ Rechazar", style=discord.ButtonStyle.red)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Rechaza el retiro y devuelve el saldo al usuario."""
        db = interaction.client.db
        await db.add_balance(self.user_id, self.amount)

        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)

        await interaction.response.send_message(
            embed=discord.Embed(
                description=f"❌ Retiro rechazado. Se devolvieron {fmt_gems(self.amount)} a <@{self.user_id}>.",
                color=COLOR_ERROR
            )
        )
        if interaction.channel.name.startswith("withdraw-"):
            await interaction.followup.send(
                embed=discord.Embed(description="¿Cerrar el ticket?", color=COLOR_ERROR),
                view=CloseTicketView()
            )


# ── COG PRINCIPAL DE ECONOMÍA ─────────────────────────────────
class Economy(commands.Cog):
    """Módulo de economía: vinculación, balance, depósitos y retiros."""

    def __init__(self, bot):
        self.bot = bot                          # Referencia al bot principal

    # ── /link ─────────────────────────────────────────────────
    @app_commands.command(name="link", description="Vincula tu cuenta de Roblox al bot")
    @app_commands.describe(usuario_roblox="Tu nombre de usuario exacto en Roblox")
    async def link(self, interaction: discord.Interaction, usuario_roblox: str):
        """
        Verifica que el usuario de Roblox existe via la API oficial
        y luego lo vincula a la cuenta de Discord.
        """
        await interaction.response.defer(ephemeral=True)

        db      = self.bot.db
        user_id = str(interaction.user.id)

        # ── Verifica el usuario en la API de Roblox ───────────
        # Endpoint oficial: busca por username y retorna el ID y nombre exacto
        roblox_user = None
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    "https://users.roblox.com/v1/usernames/users",
                    json={"usernames": [usuario_roblox], "excludeBannedUsers": False}
                )
                if r.status_code == 200:
                    data = r.json().get("data", [])
                    if data:
                        # API retorna el nombre exacto con mayúsculas correctas
                        roblox_user = {
                            "id":   data[0]["id"],
                            "name": data[0]["name"]      # Nombre real con capitalización correcta
                        }
        except Exception:
            pass

        if not roblox_user:
            await interaction.followup.send(
                embed=error_embed(
                    f"No se encontró el usuario **{usuario_roblox}** en Roblox.\n"
                    "Asegúrate de escribir exactamente tu nombre de usuario."
                ),
                ephemeral=True
            )
            return

        # Usa el nombre exacto que devuelve Roblox (capitalización correcta)
        nombre_exacto = roblox_user["name"]
        roblox_id     = roblox_user["id"]

        existing = await db.get_user(user_id)

        if existing:
            await db.update_roblox(user_id, nombre_exacto)
            msg = f"Cuenta actualizada: **{nombre_exacto}**"
        else:
            await db.create_user(user_id, nombre_exacto)
            msg = f"Cuenta vinculada: **{nombre_exacto}** ✅"

        embed = discord.Embed(
            title="🔗 Cuenta Vinculada",
            description=msg,
            color=COLOR_SUCCESS
        )
        embed.add_field(name="Roblox ID", value=str(roblox_id), inline=True)
        embed.add_field(name="Username",  value=nombre_exacto,  inline=True)
        embed.set_footer(text=f"Discord: {interaction.user.name}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /balance ──────────────────────────────────────────────
    @app_commands.command(name="balance", description="Muestra tus gemas actuales")
    async def balance(self, interaction: discord.Interaction):
        """Muestra el balance de gemas y el total apostado del usuario."""
        if not await check_linked(interaction):
            return                              # Sale si no está vinculado

        db      = self.bot.db
        user_id = str(interaction.user.id)

        user    = await db.get_user(user_id)    # Datos completos del usuario
        bal     = user["balance"]               # Balance actual
        wagered = user["total_wagered"]         # Total apostado

        embed = discord.Embed(
            title=f"💎 Balance de {interaction.user.display_name}",
            color=COLOR_PURPLE
        )
        embed.add_field(name="Gemas",         value=fmt_gems(bal),     inline=True)
        embed.add_field(name="Total Apostado",value=fmt_gems(wagered), inline=True)
        embed.add_field(name="Roblox",        value=f"👤 {user['roblox_name']}", inline=False)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /deposit ──────────────────────────────────────────────
    @app_commands.command(name="deposit", description="Solicita un depósito de gemas")
    @app_commands.describe(cantidad="Cantidad de gemas a depositar")
    async def deposit(self, interaction: discord.Interaction, cantidad: str):
        """Crea un ticket de depósito para que un agente lo confirme."""
        if not await check_linked(interaction):
            return

        cantidad = parse_amount(str(cantidad))
        if not cantidad or cantidad <= 0:
            await interaction.response.send_message(
                embed=error_embed("La cantidad debe ser mayor a 0. Usa K/M/B (ej: 500k, 1m)"), ephemeral=True
            )
            return

        db      = self.bot.db
        user_id = str(interaction.user.id)
        user    = await db.get_user(user_id)

        # Crea la transacción pendiente en la base de datos
        tx_id = await db.create_transaction(user_id, "deposit", cantidad)

        agent_role_id = await db.get_config("agent_role")
        category_id   = await db.get_config("deposit_category")
        dep_channel_id = await db.get_config("deposit_channel")   # Fallback

        guild       = interaction.guild
        agent_role  = guild.get_role(int(agent_role_id)) if agent_role_id else None
        ticket_channel = None

        if category_id:
            # ── Crea canal privado dentro de la categoría ─────
            category = guild.get_channel(int(category_id))
            if category and isinstance(category, discord.CategoryChannel):
                # Permisos: solo el usuario y los agentes pueden ver el canal
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(read_messages=False),
                    interaction.user:   discord.PermissionOverwrite(read_messages=True, send_messages=True),
                    guild.me:           discord.PermissionOverwrite(read_messages=True, send_messages=True),
                }
                if agent_role:
                    overwrites[agent_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

                ticket_channel = await guild.create_text_channel(
                    name=f"deposit-{interaction.user.name}-{tx_id}",
                    category=category,
                    overwrites=overwrites,
                    reason=f"Ticket depósito #{tx_id}"
                )

        elif dep_channel_id:
            # Fallback: canal fijo configurado con /setchannel
            ticket_channel = guild.get_channel(int(dep_channel_id))

        if not ticket_channel:
            await interaction.response.send_message(
                embed=error_embed(
                    "No hay categoría ni canal de depósitos configurado.\n"
                    "Usa `/setcategory tipo:deposit` o `/setchannel tipo:deposit`."
                ),
                ephemeral=True
            )
            return

        # Construye el embed del ticket
        embed = discord.Embed(
            title=f"📥 Ticket de Depósito #{tx_id}",
            color=COLOR_INFO
        )
        embed.add_field(name="Usuario Discord", value=interaction.user.mention, inline=True)
        embed.add_field(name="Usuario Roblox",  value=user["roblox_name"],      inline=True)
        embed.add_field(name="Cantidad",        value=fmt_gems(cantidad),        inline=True)
        embed.add_field(name="Estado",          value="⏳ Pendiente",            inline=False)
        embed.set_footer(text=f"TX ID: #{tx_id}")

        ping = agent_role.mention if agent_role else "@staff"

        view = ConfirmDepositView(tx_id, user_id, cantidad)
        await ticket_channel.send(content=ping, embed=embed, view=view)

        # Link al canal del ticket
        ticket_link = f"https://discord.com/channels/{guild.id}/{ticket_channel.id}"

        await interaction.response.send_message(
            embed=success_embed(
                "Solicitud Enviada",
                f"Tu solicitud de depósito de {fmt_gems(cantidad)} ha sido enviada.\n"
                f"[Ver ticket]({ticket_link}) — TX ID: **#{tx_id}**"
            ),
            ephemeral=True
        )

    # ── /withdraw ─────────────────────────────────────────────
    @app_commands.command(name="withdraw", description="Solicita un retiro de gemas")
    @app_commands.describe(cantidad="Cantidad de gemas a retirar")
    async def withdraw(self, interaction: discord.Interaction, cantidad: str):
        """Crea un ticket de retiro. Descuenta el saldo inmediatamente (lo devuelve si se rechaza)."""
        if not await check_linked(interaction):
            return

        cantidad = parse_amount(str(cantidad))
        if not cantidad or cantidad <= 0:
            await interaction.response.send_message(
                embed=error_embed("La cantidad debe ser mayor a 0."), ephemeral=True
            )
            return

        db      = self.bot.db
        user_id = str(interaction.user.id)

        # Verifica que no tiene wager requirement pendiente
        pending = await db.get_wager_requirement(user_id)
        if pending > 0:
            await interaction.response.send_message(
                embed=error_embed(
                    f"Debes apostar antes de retirar.\n"
                    f"Wager pendiente: {fmt_gems(pending)}\n"
                    f"Juega en cualquier juego para reducirlo."
                ),
                ephemeral=True
            )
            return

        # Descuenta el saldo inmediatamente para reservarlo
        ok = await db.remove_balance(user_id, cantidad)
        if not ok:
            bal = await db.get_balance(user_id)
            await interaction.response.send_message(
                embed=error_embed(
                    f"Saldo insuficiente.\nTienes: {fmt_gems(bal)}\nNecesitas: {fmt_gems(cantidad)}"
                ),
                ephemeral=True
            )
            return

        user  = await db.get_user(user_id)
        tx_id = await db.create_transaction(user_id, "withdraw", cantidad)

        agent_role_id  = await db.get_config("agent_role")
        category_id    = await db.get_config("withdraw_category")
        ret_channel_id = await db.get_config("withdraw_channel")   # Fallback

        guild       = interaction.guild
        agent_role  = guild.get_role(int(agent_role_id)) if agent_role_id else None
        ticket_channel = None

        if category_id:
            # ── Crea canal privado dentro de la categoría ─────
            category = guild.get_channel(int(category_id))
            if category and isinstance(category, discord.CategoryChannel):
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(read_messages=False),
                    interaction.user:   discord.PermissionOverwrite(read_messages=True, send_messages=True),
                    guild.me:           discord.PermissionOverwrite(read_messages=True, send_messages=True),
                }
                if agent_role:
                    overwrites[agent_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

                ticket_channel = await guild.create_text_channel(
                    name=f"withdraw-{interaction.user.name}-{tx_id}",
                    category=category,
                    overwrites=overwrites,
                    reason=f"Ticket retiro #{tx_id}"
                )

        elif ret_channel_id:
            ticket_channel = guild.get_channel(int(ret_channel_id))

        if not ticket_channel:
            # Devuelve el saldo si no hay donde crear el ticket
            await db.add_balance(user_id, cantidad)
            await interaction.response.send_message(
                embed=error_embed(
                    "No hay categoría ni canal de retiros configurado.\n"
                    "Usa `/setcategory tipo:withdraw` o `/setchannel tipo:withdraw`."
                ),
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"📤 Ticket de Retiro #{tx_id}",
            color=0xE67E22
        )
        embed.add_field(name="Usuario Discord", value=interaction.user.mention, inline=True)
        embed.add_field(name="Usuario Roblox",  value=user["roblox_name"],      inline=True)
        embed.add_field(name="Cantidad",        value=fmt_gems(cantidad),        inline=True)

        ping = agent_role.mention if agent_role else "@staff"
        view = ConfirmWithdrawView(tx_id, user_id, cantidad)
        await ticket_channel.send(content=ping, embed=embed, view=view)

        ticket_link = f"https://discord.com/channels/{guild.id}/{ticket_channel.id}"

        await interaction.response.send_message(
            embed=success_embed(
                "Solicitud de Retiro Enviada",
                f"Tu solicitud de retiro de {fmt_gems(cantidad)} ha sido enviada.\n"
                f"[Ver ticket]({ticket_link}) — TX ID: **#{tx_id}**"
            ),
            ephemeral=True
        )


    # ── /tip ──────────────────────────────────────────────────
    @app_commands.command(name="tip", description="Envía gemas a otro usuario")
    @app_commands.describe(
        usuario="El usuario que recibirá las gemas",
        cantidad="Cantidad de gemas a enviar"
    )
    async def tip(self, interaction: discord.Interaction, usuario: discord.Member, cantidad: str):
        """Transfiere gemas de tu balance al de otro usuario."""
        if not await check_linked(interaction):
            return

        cantidad = parse_amount(str(cantidad))
        if not cantidad or cantidad <= 0:
            await interaction.response.send_message(
                embed=error_embed("Cantidad inválida. Usa K/M/B (ej: 500k, 1m)"), ephemeral=True
            )
            return

        if usuario.id == interaction.user.id:
            await interaction.response.send_message(
                embed=error_embed("No puedes enviarte gemas a ti mismo."), ephemeral=True
            )
            return

        if cantidad <= 0:
            await interaction.response.send_message(
                embed=error_embed("La cantidad debe ser mayor a 0."), ephemeral=True
            )
            return

        db          = self.bot.db
        sender_id   = str(interaction.user.id)
        receiver_id = str(usuario.id)

        # Verifica que el receptor tiene cuenta
        receiver = await db.get_user(receiver_id)
        if not receiver:
            await interaction.response.send_message(
                embed=error_embed(f"{usuario.display_name} no tiene cuenta vinculada."),
                ephemeral=True
            )
            return

        # Descuenta del emisor
        ok = await db.remove_balance(sender_id, cantidad)
        if not ok:
            bal = await db.get_balance(sender_id)
            await interaction.response.send_message(
                embed=error_embed(
                    f"Saldo insuficiente.\nTienes: {fmt_gems(bal)} | Necesitas: {fmt_gems(cantidad)}"
                ),
                ephemeral=True
            )
            return

        # Añade al receptor + wager requirement 1x
        await db.add_balance(receiver_id, cantidad)
        await db.add_wager_requirement(receiver_id, cantidad)

        embed = discord.Embed(title="🎁 Tip Enviado", color=COLOR_SUCCESS)
        embed.add_field(name="De",       value=interaction.user.mention, inline=True)
        embed.add_field(name="Para",     value=usuario.mention,          inline=True)
        embed.add_field(name="Cantidad", value=fmt_gems(cantidad),        inline=True)
        sender_bal = await db.get_balance(sender_id)
        embed.set_footer(text=f"Tu saldo: {fmt_gems(sender_bal)}")

        await interaction.response.send_message(embed=embed)

        # DM al receptor
        try:
            notif = discord.Embed(
                title="🎁 ¡Recibiste un tip!",
                description=f"{interaction.user.mention} te envió {fmt_gems(cantidad)} 💎",
                color=COLOR_SUCCESS
            )
            await usuario.send(embed=notif)
        except Exception:
            pass

    # ── /rakeback ─────────────────────────────────────────────
    @app_commands.command(name="rakeback", description="Ver y reclamar tu rakeback acumulado")
    async def rakeback(self, interaction: discord.Interaction):
        """Muestra el rakeback pendiente con un botón para reclamarlo."""
        if not await check_linked(interaction):
            return

        db           = self.bot.db
        user_id      = str(interaction.user.id)
        amount       = await db.get_rakeback(user_id)
        rakeback_pct = float(await db.get_config("rakeback_pct") or "20")

        embed = discord.Embed(title="💸 Tu Rakeback", color=COLOR_INFO)
        embed.add_field(name="Acumulado",  value=fmt_gems(amount),   inline=True)
        embed.add_field(name="Porcentaje", value=f"{rakeback_pct}%", inline=True)
        embed.add_field(
            name="Cómo funciona",
            value=(
                f"Cada vez que pierdes en cualquier juego,\n"
                f"el **{rakeback_pct}%** de tu pérdida se acumula aquí.\n"
                "Pulsa **Reclamar** para añadirlo a tu balance."
            ),
            inline=False
        )

        # Vista con botón Reclamar interactivo
        view = RakebackView(user_id, amount, rakeback_pct, db)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ── Función de carga del módulo ───────────────────────────────
async def setup(bot):
    """Discord.py llama a esta función al cargar el módulo con load_extension."""
    await bot.add_cog(Economy(bot))
