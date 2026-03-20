# ============================================================
# cogs/games/blackjack.py — Juego de Blackjack (corregido)
# ============================================================
# FIXES:
#  - active_games se limpia correctamente al terminar la partida
#  - Delay de 1s al pedir carta (Hit) para efecto visual
#  - El cog pasa su referencia al juego para poder limpiar
# ============================================================

import discord
from discord.ext import commands
from discord import app_commands
import random
import secrets
_rng = random.SystemRandom()  # Cryptographically secure RNG
import asyncio                                  # Para el delay entre cartas
from utils import (
    apply_rakeback,
    check_linked, check_balance, fmt_gems,
    error_embed, update_wager_roles,
    COLOR_GOLD, COLOR_ERROR, COLOR_INFO
)

# ── Baraja ────────────────────────────────────────────────────
SUITS  = ["♠️", "♥️", "♦️", "♣️"]
RANKS  = ["2","3","4","5","6","7","8","9","10","J","Q","K","A"]
CARD_VALUES = {
    "2":2,"3":3,"4":4,"5":5,"6":6,"7":7,"8":8,"9":9,
    "10":10,"J":10,"Q":10,"K":10,"A":11
}

def new_deck():
    """Crea una baraja mezclada."""
    deck = [(r, s) for s in SUITS for r in RANKS]
    _rng.shuffle(deck)
    return deck

def draw_card(deck):
    """Saca la primera carta del mazo."""
    return deck.pop(0)

def hand_value(hand):
    """Calcula el valor de la mano. Ases bajan de 11 a 1 si se pasa de 21."""
    total, aces = 0, 0
    for rank, _ in hand:
        total += CARD_VALUES[rank]
        if rank == "A":
            aces += 1
    while total > 21 and aces > 0:
        total -= 10
        aces  -= 1
    return total

def fmt_hand(hand, hide_first=False):
    """Formatea la mano para mostrar en Discord."""
    cards = []
    for i, (rank, suit) in enumerate(hand):
        cards.append("🂠" if (hide_first and i == 0) else f"{rank}{suit}")
    return "  ".join(cards)

def is_blackjack(hand):
    """Comprueba si es Blackjack natural (21 con 2 cartas)."""
    return len(hand) == 2 and hand_value(hand) == 21


# ── Vista con botones Hit/Stand ───────────────────────────────
class BlackjackView(discord.ui.View):

    def __init__(self, game):
        super().__init__(timeout=120)
        self.game = game

    async def on_timeout(self):
        """Tiempo agotado: limpia la partida y desactiva botones."""
        # Elimina del diccionario de activas — CLAVE del fix
        self.game.cog.active_games.pop(self.game.player_id, None)
        for item in self.children:
            item.disabled = True
        try:
            await self.game.message.edit(view=self)
        except Exception:
            pass

    @discord.ui.button(label="🃏 Hit", style=discord.ButtonStyle.primary)
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        """El jugador pide carta."""
        if interaction.user.id != self.game.player_id:
            await interaction.response.send_message("Esta no es tu partida.", ephemeral=True)
            return

        # Desactiva botones y muestra "repartiendo..."
        for item in self.children:
            item.disabled = True
        loading = self.game.build_embed()
        loading.set_footer(text="🃏 Repartiendo carta...")
        await interaction.response.edit_message(embed=loading, view=self)

        # Delay de 1 segundo — efecto visual de reparto
        await asyncio.sleep(1)

        # Reparte la carta
        self.game.player_hand.append(draw_card(self.game.deck))
        value = hand_value(self.game.player_hand)

        if value > 21:
            await self.game.end_game(interaction, "bust", via_edit=True)
        elif value == 21:
            # Llega a 21, se planta automáticamente
            await self.game.do_stand(interaction, via_edit=True)
        else:
            # Reactiva botones y actualiza
            for item in self.children:
                item.disabled = False
            new_view = BlackjackView(self.game)
            await interaction.edit_original_response(
                embed=self.game.build_embed(), view=new_view
            )

    @discord.ui.button(label="🛑 Stand", style=discord.ButtonStyle.secondary)
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        """El jugador se planta."""
        if interaction.user.id != self.game.player_id:
            await interaction.response.send_message("Esta no es tu partida.", ephemeral=True)
            return
        await self.game.do_stand(interaction)


# ── Clase de partida ──────────────────────────────────────────
class BlackjackGame:

    def __init__(self, bot, cog, player, bet):
        self.bot       = bot
        self.cog       = cog                    # Referencia al cog para limpiar active_games
        self.player    = player
        self.player_id = player.id
        self.bet       = bet
        self.deck      = new_deck()
        self.message   = None

        # Reparte 2 cartas a cada uno
        self.player_hand = [draw_card(self.deck), draw_card(self.deck)]
        self.dealer_hand = [draw_card(self.deck), draw_card(self.deck)]

    def build_embed(self, show_dealer=False, result_text=None):
        """Construye el embed del estado actual."""
        pv = hand_value(self.player_hand)
        dv = hand_value(self.dealer_hand)

        if result_text:
            color = COLOR_GOLD if any(w in result_text for w in ["✅","🃏"]) else \
                    (COLOR_ERROR if "❌" in result_text else 0x95a5a6)
        else:
            color = COLOR_INFO

        embed = discord.Embed(
            title=f"🃏 Blackjack — {self.player.display_name}",
            color=color
        )
        embed.add_field(name=f"Tu mano ({pv})",
                        value=fmt_hand(self.player_hand), inline=False)
        embed.add_field(name=f"Banca ({'?' if not show_dealer else dv})",
                        value=fmt_hand(self.dealer_hand, hide_first=not show_dealer),
                        inline=False)
        embed.add_field(name="Apuesta", value=fmt_gems(self.bet), inline=True)
        if result_text:
            embed.add_field(name="Resultado", value=result_text, inline=False)
        return embed

    async def do_stand(self, interaction, via_edit=False):
        """La banca juega hasta 17+ y se determina el ganador."""
        while hand_value(self.dealer_hand) < 17:
            self.dealer_hand.append(draw_card(self.deck))

        pv = hand_value(self.player_hand)
        dv = hand_value(self.dealer_hand)

        if dv > 21 or pv > dv:
            result = "win"
        elif pv < dv:
            result = "lose"
        else:
            result = "tie"

        await self.end_game(interaction, result, via_edit=via_edit)

    async def end_game(self, interaction, result, via_edit=False):
        """
        Termina la partida y actualiza balance.
        SIEMPRE limpia active_games al llamarse.
        """
        db      = self.bot.db
        user_id = str(self.player_id)

        # ── LIMPIA active_games — fix principal ───────────────
        self.cog.active_games.pop(self.player_id, None)

        # ── Calcula pago ──────────────────────────────────────
        if result == "blackjack":
            payout    = int(self.bet * 1.5)
            await db.add_balance(user_id, self.bet + payout)
            profit    = payout
            res_text  = f"🃏 ¡BLACKJACK! Ganaste {fmt_gems(payout)}"
            db_result = "win"
        elif result == "win":
            await db.add_balance(user_id, self.bet * 2)
            profit    = self.bet
            res_text  = f"✅ ¡Ganaste {fmt_gems(self.bet)}!"
            db_result = "win"
        elif result == "tie":
            await db.add_balance(user_id, self.bet)
            profit    = 0
            res_text  = "🤝 Empate — se devuelve tu apuesta"
            db_result = "tie"
        else:
            profit    = -self.bet
            res_text  = f"❌ Perdiste {fmt_gems(self.bet)}"
            db_result = "lose"
            # Rakeback: % del beneficio de la casa (edge%), no del total apostado
            edge_pct_bj  = await db.get_house_edge("blackjack")
            house_profit = int(self.bet * edge_pct_bj / 100)
            rakeback_pct = float(await db.get_config("rakeback_pct") or "20")
            rakeback_amt = int(house_profit * rakeback_pct / 100)
            if rakeback_amt > 0:
                await db.add_rakeback(user_id, rakeback_amt)

        await db.log_game(user_id, "blackjack", self.bet, db_result, profit)

        member = interaction.guild.get_member(self.player_id)
        if member:
            await update_wager_roles(self.bot, interaction.guild, member)

        new_bal = await db.get_balance(user_id)
        embed   = self.build_embed(show_dealer=True, result_text=res_text)
        embed.set_footer(text=f"Saldo actual: {fmt_gems(new_bal)}")

        # Botones desactivados
        view = BlackjackView(self)
        for item in view.children:
            item.disabled = True

        # Edita el mensaje según cómo fue llamado
        if via_edit:
            await interaction.edit_original_response(embed=embed, view=view)
        else:
            await interaction.response.edit_message(embed=embed, view=view)


# ── COG ───────────────────────────────────────────────────────
class Blackjack(commands.Cog):

    def __init__(self, bot):
        self.bot          = bot
        self.active_games = {}              # {player_id (int): BlackjackGame}

    @app_commands.command(name="blackjack", description="Juega una partida de Blackjack")
    @app_commands.describe(apuesta="Cantidad de gemas a apostar")
    async def blackjack(self, interaction: discord.Interaction, apuesta: int):
        if not await check_linked(interaction):
            return

        if apuesta <= 0:
            await interaction.response.send_message(
                embed=error_embed("La apuesta debe ser mayor a 0."), ephemeral=True
            )
            return

        # Comprueba partida activa
        if interaction.user.id in self.active_games:
            await interaction.response.send_message(
                embed=error_embed("Ya tienes una partida activa. Termínala primero."),
                ephemeral=True
            )
            return

        if not await check_balance(interaction, apuesta):
            return

        user_id = str(interaction.user.id)
        await self.bot.db.remove_balance(user_id, apuesta)
        await self.bot.db.add_wager(user_id, apuesta)

        # Crea la partida pasando self (cog) para poder limpiar active_games
        game = BlackjackGame(self.bot, self, interaction.user, apuesta)
        self.active_games[interaction.user.id] = game

        # Blackjack natural al inicio
        if is_blackjack(game.player_hand):
            self.active_games.pop(interaction.user.id, None)    # Limpia antes
            embed = game.build_embed(show_dealer=False)
            await interaction.response.send_message(embed=embed)
            game.message = await interaction.original_response()
            await asyncio.sleep(0.8)
            await game.end_game(interaction, "blackjack", via_edit=True)
            return

        # Envía el embed inicial
        view  = BlackjackView(game)
        embed = game.build_embed()
        await interaction.response.send_message(embed=embed, view=view)
        game.message = await interaction.original_response()


async def setup(bot):
    await bot.add_cog(Blackjack(bot))
