# ============================================================
# utils.py — Funciones de utilidad compartidas
# ============================================================
# NUEVO: is_owner y is_admin leen OWNER_IDS y ADMIN_IDS
# desde variables de entorno (Railway) en vez de un solo ID.
# Formato en Railway: OWNER_IDS=123456,789012
# ============================================================

import discord
import os
from dotenv import load_dotenv

load_dotenv()

# ── Colores para embeds ───────────────────────────────────────
COLOR_SUCCESS = 0x2ECC71
COLOR_ERROR   = 0xE74C3C
COLOR_INFO    = 0x3498DB
COLOR_GOLD    = 0xF1C40F
COLOR_ORANGE  = 0xE67E22
COLOR_PURPLE  = 0x9B59B6

# ── Helpers de permisos ───────────────────────────────────────
def _get_ids(env_key: str) -> set:
    """
    Lee una variable de entorno con IDs separados por comas.
    Ejemplo: OWNER_IDS=123,456,789 → {'123', '456', '789'}
    También soporta el antiguo OWNER_ID (singular) para compatibilidad.
    """
    raw = os.getenv(env_key, "")
    ids = {x.strip() for x in raw.split(",") if x.strip()}

    # Compatibilidad con OWNER_ID singular
    if env_key == "OWNER_IDS":
        single = os.getenv("OWNER_ID", "").strip()
        if single:
            ids.add(single)

    return ids

def is_owner(user_id: int) -> bool:
    """
    Retorna True si el usuario está en la lista OWNER_IDS de Railway.
    Tiene todos los permisos del bot.
    """
    return str(user_id) in _get_ids("OWNER_IDS")

def is_admin(user_id: int) -> bool:
    """
    Retorna True si el usuario está en ADMIN_IDS o OWNER_IDS.
    Los admins pueden crear códigos y gestionar ciertas funciones.
    """
    return str(user_id) in _get_ids("ADMIN_IDS") or is_owner(user_id)

def get_owner_ids() -> set:
    """Retorna el set completo de IDs de owners."""
    return _get_ids("OWNER_IDS")

def get_admin_ids() -> set:
    """Retorna el set completo de IDs de admins + owners."""
    return _get_ids("ADMIN_IDS") | _get_ids("OWNER_IDS")

# ── Formateadores ─────────────────────────────────────────────
def fmt(number: int) -> str:
    """Formats with K/M/B abbreviations. 1500→1.5K, 1000000→1M, 2500000000→2.5B"""
    abs_n = abs(number)
    sign  = "-" if number < 0 else ""
    if abs_n >= 1_000_000_000:
        val = abs_n / 1_000_000_000
        s = f"{val:.1f}".rstrip('0').rstrip('.')
        return f"{sign}{s}B"
    elif abs_n >= 1_000_000:
        val = abs_n / 1_000_000
        s = f"{val:.1f}".rstrip('0').rstrip('.')
        return f"{sign}{s}M"
    elif abs_n >= 1_000:
        val = abs_n / 1_000
        s = f"{val:.1f}".rstrip('0').rstrip('.')
        return f"{sign}{s}K"
    return f"{sign}{abs_n:,}"

def fmt_gems(amount: int) -> str:
    """💎 1.5K, 💎 10M, 💎 2.5B"""
    return f"💎 {fmt(amount)}"

def fmt_multi(multiplier: float) -> str:
    """Formats multiplier with exactly 2 decimal places: 1.50x, 2.34x"""
    return f"{multiplier:.2f}x"

def exact_payout(bet: int, multiplier: float) -> int:
    """
    Calculates exact integer payout with no floating point errors.
    Uses round() then int() to avoid truncation issues.
    """
    return int(round(bet * multiplier, 0))

def parse_amount(value: str) -> int | None:
    """
    Parsea una cantidad con K/M/B como sufijo.
    Ejemplos: "1k" → 1000, "1.5m" → 1500000, "2b" → 2000000000
    También acepta números normales: "1000" → 1000
    Retorna None si el formato es inválido.
    """
    if not value:
        return None
    value = value.strip().lower().replace(",", "").replace("_", "")
    multipliers = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}
    for suffix, mult in multipliers.items():
        if value.endswith(suffix):
            try:
                num = float(value[:-1])
                return int(num * mult)
            except ValueError:
                return None
    try:
        return int(float(value))
    except ValueError:
        return None

# ── Embeds estándar ───────────────────────────────────────────
def error_embed(message: str) -> discord.Embed:
    """Embed rojo de error."""
    return discord.Embed(description=f"❌ {message}", color=COLOR_ERROR)

def success_embed(title: str, message: str) -> discord.Embed:
    """Embed verde de éxito."""
    return discord.Embed(title=f"✅ {title}", description=message, color=COLOR_SUCCESS)

# ── Checks de usuario ─────────────────────────────────────────
async def check_linked(interaction: discord.Interaction) -> bool:
    """Comprueba que el usuario tiene Roblox vinculado."""
    user = await interaction.client.db.get_user(str(interaction.user.id))
    if not user or not user["roblox_name"]:
        await interaction.response.send_message(
            embed=error_embed("Debes vincular tu cuenta de Roblox primero.\nUsa `/link <usuario_roblox>`"),
            ephemeral=True
        )
        return False
    return True

async def check_balance(interaction: discord.Interaction, amount: int) -> bool:
    """Comprueba que el usuario tiene saldo suficiente."""
    balance = await interaction.client.db.get_balance(str(interaction.user.id))
    if balance < amount:
        await interaction.response.send_message(
            embed=error_embed(
                f"No tienes suficientes gemas.\n"
                f"Tu saldo: {fmt_gems(balance)}\n"
                f"Necesitas: {fmt_gems(amount)}"
            ),
            ephemeral=True
        )
        return False
    return True

# ── Roles de wager ────────────────────────────────────────────
async def update_wager_roles(bot, guild: discord.Guild, member: discord.Member):
    """Asigna/quita roles automáticamente según el total apostado."""
    total       = await bot.db.get_user_total_wagered(str(member.id))
    wager_roles = await bot.db.get_wager_roles()

    for role_data in reversed(wager_roles):
        threshold = role_data["threshold"]
        role_id   = role_data["role_id"]
        role      = guild.get_role(int(role_id))
        if not role:
            continue
        if total >= threshold:
            if role not in member.roles:
                try:
                    await member.add_roles(role)
                except discord.Forbidden:
                    pass
        else:
            if role in member.roles:
                try:
                    await member.remove_roles(role)
                except discord.Forbidden:
                    pass

# ── Rakeback ──────────────────────────────────────────────────
async def apply_rakeback(bot, discord_id: str, loss_amount: int):
    """Acumula rakeback tras una pérdida."""
    if loss_amount <= 0:
        return
    pct_str = await bot.db.get_config("rakeback_pct")
    pct     = float(pct_str) if pct_str else 20.0
    if pct <= 0:
        return
    amount = int(loss_amount * (pct / 100))
    if amount > 0:
        await bot.db.add_rakeback(discord_id, amount)


async def reduce_wager_req(bot, discord_id: str, amount: int):
    """
    Reduce el wager requirement del usuario tras apostar.
    Se llama desde cada juego con la cantidad apostada.
    """
    await bot.db.reduce_wager_requirement(discord_id, amount)
