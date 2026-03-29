# cogs/games/crash.py — Juego del Cohete (Crash)
# ============================================================
# El cohete despega y el multiplicador sube.
# Los jugadores apuestan durante 10s de betting window.
# Pueden hacer cashout en cualquier momento.
# Si no cashean antes del crash, pierden todo.
# 5% house edge: E[crash] = 1/0.95 ≈ 1.053x
#
# Variables Railway:
#   CRASH_CHANNEL_ID (o configurar con /setchannel tipo:crash)
# ============================================================

import discord
from discord.ext import commands
from discord import app_commands
from discord.ext import tasks
import asyncio
import math
import random
_rng = random.SystemRandom()
from utils import (
    parse_amount, check_linked, fmt_gems,
    error_embed, update_wager_roles,
    COLOR_GOLD, COLOR_ERROR, COLOR_INFO, COLOR_PURPLE
)

BETTING_WINDOW  = 10       # segundos para apostar
TICK_INTERVAL   = 0.5      # segundos entre actualizaciones del embed
MIN_CRASH       = 1.01     # multiplicador mínimo de crash
HOUSE_EDGE      = 0.05     # 5%

def generate_crash_point() -> float:
    """
    Genera el punto de crash con 5% house edge.
    Formula: 0.95 / (1 - random) con mínimo 1.01
    Distribución: P(crash > x) = 0.95/x para x >= 1
    """
    r = _rng.random()
    if r >= 0.95:
        return MIN_CRASH    # ~5% del tiempo crash inmediato (house edge)
    crash = 0.95 / (1 - r)
    return round(max(MIN_CRASH, crash), 2)


class CrashBet:
    """Apuesta de un jugador en la ronda actual."""
    def __init__(self, user: discord.Member, amount: int, auto_cashout: float | None):
        self.user         = user
        self.amount       = amount
        self.auto_cashout = auto_cashout    # Multiplicador para auto-cashout (None = manual)
        self.cashed_out   = False
        self.cashout_mult = None            # Multiplicador al que cobró


class CrashGame:
    """Estado de la ronda actual de Crash."""
    IDLE    = "idle"
    BETTING = "betting"
    RUNNING = "running"
    CRASHED = "crashed"

    def __init__(self):
        self.state       = self.IDLE
        self.bets        = {}           # {user_id: CrashBet}
        self.multiplier  = 1.00
        self.crash_point = 1.00
        self.round       = 0
        self.message     = None         # Embed del canal de crash
        self.channel     = None


class CrashView(discord.ui.View):
    """Vista con botones Cashout (durante la partida)."""

    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="💰 Cashout", style=discord.ButtonStyle.success, custom_id="crash_cashout")
    async def cashout(self, interaction: discord.Interaction, button: discord.ui.Button):
        game = self.cog.game
        uid  = str(interaction.user.id)

        if game.state != CrashGame.RUNNING:
            await interaction.response.send_message("No hay ronda activa.", ephemeral=True)
            return

        bet = game.bets.get(uid)
        if not bet:
            await interaction.response.send_message("No tienes apuesta en esta ronda.", ephemeral=True)
            return

        if bet.cashed_out:
            await interaction.response.send_message(
                f"Ya cobraste a x{bet.cashout_mult:.2f}.", ephemeral=True
            )
            return

        bet.cashed_out   = True
        bet.cashout_mult = game.multiplier
        payout           = int(round(bet.amount * game.multiplier, 0))
        profit           = payout - bet.amount

        await self.cog.bot.db.add_balance(uid, payout)
        await self.cog.bot.db.log_game(uid, "crash", bet.amount, "win", profit)
        member = interaction.guild.get_member(interaction.user.id)
        if member:
            await update_wager_roles(self.cog.bot, interaction.guild, member)

        await interaction.response.send_message(
            embed=discord.Embed(
                description=f"✅ Cobrado a **x{game.multiplier:.2f}** — {fmt_gems(payout)}",
                color=COLOR_GOLD
            ),
            ephemeral=True
        )


class BettingView(discord.ui.View):
    """Vista de la betting window con botones Apostar."""

    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="🚀 Apostar", style=discord.ButtonStyle.primary, custom_id="crash_bet")
    async def place_bet(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BetModal(self.cog))

    @discord.ui.button(label="⚙️ Auto Cashout", style=discord.ButtonStyle.secondary, custom_id="crash_auto")
    async def auto_cashout(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AutoCashoutModal(self.cog))


class BetModal(discord.ui.Modal, title="🚀 Apostar en Crash"):
    amount_input = discord.ui.TextInput(
        label="Cantidad a apostar",
        placeholder="Ej: 1m, 500k, 250000000",
        required=True
    )

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        game   = self.cog.game
        uid    = str(interaction.user.id)

        if game.state != CrashGame.BETTING:
            await interaction.response.send_message("La betting window ha cerrado.", ephemeral=True)
            return

        amount = parse_amount(self.amount_input.value)
        if not amount or amount <= 0:
            await interaction.response.send_message(embed=error_embed("Cantidad inválida."), ephemeral=True)
            return

        # Check linked
        user_data = await self.cog.bot.db.get_user(uid)
        if not user_data or not user_data["roblox_name"]:
            await interaction.response.send_message(embed=error_embed("Usa /link primero."), ephemeral=True)
            return

        # Check balance
        bal = await self.cog.bot.db.get_balance(uid)
        if bal < amount:
            await interaction.response.send_message(
                embed=error_embed(f"Saldo insuficiente. Tienes {fmt_gems(bal)}"), ephemeral=True
            )
            return

        # Replace existing bet if already betting
        if uid in game.bets:
            old_amount = game.bets[uid].amount
            await self.cog.bot.db.add_balance(uid, old_amount)  # Refund old bet

        await self.cog.bot.db.remove_balance(uid, amount)
        await self.cog.bot.db.add_wager(uid, amount)
        await self.cog.bot.db.reduce_wager_requirement(uid, amount)

        auto = game.bets[uid].auto_cashout if uid in game.bets else None
        game.bets[uid] = CrashBet(interaction.user, amount, auto)

        await interaction.response.send_message(
            embed=discord.Embed(
                description=f"✅ Apuesta registrada: {fmt_gems(amount)}" + (f" · Auto cashout: x{auto:.2f}" if auto else ""),
                color=COLOR_INFO
            ),
            ephemeral=True
        )


class AutoCashoutModal(discord.ui.Modal, title="⚙️ Auto Cashout"):
    mult_input = discord.ui.TextInput(
        label="Multiplicador de auto cashout",
        placeholder="Ej: 2.0 (cobra automáticamente a x2.0)",
        required=True
    )
    amount_input = discord.ui.TextInput(
        label="Cantidad a apostar (opcional si ya apostaste)",
        placeholder="Ej: 1m, 500k — deja vacío para mantener",
        required=False
    )

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        game = self.cog.game
        uid  = str(interaction.user.id)

        if game.state != CrashGame.BETTING:
            await interaction.response.send_message("La betting window ha cerrado.", ephemeral=True)
            return

        try:
            auto_mult = float(self.mult_input.value.replace(",", "."))
            if auto_mult <= 1.0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(embed=error_embed("Multiplicador inválido (debe ser > 1.0)."), ephemeral=True)
            return

        # Check linked
        user_data = await self.cog.bot.db.get_user(uid)
        if not user_data or not user_data["roblox_name"]:
            await interaction.response.send_message(embed=error_embed("Usa /link primero."), ephemeral=True)
            return

        # Process amount if provided
        amount_str = self.amount_input.value.strip()
        if amount_str:
            amount = parse_amount(amount_str)
            if not amount or amount <= 0:
                await interaction.response.send_message(embed=error_embed("Cantidad inválida."), ephemeral=True)
                return
            bal = await self.cog.bot.db.get_balance(uid)
            if bal < amount:
                await interaction.response.send_message(
                    embed=error_embed(f"Saldo insuficiente. Tienes {fmt_gems(bal)}"), ephemeral=True
                )
                return
            if uid in game.bets:
                await self.cog.bot.db.add_balance(uid, game.bets[uid].amount)
            await self.cog.bot.db.remove_balance(uid, amount)
            await self.cog.bot.db.add_wager(uid, amount)
            await self.cog.bot.db.reduce_wager_requirement(uid, amount)
            game.bets[uid] = CrashBet(interaction.user, amount, auto_mult)
        elif uid in game.bets:
            game.bets[uid].auto_cashout = auto_mult
        else:
            await interaction.response.send_message(embed=error_embed("Primero haz una apuesta."), ephemeral=True)
            return

        bet_amount = game.bets[uid].amount
        await interaction.response.send_message(
            embed=discord.Embed(
                description=f"⚙️ Auto cashout activado a **x{auto_mult:.2f}** · Apuesta: {fmt_gems(bet_amount)}",
                color=COLOR_INFO
            ),
            ephemeral=True
        )


class Crash(commands.Cog):
    """Módulo del juego Crash."""

    def __init__(self, bot):
        self.bot    = bot
        self.game   = CrashGame()
        self._loop.start()

    def cog_unload(self):
        self._loop.cancel()

    def _build_betting_embed(self, seconds_left: int) -> discord.Embed:
        embed = discord.Embed(
            title="🚀 CRASH — Betting Window",
            description=f"**{seconds_left} segundos** para apostar\nPulsa 🚀 **Apostar** o configura ⚙️ **Auto Cashout**",
            color=COLOR_INFO
        )
        if self.game.bets:
            players_text = ""
            for uid, bet in list(self.game.bets.items())[:15]:
                auto = f" (auto x{bet.auto_cashout:.2f})" if bet.auto_cashout else ""
                players_text += f"{bet.user.mention} — {fmt_gems(bet.amount)}{auto}\n"
            embed.add_field(name=f"👥 Jugadores ({len(self.game.bets)})", value=players_text, inline=False)
        embed.set_footer(text=f"Ronda #{self.game.round}")
        return embed

    def _build_running_embed(self) -> discord.Embed:
        mult  = self.game.multiplier
        color = COLOR_GOLD if mult < 2 else (0x00FF00 if mult < 5 else 0xFF6600)

        # Rocket progress bar
        prog = min(int((math.log(mult) / math.log(10)) * 20), 20)
        bar  = "🚀" + "═" * prog + "💫"

        embed = discord.Embed(
            title=f"🚀 CRASH — x{mult:.2f}",
            description=bar,
            color=color
        )

        # Show active bets
        if self.game.bets:
            active, cashed = [], []
            for uid, bet in self.game.bets.items():
                if bet.cashed_out:
                    payout = int(round(bet.amount * bet.cashout_mult, 0))
                    cashed.append(f"✅ {bet.user.display_name} cobró x{bet.cashout_mult:.2f} → {fmt_gems(payout)}")
                else:
                    pot = int(round(bet.amount * mult, 0))
                    auto = f" ⚙️x{bet.auto_cashout:.2f}" if bet.auto_cashout else ""
                    active.append(f"🎯 {bet.user.display_name} {fmt_gems(bet.amount)} → {fmt_gems(pot)}{auto}")

            if active:
                embed.add_field(name="En juego", value="\n".join(active[:10]), inline=False)
            if cashed:
                embed.add_field(name="Cobrado", value="\n".join(cashed[:10]), inline=False)

        embed.set_footer(text=f"Ronda #{self.game.round} · Pulsa 💰 Cashout para cobrar")
        return embed

    def _build_crash_embed(self) -> discord.Embed:
        cp = self.game.crash_point
        embed = discord.Embed(
            title=f"💥 CRASH — x{cp:.2f}",
            description="El cohete ha explotado",
            color=COLOR_ERROR
        )
        winners, losers = [], []
        for uid, bet in self.game.bets.items():
            if bet.cashed_out:
                payout = int(round(bet.amount * bet.cashout_mult, 0))
                profit = payout - bet.amount
                winners.append(f"✅ {bet.user.display_name} x{bet.cashout_mult:.2f} +{fmt_gems(profit)}")
            else:
                losers.append(f"💥 {bet.user.display_name} -{fmt_gems(bet.amount)}")
        if winners:
            embed.add_field(name="Ganadores", value="\n".join(winners[:10]), inline=False)
        if losers:
            embed.add_field(name="Perdedores", value="\n".join(losers[:10]), inline=False)
        embed.set_footer(text=f"Ronda #{self.game.round} · Próxima ronda en 10s")
        return embed

    @tasks.loop(seconds=1)
    async def _loop(self):
        """Loop principal del juego Crash."""
        try:
            await self._run_cycle()
        except Exception as e:
            print(f"[Crash] Loop error: {e}")

    @_loop.before_loop
    async def before_loop(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(3)

    async def _run_cycle(self):
        """Un ciclo completo: betting → running → crashed → wait."""
        self._loop.stop()   # Pause loop — we control timing manually
        try:
            # Get crash channel
            ch_id   = await self.bot.db.get_config("crash_channel")
            if not ch_id:
                await asyncio.sleep(5)
                return
            channel = None
            for guild in self.bot.guilds:
                channel = guild.get_channel(int(ch_id))
                if channel:
                    break
            if not channel:
                await asyncio.sleep(5)
                return

            self.game.channel = channel

            # ── BETTING WINDOW ────────────────────────────────
            self.game.state       = CrashGame.BETTING
            self.game.bets        = {}
            self.game.multiplier  = 1.00
            self.game.crash_point = generate_crash_point()
            self.game.round      += 1

            bet_view = BettingView(self)
            msg      = await channel.send(embed=self._build_betting_embed(BETTING_WINDOW), view=bet_view)
            self.game.message = msg

            for t in range(BETTING_WINDOW, 0, -1):
                await asyncio.sleep(1)
                try:
                    await msg.edit(embed=self._build_betting_embed(t))
                except Exception:
                    pass

            # No bets — skip round
            if not self.game.bets:
                await msg.delete()
                return

            # ── RUNNING ───────────────────────────────────────
            self.game.state = CrashGame.RUNNING
            crash_view      = CrashView(self)
            self.game.multiplier = 1.00

            await msg.edit(embed=self._build_running_embed(), view=crash_view)

            # Tick up multiplier until crash
            while self.game.multiplier < self.game.crash_point:
                await asyncio.sleep(TICK_INTERVAL)
                # Multiplier grows: slow at first, faster later
                self.game.multiplier = round(self.game.multiplier * 1.06, 2)
                if self.game.multiplier > self.game.crash_point:
                    self.game.multiplier = self.game.crash_point

                # Auto cashouts
                for uid, bet in self.game.bets.items():
                    if not bet.cashed_out and bet.auto_cashout and self.game.multiplier >= bet.auto_cashout:
                        bet.cashed_out   = True
                        bet.cashout_mult = self.game.multiplier
                        payout = int(round(bet.amount * self.game.multiplier, 0))
                        profit = payout - bet.amount
                        await self.bot.db.add_balance(uid, payout)
                        await self.bot.db.log_game(uid, "crash", bet.amount, "win", profit)

                try:
                    await msg.edit(embed=self._build_running_embed())
                except Exception:
                    pass

            # ── CRASHED ───────────────────────────────────────
            self.game.state = CrashGame.CRASHED

            # Process losers
            for uid, bet in self.game.bets.items():
                if not bet.cashed_out:
                    # House edge rakeback
                    house_p = int(bet.amount * HOUSE_EDGE)
                    rb_pct  = float(await self.bot.db.get_config("rakeback_pct") or "20")
                    rb_amt  = int(house_p * rb_pct / 100)
                    if rb_amt > 0:
                        await self.bot.db.add_rakeback(uid, rb_amt)
                    await self.bot.db.log_game(uid, "crash", bet.amount, "lose", -bet.amount)

            await msg.edit(embed=self._build_crash_embed(), view=None)
            await asyncio.sleep(10)     # Wait before next round
            await msg.delete()

        except Exception as e:
            print(f"[Crash] Cycle error: {e}")
        finally:
            self._loop.restart()

    @app_commands.command(name="crash_status", description="Ver el estado actual del juego Crash")
    async def crash_status(self, interaction: discord.Interaction):
        game = self.game
        if game.state == CrashGame.IDLE:
            await interaction.response.send_message("El juego Crash no está activo.", ephemeral=True)
        elif game.state == CrashGame.BETTING:
            ch_id = await self.bot.db.get_config("crash_channel")
            link  = f"https://discord.com/channels/{interaction.guild_id}/{ch_id}" if ch_id else "no configurado"
            await interaction.response.send_message(
                embed=discord.Embed(description=f"🎲 Betting window activa → [ir al canal]({link})", color=COLOR_INFO),
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                embed=discord.Embed(description=f"🚀 Crash en curso — x{game.multiplier:.2f}", color=COLOR_GOLD),
                ephemeral=True
            )


async def setup(bot):
    await bot.add_cog(Crash(bot))
