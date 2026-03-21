# ============================================================
# cogs/games/coinflip.py — Coinflip PvP completo
# ============================================================
# Flujo:
#  1. /coinflip apuesta:1000
#  2. Aparecen DOS botones: 🪙 Cara | ✨ Cruz  (el creador elige)
#  3. Se publica el reto en el canal con 3 botones:
#       [⚔️ Unirse]  [🤖 Call Bot]  (y el link al mensaje)
#  4a. Otro usuario pulsa Unirse → se le asigna el lado contrario
#  4b. El creador pulsa Call Bot → el bot juega como oponente
#  5. En ambos casos se lanza la moneda y se muestra el resultado
#
# - Funciona desde CUALQUIER canal
# - NO empieza automáticamente — espera acción del oponente
# - El bot puede actuar como oponente pagando del house (gems virtuales)
# ============================================================

import discord
from discord.ext import commands
from discord import app_commands
import random
import secrets
_rng = random.SystemRandom()
import asyncio
from utils import (
    parse_amount,
    check_linked, check_balance, fmt_gems,
    error_embed, update_wager_roles,
    COLOR_GOLD, COLOR_ERROR, COLOR_INFO, COLOR_PURPLE
)

# Emojis de cada lado
SIDE_EMOJI = {"cara": "🪙", "cruz": "✨"}
SIDE_LABEL = {"cara": "🪙 Cara", "cruz": "✨ Cruz"}


# ── Clase que representa un reto activo ──────────────────────
class CoinflipChallenge:
    """Datos de un reto de coinflip esperando oponente."""

    def __init__(self, creator: discord.Member, bet: int, creator_side: str):
        self.creator       = creator
        self.creator_id    = creator.id
        self.bet           = bet
        self.creator_side  = creator_side                           # 'cara' o 'cruz'
        self.opponent_side = "cruz" if creator_side == "cara" else "cara"
        self.message       = None                                   # Mensaje público del reto
        self.resolved      = False                                  # ¿Ya se resolvió?


# ──────────────────────────────────────────────────────────────
# VISTA 1: El creador elige su lado (ephemeral, solo para él)
# ──────────────────────────────────────────────────────────────
class ChooseSideView(discord.ui.View):
    """
    Vista inicial con dos botones: 🪙 Cara y ✨ Cruz.
    Solo visible para el creador (ephemeral).
    Al elegir, publica el reto en el canal.
    """

    def __init__(self, cog, bet: int, creator: discord.Member, channel, guild):
        super().__init__(timeout=30)
        self.cog     = cog
        self.bet     = bet
        self.creator = creator
        self.channel = channel          # Canal donde se ejecutó el comando
        self.guild   = guild            # Servidor para buscar el canal configurado

    @discord.ui.button(label="🪙 Cara",  style=discord.ButtonStyle.primary)
    async def btn_cara(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.creator.id:
            return
        await self._pick(interaction, "cara")

    @discord.ui.button(label="✨ Cruz", style=discord.ButtonStyle.primary)
    async def btn_cruz(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.creator.id:
            return
        await self._pick(interaction, "cruz")

    async def _pick(self, interaction: discord.Interaction, side: str):
        """El creador eligió su lado — publica el reto."""
        # Desactiva los botones de elección
        for item in self.children:
            item.disabled = True

        # Crea el reto y lo registra en el cog
        challenge = CoinflipChallenge(self.creator, self.bet, side)
        self.cog.active_challenges[self.creator.id] = challenge

        # Busca el canal de coinflip configurado por el owner
        cf_channel_id = await self.cog.bot.db.get_config("coinflip_channel")
        if cf_channel_id:
            target = self.guild.get_channel(int(cf_channel_id)) or self.channel
        else:
            target = self.channel   # Fallback al canal donde se ejecutó

        # Construye el embed del reto
        embed = _build_challenge_embed(challenge, pending=True)

        # Publica el reto en el canal correcto
        join_view = JoinView(self.cog, challenge)
        msg       = await target.send(embed=embed, view=join_view)
        challenge.message = msg

        # Link directo al mensaje del reto
        link = f"https://discord.com/channels/{interaction.guild_id}/{target.id}/{msg.id}"

        # Confirma al creador con el link
        canal_txt = f"en {target.mention}" if target.id != self.channel.id else "aquí"
        await interaction.response.edit_message(
            content=f"✅ Reto publicado {canal_txt} — **{SIDE_LABEL[side]}**\n[Ver coinflip]({link})",
            embed=None,
            view=self
        )


# ──────────────────────────────────────────────────────────────
# VISTA 2: El reto público (visible para todos en el canal)
# ──────────────────────────────────────────────────────────────
class JoinView(discord.ui.View):
    """
    Vista pública con tres botones:
    - ⚔️ Unirse   → otro usuario se une como oponente
    - 🤖 Call Bot → el bot hace de oponente automáticamente
    - ❌ Cancelar → solo el creador puede cancelar
    """

    def __init__(self, cog, challenge: CoinflipChallenge):
        super().__init__(timeout=600)       # 10 min para que alguien se una
        self.cog       = cog
        self.challenge = challenge

    async def on_timeout(self):
        """Nadie se unió — devuelve la apuesta y cierra el reto."""
        ch = self.challenge
        if ch.resolved:
            return
        ch.resolved = True
        self.cog.active_challenges.pop(ch.creator_id, None)

        # Devuelve el saldo al creador
        await self.cog.bot.db.add_balance(str(ch.creator_id), ch.bet)

        for item in self.children:
            item.disabled = True
        try:
            embed = discord.Embed(
                title="🪙 Coinflip — Expirado",
                description=(
                    f"Nadie aceptó el reto de {ch.creator.mention}.\n"
                    f"Se devuelven {fmt_gems(ch.bet)}."
                ),
                color=0x95a5a6
            )
            await ch.message.edit(embed=embed, view=self)
        except Exception:
            pass

    # ── Botón: Unirse ─────────────────────────────────────────
    @discord.ui.button(label="⚔️ Unirse", style=discord.ButtonStyle.success, row=0)
    async def unirse(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Otro jugador se une como oponente."""
        ch = self.challenge

        # El creador no puede unirse a su propio reto
        if interaction.user.id == ch.creator_id:
            await interaction.response.send_message(
                "No puedes unirte a tu propio reto.", ephemeral=True
            )
            return

        if ch.resolved:
            await interaction.response.send_message("Este reto ya terminó.", ephemeral=True)
            return

        # Verifica que está registrado
        user = await self.cog.bot.db.get_user(str(interaction.user.id))
        if not user or not user["roblox_name"]:
            await interaction.response.send_message(
                embed=error_embed("Debes vincular tu Roblox primero. Usa `/link`."),
                ephemeral=True
            )
            return

        # Verifica saldo
        bal = await self.cog.bot.db.get_balance(str(interaction.user.id))
        if bal < ch.bet:
            await interaction.response.send_message(
                embed=error_embed(
                    f"Saldo insuficiente.\n"
                    f"Necesitas: {fmt_gems(ch.bet)}\n"
                    f"Tienes: {fmt_gems(bal)}"
                ),
                ephemeral=True
            )
            return

        # Descuenta la apuesta del oponente
        await self.cog.bot.db.remove_balance(str(interaction.user.id), ch.bet)
        await self.cog.bot.db.add_wager(str(interaction.user.id), ch.bet)
        await self.cog.bot.db.reduce_wager_requirement(str(interaction.user.id), ch.bet)

        # Resuelve el coinflip contra un jugador real
        await self._resolve(interaction, opponent=interaction.user, vs_bot=False)

    # ── Botón: Call Bot ───────────────────────────────────────
    @discord.ui.button(label="🤖 Call Bot", style=discord.ButtonStyle.primary, row=0)
    async def callbot(self, interaction: discord.Interaction, button: discord.ui.Button):
        """
        Solo el creador puede llamar al bot.
        El bot actúa como oponente — paga con gemas virtuales del house.
        """
        ch = self.challenge

        # Solo el creador puede llamar al bot
        if interaction.user.id != ch.creator_id:
            await interaction.response.send_message(
                "Solo el creador del reto puede llamar al bot.", ephemeral=True
            )
            return

        if ch.resolved:
            await interaction.response.send_message("Este reto ya terminó.", ephemeral=True)
            return

        # Resuelve el coinflip contra el bot (house)
        await self._resolve(interaction, opponent=None, vs_bot=True)

    # ── Botón: Cancelar ───────────────────────────────────────
    @discord.ui.button(label="❌ Cancelar", style=discord.ButtonStyle.danger, row=0)
    async def cancelar(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Solo el creador puede cancelar el reto."""
        ch = self.challenge

        if interaction.user.id != ch.creator_id:
            await interaction.response.send_message(
                "Solo el creador puede cancelar el reto.", ephemeral=True
            )
            return

        if ch.resolved:
            await interaction.response.send_message("Este reto ya terminó.", ephemeral=True)
            return

        ch.resolved = True
        self.cog.active_challenges.pop(ch.creator_id, None)

        # Devuelve el saldo al creador
        await self.cog.bot.db.add_balance(str(ch.creator_id), ch.bet)

        # Borra el mensaje silenciosamente — sin embed de cancelado
        try:
            await interaction.message.delete()
        except Exception:
            pass
        await interaction.response.defer()

    # ── Lógica de resolución (compartida) ─────────────────────
    async def _resolve(
        self,
        interaction: discord.Interaction,
        opponent: discord.Member | None,
        vs_bot: bool
    ):
        """
        Lanza la moneda y determina el ganador.
        vs_bot=True → el oponente es el house (sin coste para nadie extra).
        vs_bot=False → oponente es un jugador real que ya pagó.
        """
        ch = self.challenge
        if ch.resolved:
            return
        ch.resolved = True
        self.cog.active_challenges.pop(ch.creator_id, None)

        # Desactiva todos los botones
        for item in self.children:
            item.disabled = True

        # ── Animación de la moneda girando ────────────────────
        FRAMES = [
            ("🌀", "Lanzando la moneda..."),
            ("🪙", "· · · · · · · · ·"),
            ("✨", "· · · · · · · · ·"),
            ("🪙", "· · · · · · · ·"),
            ("✨", "· · · · · · ·"),
            ("🪙", "· · · · · ·"),
            ("✨", "· · · · ·"),
            ("🪙", "· · · ·"),
        ]
        DELAYS = [0.2, 0.2, 0.2, 0.3, 0.3, 0.4, 0.5, 0.6]
        for (emoji, text), delay in zip(FRAMES, DELAYS):
            spin = discord.Embed(title=f"{emoji}  Coinflip", description=f"**{text}**", color=COLOR_PURPLE)
            try:
                await interaction.response.edit_message(embed=spin, view=self)
            except Exception:
                try:
                    await interaction.edit_original_response(embed=spin, view=self)
                except Exception:
                    pass
            await asyncio.sleep(delay)

        # ── Lanza la moneda ───────────────────────────────────
        resultado = _rng.choice(["cara", "cruz"])

        # Obtiene el house edge
        edge          = await self.cog.bot.db.get_house_edge("coinflip")
        payout_factor = 1 - (edge / 100)          # Ej: 0.95 con 5% edge

        # Determina ganador y perdedor
        creator_wins = (resultado == ch.creator_side)

        if vs_bot:
            # ── Contra el bot ──────────────────────────────────
            if creator_wins:
                # El creador gana — el bot le paga (house paga)
                payout = int(ch.bet * 2 * payout_factor)
                await self.cog.bot.db.add_balance(str(ch.creator_id), payout)
                ganador_nombre = ch.creator.mention
                perdedor_nombre = "🤖 Bot"
                ganador_profit  = payout - ch.bet
            else:
                # El bot gana — el creador ya pagó al inicio
                payout          = 0                 # No hay payout para el creador
                ganador_nombre  = "🤖 Bot"
                perdedor_nombre = ch.creator.mention
                ganador_profit  = ch.bet

            # Rakeback handled below after embed is built

            # Logs solo del creador (el bot no tiene cuenta)
            profit_creator = (payout - ch.bet) if creator_wins else -ch.bet
            await self.cog.bot.db.log_game(
                str(ch.creator_id), "coinflip", ch.bet,
                "win" if creator_wins else "lose", profit_creator
            )

            opponent_display = "🤖 Bot"
            opponent_side_label = SIDE_LABEL[ch.opponent_side]

        else:
            # ── Contra jugador real ────────────────────────────
            if creator_wins:
                ganador   = ch.creator
                perdedor  = opponent
            else:
                ganador   = opponent
                perdedor  = ch.creator

            payout = int(ch.bet * 2 * payout_factor)
            await self.cog.bot.db.add_balance(str(ganador.id), payout)
            await self.cog.bot.db.add_wager(str(ch.creator_id), ch.bet)

            # Rakeback handled below after embed is built

            # Logs para ambos jugadores
            profit_ganador  = payout - ch.bet
            profit_perdedor = -ch.bet
            await self.cog.bot.db.log_game(str(ganador.id),  "coinflip", ch.bet, "win",  profit_ganador)
            await self.cog.bot.db.log_game(str(perdedor.id), "coinflip", ch.bet, "lose", profit_perdedor)

            # Roles de wager para ambos
            guild = interaction.guild
            for m in [ch.creator, opponent]:
                member = guild.get_member(m.id)
                if member:
                    await update_wager_roles(self.cog.bot, guild, member)

            ganador_nombre  = ganador.mention
            perdedor_nombre = perdedor.mention
            ganador_profit  = profit_ganador
            opponent_display     = opponent.mention
            opponent_side_label  = SIDE_LABEL[ch.opponent_side]

        # ── Embed de resultado ────────────────────────────────
        embed = discord.Embed(
            title="🪙 Coinflip — Resultado",
            color=COLOR_GOLD
        )
        embed.add_field(
            name="La moneda cayó en",
            value=f"**{SIDE_LABEL[resultado]}**",
            inline=False
        )
        embed.add_field(
            name=f"{SIDE_EMOJI[ch.creator_side]} {ch.creator.display_name}",
            value=SIDE_LABEL[ch.creator_side],
            inline=True
        )
        embed.add_field(
            name=f"{SIDE_EMOJI[ch.opponent_side]} {'Bot' if vs_bot else opponent.display_name}",
            value=opponent_side_label,
            inline=True
        )
        embed.add_field(
            name="🏆 Ganador",
            value=f"{ganador_nombre} gana {fmt_gems(int(ch.bet * 2 * payout_factor))}",
            inline=False
        )

        # Texto de rakeback si aplica
        # Only show rakeback footer if a human lost
        human_loser_id = None
        if vs_bot and not creator_wins:
            human_loser_id = str(ch.creator_id)
        elif not vs_bot:
            human_loser_id = str(perdedor.id) if perdedor else None

        if human_loser_id:
            edge_cf      = await self.cog.bot.db.get_house_edge("coinflip")
            house_profit = int(ch.bet * edge_cf / 100)
            rakeback_pct = float(await self.cog.bot.db.get_config("rakeback_pct") or "20")
            rakeback_amt = int(house_profit * rakeback_pct / 100)
            if rakeback_amt > 0:
                await self.cog.bot.db.add_rakeback(human_loser_id, rakeback_amt)
                embed.set_footer(text=f"Rakeback +{fmt_gems(rakeback_amt)} acreditado al perdedor")

        await asyncio.sleep(0.3)
        try:
            await interaction.edit_original_response(embed=embed, view=self)
        except Exception:
            pass


# ── Helper: construye el embed del reto pendiente ─────────────
def _build_challenge_embed(challenge: CoinflipChallenge, pending: bool) -> discord.Embed:
    """Construye el embed del reto esperando oponente."""
    embed = discord.Embed(
        title="🪙 Coinflip — Reto Abierto",
        description=(
            f"{challenge.creator.mention} apuesta {fmt_gems(challenge.bet)}\n"
            f"Ha elegido **{SIDE_LABEL[challenge.creator_side]}**\n\n"
            f"¿Quién acepta **{SIDE_LABEL[challenge.opponent_side]}**?\n\n"
            f"Pulsa **⚔️ Unirse** para jugar contra él\n"
            f"o **🤖 Call Bot** para que el bot sea su rival"
        ),
        color=COLOR_PURPLE
    )
    embed.add_field(name="Apuesta",  value=fmt_gems(challenge.bet),             inline=True)
    embed.add_field(name="Premio",   value=fmt_gems(challenge.bet * 2),         inline=True)
    embed.set_thumbnail(url=challenge.creator.display_avatar.url)
    embed.set_footer(text="El creador puede llamar al bot si nadie se une • Expira en 10 min")
    return embed


# ── COG ───────────────────────────────────────────────────────
class Coinflip(commands.Cog):
    """Módulo del juego Coinflip PvP con opción Call Bot."""

    def __init__(self, bot):
        self.bot               = bot
        self.active_challenges = {}         # {creator_id: CoinflipChallenge}

    @app_commands.command(name="coinflip", description="Crea un reto de coinflip — elige tu lado")
    @app_commands.describe(apuesta="Cantidad de gemas a apostar")
    async def coinflip(self, interaction: discord.Interaction, apuesta: str):
        """
        Inicia un coinflip. Primero eliges tu lado con botones,
        luego esperas a que alguien se una o llamas al bot.
        """
        if not await check_linked(interaction):
            return

        apuesta = parse_amount(str(apuesta))
        if not apuesta or apuesta <= 0:
            await interaction.response.send_message(
                embed=error_embed("La apuesta debe ser mayor a 0."), ephemeral=True
            )
            return

        # Un usuario solo puede tener un reto abierto
        if interaction.user.id in self.active_challenges:
            ch       = self.active_challenges[interaction.user.id]
            msg      = ch.message
            msg_link = f"https://discord.com/channels/{interaction.guild_id}/{msg.channel.id}/{msg.id}"
            await interaction.response.send_message(
                embed=error_embed(f"Ya tienes un reto abierto → [Ver reto]({msg_link})"),
                ephemeral=True
            )
            return

        if not await check_balance(interaction, apuesta):
            return

        # Descuenta la apuesta del creador inmediatamente
        await self.bot.db.remove_balance(str(interaction.user.id), apuesta)
        await self.bot.db.add_wager(str(interaction.user.id), apuesta)
        await self.bot.db.reduce_wager_requirement(str(interaction.user.id), apuesta)

        # Muestra los botones de elección de lado (solo al creador, ephemeral)
        embed = discord.Embed(
            title="🪙 Coinflip — Elige tu lado",
            description=(
                f"Apuesta: **{fmt_gems(apuesta)}**\n\n"
                "¿Con qué lado vas?"
            ),
            color=COLOR_PURPLE
        )
        view = ChooseSideView(self, apuesta, interaction.user, interaction.channel, interaction.guild)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Coinflip(bot))
