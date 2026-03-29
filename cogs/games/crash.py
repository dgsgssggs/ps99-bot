# ============================================================
# cogs/games/crash.py — Juego del Cohete (Crash)
# ============================================================
# Multiplicador: sube de 1.01 → 1.02 → 1.03... (+0.01 por tick)
# Tick: cada 0.5s = +0.01 → sube 0.02x por segundo
# Crash: 2s de pausa → nueva ronda automática
# House edge 5%: P(crash > x) ≈ 0.95/x
# Canal específico: configurar con /setchannel tipo:crash
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

BETTING_WINDOW = 10      # segundos de betting window
TICK_INTERVAL  = 0.5     # segundos entre ticks de multiplicador
MULT_INCREMENT = 0.01    # incremento por tick (1.01 → 1.02 → ...)
MIN_CRASH      = 1.01    # mínimo crash posible
HOUSE_EDGE     = 0.05    # 5%
CRASH_PAUSE    = 2       # segundos entre crash y nueva ronda


def generate_crash_point() -> float:
    """
    Genera el punto de crash con 5% house edge.
    Formula: 0.95 / (1 - r)  donde r ∈ [0, 1)
    P(crash > x) = 0.95/x  para x ≥ 1
    ~5% del tiempo crashea en el mínimo (house edge instantáneo).
    """
    r = _rng.random()
    if r >= 0.95:
        return MIN_CRASH
    crash = 0.95 / (1.0 - r)
    # Redondea al 0.01 más cercano
    return round(max(MIN_CRASH, crash), 2)


class CrashBet:
    def __init__(self, user: discord.Member, amount: int, auto_cashout: float | None):
        self.user         = user
        self.amount       = amount
        self.auto_cashout = auto_cashout
        self.cashed_out   = False
        self.cashout_mult = None


class CrashGame:
    IDLE    = "idle"
    BETTING = "betting"
    RUNNING = "running"
    CRASHED = "crashed"

    def __init__(self):
        self.state       = self.IDLE
        self.bets        = {}
        self.multiplier  = 1.00
        self.crash_point = 1.00
        self.message     = None
        self.channel     = None


class CrashView(discord.ui.View):
    """Vista activa durante la subida — botón Cashout."""

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
        payout = int(round(bet.amount * game.multiplier, 0))
        profit = payout - bet.amount

        await self.cog.bot.db.add_balance(uid, payout)
        await self.cog.bot.db.log_game(uid, "crash", bet.amount, "win", profit)
        member = interaction.guild.get_member(interaction.user.id)
        if member:
            await update_wager_roles(self.cog.bot, interaction.guild, member)

        await interaction.response.send_message(
            embed=discord.Embed(
                description=f"✅ Cobrado a **x{game.multiplier:.2f}** — {fmt_gems(payout)} (+{fmt_gems(profit)})",
                color=COLOR_GOLD
            ),
            ephemeral=True
        )


class BettingView(discord.ui.View):
    """Vista de betting window."""

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
        placeholder="Ej: 1m, 500k, 2.5b",
        required=True
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

        amount = parse_amount(self.amount_input.value)
        if not amount or amount <= 0:
            await interaction.response.send_message(embed=error_embed("Cantidad inválida."), ephemeral=True)
            return

        user_data = await self.cog.bot.db.get_user(uid)
        if not user_data or not user_data["roblox_name"]:
            await interaction.response.send_message(embed=error_embed("Usa /link primero."), ephemeral=True)
            return

        bal = await self.cog.bot.db.get_balance(uid)
        if bal < amount:
            await interaction.response.send_message(
                embed=error_embed(f"Saldo insuficiente. Tienes {fmt_gems(bal)}"), ephemeral=True
            )
            return

        # Reembolsa apuesta anterior si la hay
        if uid in game.bets:
            await self.cog.bot.db.add_balance(uid, game.bets[uid].amount)

        await self.cog.bot.db.remove_balance(uid, amount)
        await self.cog.bot.db.add_wager(uid, amount)
        await self.cog.bot.db.reduce_wager_requirement(uid, amount)

        auto = game.bets[uid].auto_cashout if uid in game.bets else None
        game.bets[uid] = CrashBet(interaction.user, amount, auto)

        msg = f"✅ Apuesta: {fmt_gems(amount)}"
        if auto:
            msg += f" · Auto cashout: **x{auto:.2f}**"
        await interaction.response.send_message(
            embed=discord.Embed(description=msg, color=COLOR_INFO), ephemeral=True
        )


class AutoCashoutModal(discord.ui.Modal, title="⚙️ Auto Cashout"):
    mult_input = discord.ui.TextInput(
        label="Multiplicador de auto cashout",
        placeholder="Ej: 2.0 — cobra automáticamente a x2.0",
        required=True
    )
    amount_input = discord.ui.TextInput(
        label="Cantidad (opcional si ya apostaste)",
        placeholder="Ej: 1m — deja vacío para mantener la apuesta",
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
            await interaction.response.send_message(
                embed=error_embed("Multiplicador inválido (debe ser > 1.0)."), ephemeral=True
            )
            return

        user_data = await self.cog.bot.db.get_user(uid)
        if not user_data or not user_data["roblox_name"]:
            await interaction.response.send_message(embed=error_embed("Usa /link primero."), ephemeral=True)
            return

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
            await interaction.response.send_message(
                embed=error_embed("Primero haz una apuesta con 🚀 Apostar."), ephemeral=True
            )
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

    def __init__(self, bot):
        self.bot  = bot
        self.game = CrashGame()
        self._loop.start()

    def cog_unload(self):
        self._loop.cancel()

    # ── Embeds ────────────────────────────────────────────────

    def _build_betting_embed(self, seconds_left: int) -> discord.Embed:
        embed = discord.Embed(
            title="🚀 CRASH — Betting Window",
            description=(
                f"**{seconds_left}s** para apostar\n"
                f"Pulsa 🚀 **Apostar** o ⚙️ **Auto Cashout**"
            ),
            color=COLOR_INFO
        )
        if self.game.bets:
            lines = []
            for uid, bet in list(self.game.bets.items())[:15]:
                auto = f" ⚙️ x{bet.auto_cashout:.2f}" if bet.auto_cashout else ""
                lines.append(f"{bet.user.mention} — {fmt_gems(bet.amount)}{auto}")
            embed.add_field(
                name=f"👥 Jugadores ({len(self.game.bets)})",
                value="\n".join(lines),
                inline=False
            )
        return embed

    def _build_running_embed(self) -> discord.Embed:
        mult  = self.game.multiplier
        # Color: amarillo < 2x, verde 2-5x, naranja > 5x
        if mult < 2.0:
            color = COLOR_GOLD
        elif mult < 5.0:
            color = 0x00CC44
        else:
            color = 0xFF6600

        # Barra de progreso (log scale, máx visual ~10x)
        prog = min(int((math.log10(max(mult, 1.01))) * 10), 20)
        bar  = "🚀" + "═" * prog + "💫"

        embed = discord.Embed(
            title=f"🚀 x{mult:.2f}",
            description=bar,
            color=color
        )

        active, cashed = [], []
        for uid, bet in self.game.bets.items():
            if bet.cashed_out:
                payout = int(round(bet.amount * bet.cashout_mult, 0))
                cashed.append(f"✅ **{bet.user.display_name}** x{bet.cashout_mult:.2f} → {fmt_gems(payout)}")
            else:
                pot  = int(round(bet.amount * mult, 0))
                auto = f" ⚙️x{bet.auto_cashout:.2f}" if bet.auto_cashout else ""
                active.append(f"🎯 **{bet.user.display_name}** {fmt_gems(bet.amount)} → {fmt_gems(pot)}{auto}")

        if active:
            embed.add_field(name="En juego", value="\n".join(active[:10]), inline=False)
        if cashed:
            embed.add_field(name="Cobrado", value="\n".join(cashed[:10]), inline=False)

        embed.set_footer(text="Pulsa 💰 Cashout para cobrar")
        return embed

    def _build_crash_embed(self) -> discord.Embed:
        cp = self.game.crash_point
        embed = discord.Embed(
            title=f"💥 CRASH a x{cp:.2f}",
            description="El cohete ha explotado · Nueva ronda en 2s",
            color=COLOR_ERROR
        )
        winners, losers = [], []
        for uid, bet in self.game.bets.items():
            if bet.cashed_out:
                payout = int(round(bet.amount * bet.cashout_mult, 0))
                profit = payout - bet.amount
                winners.append(f"✅ **{bet.user.display_name}** x{bet.cashout_mult:.2f} +{fmt_gems(profit)}")
            else:
                losers.append(f"💥 **{bet.user.display_name}** -{fmt_gems(bet.amount)}")
        if winners:
            embed.add_field(name="Ganadores", value="\n".join(winners[:10]), inline=False)
        if losers:
            embed.add_field(name="Perdedores", value="\n".join(losers[:10]), inline=False)
        return embed

    # ── Loop principal ────────────────────────────────────────

    @tasks.loop(seconds=1)
    async def _loop(self):
        try:
            await self._run_cycle()
        except Exception as e:
            print(f"[Crash] Loop error: {e}")

    @_loop.before_loop
    async def before_loop(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(3)

    async def _run_cycle(self):
        self._loop.stop()
        try:
            # Obtener canal de crash
            ch_id = await self.bot.db.get_config("crash_channel")
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

            bet_view = BettingView(self)
            msg = await channel.send(
                embed=self._build_betting_embed(BETTING_WINDOW),
                view=bet_view
            )
            self.game.message = msg

            for t in range(BETTING_WINDOW, 0, -1):
                await asyncio.sleep(1)
                try:
                    await msg.edit(embed=self._build_betting_embed(t))
                except Exception:
                    pass

            # Sin apuestas → skip silencioso
            if not self.game.bets:
                try:
                    await msg.delete()
                except Exception:
                    pass
                return

            # ── RUNNING ───────────────────────────────────────
            self.game.state      = CrashGame.RUNNING
            self.game.multiplier = 1.00
            crash_view           = CrashView(self)

            await msg.edit(embed=self._build_running_embed(), view=crash_view)

            # Sube 0.01 cada TICK_INTERVAL segundos
            while self.game.multiplier < self.game.crash_point:
                await asyncio.sleep(TICK_INTERVAL)

                self.game.multiplier = round(self.game.multiplier + MULT_INCREMENT, 2)
                if self.game.multiplier > self.game.crash_point:
                    self.game.multiplier = self.game.crash_point

                # Auto cashouts
                for uid, bet in list(self.game.bets.items()):
                    if (not bet.cashed_out
                            and bet.auto_cashout
                            and self.game.multiplier >= bet.auto_cashout):
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

            for uid, bet in self.game.bets.items():
                if not bet.cashed_out:
                    house_p = int(bet.amount * HOUSE_EDGE)
                    rb_pct  = float(await self.bot.db.get_config("rakeback_pct") or "20")
                    rb_amt  = int(house_p * rb_pct / 100)
                    if rb_amt > 0:
                        await self.bot.db.add_rakeback(uid, rb_amt)
                    await self.bot.db.log_game(uid, "crash", bet.amount, "lose", -bet.amount)

            await msg.edit(embed=self._build_crash_embed(), view=None)
            await asyncio.sleep(CRASH_PAUSE)   # 2 segundos y nueva ronda
            try:
                await msg.delete()
            except Exception:
                pass

        except Exception as e:
            print(f"[Crash] Cycle error: {e}")
        finally:
            self._loop.restart()

    # ── Comando de estado ─────────────────────────────────────

    @app_commands.command(name="crash_status", description="Ver el estado actual del juego Crash")
    async def crash_status(self, interaction: discord.Interaction):
        game = self.game
        if game.state == CrashGame.IDLE:
            await interaction.response.send_message(
                embed=discord.Embed(description="El canal de Crash no está configurado.", color=COLOR_ERROR),
                ephemeral=True
            )
        elif game.state == CrashGame.BETTING:
            ch_id = await self.bot.db.get_config("crash_channel")
            link  = f"https://discord.com/channels/{interaction.guild_id}/{ch_id}" if ch_id else "no configurado"
            await interaction.response.send_message(
                embed=discord.Embed(
                    description=f"🎲 Betting window activa → [ir al canal]({link})",
                    color=COLOR_INFO
                ),
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description=f"🚀 Crash en curso — **x{game.multiplier:.2f}**",
                    color=COLOR_GOLD
                ),
                ephemeral=True
            )


async def setup(bot):
    await bot.add_cog(Crash(bot))
