# ============================================================
# cogs/rain.py — Comando /rain (Lluvia de Gemas)
# ============================================================
# Flujo completo:
#  1. /rain monto:500000000 duracion:5
#  2. Bot muestra botones con los roles de wager configurados
#  3. Creador elige qué rol se requiere para participar
#  4. Se publica el reto con botón "Participar" y cuenta atrás
#  5. Al participar, se verifica al instante si tiene el rol
#  6. Al expirar, se divide el monto entre los participantes
# ============================================================

import discord
from discord.ext import commands
from discord import app_commands
import asyncio
from datetime import datetime, timezone
from utils import check_linked, fmt_gems, error_embed, success_embed, COLOR_GOLD, COLOR_INFO, COLOR_ERROR

MIN_AMOUNT  = 250_000_000               # Mínimo 250M de gemas
MAX_MINUTES = 15                        # Máximo 15 minutos


# ── Vista 1: El creador elige el rol de wager requerido ──────
class ChooseRoleView(discord.ui.View):
    """
    Muestra un botón por cada rol de wager configurado.
    El creador elige cuál es el requisito para esta rain.
    Solo visible para el creador (ephemeral).
    """

    def __init__(self, cog, creator, amount, duration_mins, wager_roles, guild):
        super().__init__(timeout=60)
        self.cog          = cog
        self.creator      = creator
        self.amount       = amount
        self.duration     = duration_mins
        self.guild        = guild

        # Crea un botón por cada rol de wager configurado
        for role_data in wager_roles:
            role = guild.get_role(int(role_data["role_id"]))
            if not role:
                continue                # El rol no existe en el servidor, salta

            btn = discord.ui.Button(
                label=f"{role.name} ({fmt_gems(role_data['threshold'])}+)",
                style=discord.ButtonStyle.primary,
                custom_id=f"rain_role_{role.id}"
            )
            btn.callback = self._make_role_callback(role, role_data["threshold"])
            self.add_item(btn)

        # Botón especial: sin requisito de rol (cualquiera puede participar)
        no_role_btn = discord.ui.Button(
            label="🌍 Sin requisito (todos)",
            style=discord.ButtonStyle.secondary,
            custom_id="rain_role_none"
        )
        no_role_btn.callback = self._make_role_callback(None, 0)
        self.add_item(no_role_btn)

    def _make_role_callback(self, role, threshold):
        """Genera el callback para cuando el creador elige un rol."""
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.creator.id:
                await interaction.response.send_message("Esta no es tu rain.", ephemeral=True)
                return

            # Desactiva todos los botones
            for item in self.children:
                item.disabled = True

            role_name = role.name if role else "Todos"
            await interaction.response.edit_message(
                content=f"✅ Publicando rain con rol **{role_name}**...",
                view=self
            )

            # Publica la rain en el canal
            await self.cog.launch_rain(
                channel=interaction.channel,
                creator=self.creator,
                amount=self.amount,
                duration_mins=self.duration,
                required_role=role,
                required_threshold=threshold
            )

        return callback


# ── Vista 2: El reto público con botón Participar ────────────
class RainView(discord.ui.View):
    """
    Vista pública del reto de rain.
    Cualquiera puede intentar participar pero se verifica el rol al instante.
    """

    def __init__(self, cog, rain_id: str):
        super().__init__(timeout=None)  # El timeout lo gestiona asyncio, no discord.py
        self.cog     = cog
        self.rain_id = rain_id          # ID único de esta rain para buscarlo en cog.active_rains

    @discord.ui.button(label="🌧️ Participar", style=discord.ButtonStyle.success, custom_id="rain_join")
    async def participar(self, interaction: discord.Interaction, button: discord.ui.Button):
        """El usuario intenta unirse a la rain."""
        rain = self.cog.active_rains.get(self.rain_id)
        if not rain:
            await interaction.response.send_message("Esta rain ya terminó.", ephemeral=True)
            return

        user_id = interaction.user.id

        # No puede participar el creador
        if user_id == rain["creator"].id:
            await interaction.response.send_message(
                "No puedes participar en tu propia rain.", ephemeral=True
            )
            return

        # Ya está participando
        if user_id in rain["participants"]:
            await interaction.response.send_message(
                "✅ Ya estás participando en esta rain.", ephemeral=True
            )
            return

        # ── Verificación de rol al instante ───────────────────
        required_role = rain["required_role"]
        if required_role is not None:
            member = interaction.guild.get_member(user_id)
            if not member or required_role not in member.roles:
                await interaction.response.send_message(
                    embed=error_embed(
                        f"❌ No cumples con el rol de Wager necesario para esta Rain.\n"
                        f"Necesitas: **{required_role.name}**"
                    ),
                    ephemeral=True
                )
                return                  # No se añade a la lista

        # ── Registra la participación ──────────────────────────
        rain["participants"].add(user_id)
        count = len(rain["participants"])

        # Actualiza el embed con el nuevo conteo de participantes
        embed = self._build_embed(rain, count)
        await interaction.message.edit(embed=embed, view=self)

        await interaction.response.send_message(
            f"✅ Te has unido a la rain. Participantes: **{count}**",
            ephemeral=True
        )

    def _build_embed(self, rain: dict, participant_count: int) -> discord.Embed:
        """Construye el embed del estado actual de la rain."""
        embed = discord.Embed(
            title="🌧️ ¡Lluvia de Gemas!",
            color=COLOR_GOLD
        )
        embed.add_field(name="Premio Total",    value=fmt_gems(rain["amount"]),      inline=True)
        embed.add_field(name="Participantes",   value=f"👥 {participant_count}",     inline=True)

        # Timestamp dinámico de Discord — muestra cuenta atrás automáticamente
        end_ts = int(rain["ends_at"].timestamp())
        embed.add_field(name="Termina",         value=f"<t:{end_ts}:R>",             inline=True)

        role_req = rain["required_role"]
        embed.add_field(
            name="Requisito",
            value=role_req.mention if role_req else "🌍 Todos pueden participar",
            inline=True
        )
        embed.add_field(name="Creado por",      value=rain["creator"].mention,       inline=True)

        if participant_count > 0:
            per_person = rain["amount"] // participant_count
            embed.add_field(
                name="Si cobras ahora",
                value=fmt_gems(per_person),
                inline=True
            )

        embed.set_footer(text="Pulsa Participar para unirte • Se reparte al expirar")
        return embed


# ── COG PRINCIPAL ─────────────────────────────────────────────
class Rain(commands.Cog):
    """Módulo del comando /rain."""

    def __init__(self, bot):
        self.bot          = bot
        self.active_rains = {}          # {rain_id: dict con datos de la rain}
        self._rain_counter = 0          # Para generar IDs únicos

    def _new_id(self) -> str:
        """Genera un ID único para cada rain."""
        self._rain_counter += 1
        return f"rain_{self._rain_counter}"

    @app_commands.command(name="rain", description="Haz una lluvia de gemas para el servidor")
    @app_commands.describe(
        monto="Gemas a repartir (mínimo 250,000,000)",
        duracion="Minutos que dura la rain (1-15)"
    )
    async def rain(self, interaction: discord.Interaction, monto: int, duracion: int):
        """
        Inicia una lluvia de gemas. El monto se descuenta del balance del creador.
        Luego se elige el rol de wager requerido para participar.
        """
        if not await check_linked(interaction):
            return

        # Valida el monto mínimo
        if monto < MIN_AMOUNT:
            await interaction.response.send_message(
                embed=error_embed(
                    f"El monto mínimo para una rain es {fmt_gems(MIN_AMOUNT)}.\n"
                    f"Has puesto: {fmt_gems(monto)}"
                ),
                ephemeral=True
            )
            return

        # Valida la duración
        if not (1 <= duracion <= MAX_MINUTES):
            await interaction.response.send_message(
                embed=error_embed(f"La duración debe estar entre 1 y {MAX_MINUTES} minutos."),
                ephemeral=True
            )
            return

        # Comprueba saldo
        balance = await self.bot.db.get_balance(str(interaction.user.id))
        if balance < monto:
            await interaction.response.send_message(
                embed=error_embed(
                    f"No tienes suficientes gemas.\n"
                    f"Tienes: {fmt_gems(balance)}\n"
                    f"Necesitas: {fmt_gems(monto)}"
                ),
                ephemeral=True
            )
            return

        # Descuenta el monto inmediatamente
        await self.bot.db.remove_balance(str(interaction.user.id), monto)

        # Carga los roles de wager configurados
        wager_roles = await self.bot.db.get_wager_roles()

        if not wager_roles:
            # Sin roles configurados, lanza la rain directamente sin requisito
            await interaction.response.send_message(
                content="⚠️ No hay roles de wager configurados. La rain será para todos.",
                ephemeral=True
            )
            await self.launch_rain(
                channel=interaction.channel,
                creator=interaction.user,
                amount=monto,
                duration_mins=duracion,
                required_role=None,
                required_threshold=0
            )
            return

        # Muestra los botones de roles para que el creador elija
        embed = discord.Embed(
            title="🌧️ Configura tu Rain",
            description=(
                f"**Monto:** {fmt_gems(monto)}\n"
                f"**Duración:** {duracion} minuto(s)\n\n"
                "Elige qué rol de Wager se requiere para participar:"
            ),
            color=COLOR_INFO
        )
        view = ChooseRoleView(self, interaction.user, monto, duracion, wager_roles, interaction.guild)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def launch_rain(
        self,
        channel,
        creator,
        amount: int,
        duration_mins: int,
        required_role,
        required_threshold: int
    ):
        """
        Publica la rain en el canal y programa su finalización.
        Se llama tras que el creador elige el rol requerido.
        """
        rain_id  = self._new_id()
        ends_at  = datetime.now(timezone.utc).replace(microsecond=0)
        from datetime import timedelta
        ends_at += timedelta(minutes=duration_mins)

        # Registra la rain como activa
        self.active_rains[rain_id] = {
            "creator":       creator,
            "amount":        amount,
            "ends_at":       ends_at,
            "required_role": required_role,
            "participants":  set(),
            "message":       None,
        }

        rain   = self.active_rains[rain_id]
        view   = RainView(self, rain_id)
        embed  = view._build_embed(rain, 0)

        # Post in rain channel if configured, otherwise use current channel
        rain_channel_id = await self.bot.db.get_config("rain_channel")
        rain_role_id    = await self.bot.db.get_config("rain_role")
        if rain_channel_id:
            rain_ch = channel.guild.get_channel(int(rain_channel_id))
            if rain_ch:
                channel = rain_ch

        ping = f"<@&{rain_role_id}>" if rain_role_id else ""
        msg  = await channel.send(content=ping, embed=embed, view=view)
        rain["message"] = msg

        # Espera la duración y luego finaliza
        await asyncio.sleep(duration_mins * 60)
        await self._end_rain(rain_id)

    async def _end_rain(self, rain_id: str):
        """Finaliza la rain y distribuye las gemas entre los participantes."""
        rain = self.active_rains.pop(rain_id, None)
        if not rain:
            return

        participants = list(rain["participants"])
        amount       = rain["amount"]
        msg          = rain["message"]

        if not participants:
            # Nadie participó — devuelve el monto al creador
            await self.bot.db.add_balance(str(rain["creator"].id), amount)
            embed = discord.Embed(
                title="🌧️ Rain Finalizada — Sin Participantes",
                description=(
                    f"Nadie participó en la rain de {rain['creator'].mention}.\n"
                    f"Se devuelven {fmt_gems(amount)} al creador."
                ),
                color=COLOR_ERROR
            )
            if msg:
                try:
                    await msg.edit(embed=embed, view=None)
                except Exception:
                    pass
            return

        # Divide igualmente entre todos los participantes
        per_person   = amount // len(participants)
        remainder    = amount - (per_person * len(participants))  # Sobrante por redondeo

        # Añade el saldo a cada participante + wager requirement 1x
        mention_list = []
        for uid in participants:
            await self.bot.db.add_balance(str(uid), per_person)
            await self.bot.db.add_wager_requirement(str(uid), per_person)
            mention_list.append(f"<@{uid}>")

        # El sobrante vuelve al creador
        if remainder > 0:
            await self.bot.db.add_balance(str(rain["creator"].id), remainder)

        embed = discord.Embed(
            title="🌧️ ¡Rain Finalizada!",
            color=COLOR_GOLD
        )
        embed.add_field(name="Premio Total",    value=fmt_gems(amount),              inline=True)
        embed.add_field(name="Participantes",   value=f"👥 {len(participants)}",     inline=True)
        embed.add_field(name="Cada uno recibe", value=fmt_gems(per_person),          inline=True)
        embed.add_field(
            name="Ganadores",
            value=", ".join(mention_list[:20]) + ("..." if len(mention_list) > 20 else ""),
            inline=False
        )
        embed.set_footer(text=f"Creado por {rain['creator'].display_name} • Wager req 1x aplicado")

        if msg:
            try:
                await msg.edit(embed=embed, view=None)
            except Exception:
                pass


async def setup(bot):
    await bot.add_cog(Rain(bot))
