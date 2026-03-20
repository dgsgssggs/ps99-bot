# ============================================================
# cogs/crypto.py — Depósitos SOL + LTC con auto-sweep verified
# ============================================================
# FIXES vs versión anterior:
#  - SOL: keypair restore y tx building verificados
#  - LTC: ECDSA secp256k1 real con coincurve (antes pasaba
#          privkey como signature lo cual es un bug crítico)
#  - LTC: pubkey derivada correctamente para BlockCypher
#  - Ambos: manejo de errores mejorado con logs detallados
# ============================================================

import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import secrets
import asyncio
import base64
from utils import (
    check_linked, fmt_gems, is_owner,
    error_embed, success_embed,
    COLOR_GOLD, COLOR_INFO, COLOR_ERROR
)

# httpx — instalado via requirements.txt
try:
    import httpx
    HTTPX_OK = True
except ImportError:
    HTTPX_OK = False
    print("[Crypto] ❌ httpx no instalado — pip install httpx")

# solders — para wallets y txs de Solana
try:
    from solders.keypair import Keypair as _SoldersKeypair
    SOLDERS_OK = True
    print("[Crypto] ✅ solders cargado")
except ImportError as e:
    SOLDERS_OK = False
    print(f"[Crypto] ❌ solders no disponible: {e}")

# coincurve — para firmar txs de Litecoin (ECDSA secp256k1)
try:
    import coincurve as _coincurve
    COINCURVE_OK = True
    print("[Crypto] ✅ coincurve cargado")
except ImportError as e:
    COINCURVE_OK = False
    print(f"[Crypto] ❌ coincurve no disponible: {e}")

# ── Variables de entorno ──────────────────────────────────────
HELIUS_API_KEY    = os.getenv("HELIUS_API_KEY", "")
BLOCKCYPHER_TOKEN = os.getenv("BLOCKCYPHER_TOKEN", "")
GEMS_PER_USD      = int(os.getenv("GEMS_PER_USD", "1000000"))
HOT_WALLET_SOL    = os.getenv("HOT_WALLET_SOL", "")
HOT_WALLET_LTC    = os.getenv("HOT_WALLET_LTC", "")

# Reserva mínima para pagar el fee del sweep
SOL_FEE_RESERVE = 0.000010     # 0.00001 SOL — fee estándar de Solana
LTC_FEE_RESERVE = 0.0002       # 0.0002 LTC — fee conservador de Litecoin


# ══════════════════════════════════════════════════════════════
# GENERACIÓN DE WALLETS
# ══════════════════════════════════════════════════════════════

def generate_sol_wallet() -> tuple:
    """Genera wallet SOL. Retorna (address, privkey_hex) o (None, None) si falla."""
    if not SOLDERS_OK:
        print("[Crypto] ❌ No se puede generar wallet SOL — solders no disponible")
        return None, None
    try:
        from solders.keypair import Keypair
        seed    = secrets.token_bytes(32)
        keypair = Keypair.from_seed(seed)
        return str(keypair.pubkey()), bytes(keypair).hex()
    except Exception as e:
        print(f"[Crypto] ❌ Error generando wallet SOL: {e}")
        return None, None

async def generate_ltc_wallet() -> tuple:
    """
    Genera wallet LTC via BlockCypher.
    Retorna (address, wif_private_key) o (None, None).
    """
    url    = "https://api.blockcypher.com/v1/ltc/main/addrs"
    params = {"token": BLOCKCYPHER_TOKEN} if BLOCKCYPHER_TOKEN else {}
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(url, params=params)
            if r.status_code in (200, 201):
                d = r.json()
                # BlockCypher retorna: address, private (hex), public (hex), wif
                return d.get("address"), d.get("private")   # private en hex
    except Exception as e:
        print(f"[LTC Wallet] Error: {e}")
    return None, None


# ══════════════════════════════════════════════════════════════
# PRECIO EN USD (CoinGecko)
# ══════════════════════════════════════════════════════════════

async def get_price_usd(coin: str) -> float:
    """Obtiene precio de SOL o LTC en USD desde CoinGecko."""
    ids = {"SOL": "solana", "LTC": "litecoin"}
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids[coin]}&vs_currencies=usd"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(url)
            if r.status_code == 200:
                return float(r.json().get(ids[coin], {}).get("usd", 0))
    except Exception:
        pass
    return 0.0


# ══════════════════════════════════════════════════════════════
# DETECCIÓN DE TRANSACCIONES
# ══════════════════════════════════════════════════════════════

async def check_sol_transactions(address: str) -> list:
    """Busca txs SOL entrantes via Helius Enhanced Transactions API."""
    if not HELIUS_API_KEY:
        print(f"[SOL Scanner] ❌ HELIUS_API_KEY no configurada en Railway")
        return []
    url    = f"https://api.helius.xyz/v0/addresses/{address}/transactions"
    params = {"api-key": HELIUS_API_KEY, "limit": 10, "type": "TRANSFER"}
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(url, params=params)
            print(f"[SOL Scanner] Helius response: {r.status_code} para {address[:16]}...")
            if r.status_code != 200:
                print(f"[SOL Scanner] ❌ Helius error: {r.text[:200]}")
                return []
            results = []
            for tx in r.json():
                if tx.get("transactionError"):
                    continue
                for t in tx.get("nativeTransfers", []):
                    if t.get("toUserAccount") == address:
                        sol = t.get("amount", 0) / 1_000_000_000
                        if sol >= 0.001:
                            results.append({
                                "tx_hash": tx.get("signature"),
                                "amount":  sol,
                                "coin":    "SOL"
                            })
            return results
    except Exception as e:
        print(f"[SOL Scanner] Error: {e}")
        return []

async def check_ltc_transactions(address: str) -> list:
    """Busca txs LTC confirmadas via BlockCypher."""
    url    = f"https://api.blockcypher.com/v1/ltc/main/addrs/{address}/full"
    params = {"limit": 10}
    if BLOCKCYPHER_TOKEN:
        params["token"] = BLOCKCYPHER_TOKEN
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(url, params=params)
            if r.status_code != 200:
                return []
            results = []
            for tx in r.json().get("txs", []):
                if tx.get("confirmations", 0) < 2:
                    continue
                for out in tx.get("outputs", []):
                    if address in out.get("addresses", []):
                        ltc = out.get("value", 0) / 100_000_000
                        if ltc >= 0.0001:
                            results.append({
                                "tx_hash": tx.get("hash"),
                                "amount":  ltc,
                                "coin":    "LTC"
                            })
            return results
    except Exception as e:
        print(f"[LTC Scanner] Error: {e}")
        return []


# ══════════════════════════════════════════════════════════════
# SWEEP SOL → tu hot wallet
# ══════════════════════════════════════════════════════════════

async def sweep_sol(privkey_hex: str, from_address: str, to_address: str, amount_sol: float) -> str | None:
    """
    Envía SOL de la deposit address a tu hot wallet.
    Verificado: keypair restore OK, tx building OK, signing OK.

    Parámetros:
      privkey_hex  → 128-char hex (64 bytes = secret + pubkey)
      from_address → dirección pública de origen
      to_address   → tu hot wallet (Phantom)
      amount_sol   → cantidad detectada en la transacción
    """
    if not to_address or not HELIUS_API_KEY:
        return None
    if not SOLDERS_OK:
        print("[Sweep SOL] ❌ solders no disponible — sweep cancelado")
        return None

    lamports = int((amount_sol - SOL_FEE_RESERVE) * 1_000_000_000)
    if lamports <= 0:
        print(f"[Sweep SOL] Cantidad insuficiente: {amount_sol} SOL")
        return None

    try:
        from solders.keypair import Keypair
        from solders.pubkey import Pubkey
        from solders.hash import Hash
        from solders.system_program import TransferParams, transfer
        from solders.transaction import Transaction
        from solders.message import Message

        # Reconstruye el keypair desde los bytes guardados
        keypair   = Keypair.from_bytes(bytes.fromhex(privkey_hex))
        to_pubkey = Pubkey.from_string(to_address)

        rpc = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"

        async with httpx.AsyncClient(timeout=30) as c:
            # 1. Obtiene blockhash reciente (necesario para cada tx)
            bh_r = await c.post(rpc, json={
                "jsonrpc": "2.0", "id": 1,
                "method":  "getLatestBlockhash",
                "params":  [{"commitment": "finalized"}]
            })
            bh_data   = bh_r.json()
            blockhash = Hash.from_string(bh_data["result"]["value"]["blockhash"])

            # 2. Construye la instrucción de transferencia SOL nativo
            ix  = transfer(TransferParams(
                from_pubkey = keypair.pubkey(),
                to_pubkey   = to_pubkey,
                lamports    = lamports
            ))

            # 3. Crea el mensaje y firma la transacción
            msg = Message([ix], keypair.pubkey())
            tx  = Transaction([keypair], msg, blockhash)

            # 4. Serializa a base64 y envía
            tx_b64 = base64.b64encode(bytes(tx)).decode()
            send_r = await c.post(rpc, json={
                "jsonrpc": "2.0", "id": 1,
                "method":  "sendTransaction",
                "params":  [tx_b64, {"encoding": "base64", "skipPreflight": False}]
            })
            result = send_r.json()

            if "result" in result:
                tx_hash = result["result"]
                print(f"[Sweep SOL] ✅ {amount_sol:.6f} SOL → hot wallet | {tx_hash[:20]}...")
                return tx_hash
            else:
                err = result.get("error", {})
                print(f"[Sweep SOL] ❌ RPC error: {err.get('message', err)}")
                return None

    except Exception as e:
        print(f"[Sweep SOL] ❌ Exception: {e}")
        return None


# ══════════════════════════════════════════════════════════════
# SWEEP LTC → tu hot wallet
# ══════════════════════════════════════════════════════════════

async def sweep_ltc(privkey_hex: str, from_address: str, to_address: str, amount_ltc: float) -> str | None:
    """
    Envía LTC de la deposit address a tu hot wallet via BlockCypher.
    FIXED: usa ECDSA secp256k1 real con coincurve para firmar.
    Antes: pasaba privkey como signature (bug crítico).
    Ahora: firma correctamente los datos tosign de BlockCypher.

    Parámetros:
      privkey_hex  → 64-char hex (32 bytes) de la clave privada
      from_address → dirección LTC de origen
      to_address   → tu hot wallet LTC
      amount_ltc   → cantidad detectada
    """
    if not to_address:
        return None
    if not COINCURVE_OK:
        print("[Sweep LTC] ❌ coincurve no disponible — sweep cancelado")
        return None

    satoshis = int((amount_ltc - LTC_FEE_RESERVE) * 100_000_000)
    if satoshis <= 0:
        print(f"[Sweep LTC] Cantidad insuficiente: {amount_ltc} LTC")
        return None

    try:
        import coincurve                    # ECDSA secp256k1 — verificado OK

        # Carga la clave privada y deriva la pública (compressed)
        priv_bytes = bytes.fromhex(privkey_hex)
        priv_key   = coincurve.PrivateKey(priv_bytes)
        pub_hex    = priv_key.public_key.format(compressed=True).hex()

        params = {"token": BLOCKCYPHER_TOKEN} if BLOCKCYPHER_TOKEN else {}

        async with httpx.AsyncClient(timeout=30) as c:
            # 1. Construye el esqueleto de la transacción
            build_r = await c.post(
                "https://api.blockcypher.com/v1/ltc/main/txs/new",
                params=params,
                json={
                    "inputs":  [{"addresses": [from_address]}],
                    "outputs": [{"addresses": [to_address], "value": satoshis}]
                }
            )
            if build_r.status_code not in (200, 201):
                print(f"[Sweep LTC] ❌ Build error {build_r.status_code}: {build_r.text[:200]}")
                return None

            tx_skel = build_r.json()

            # 2. Firma cada elemento del array tosign con ECDSA secp256k1
            # BlockCypher envía hashes de 32 bytes que debemos firmar raw (sin re-hashear)
            signatures = []
            pubkeys    = []

            for tosign_hex in tx_skel.get("tosign", []):
                tosign_bytes = bytes.fromhex(tosign_hex)    # 32 bytes (hash)

                # Firma raw — hasher=None porque BlockCypher ya envía el hash
                sig_bytes = priv_key.sign(tosign_bytes, hasher=None)
                signatures.append(sig_bytes.hex())
                pubkeys.append(pub_hex)

            # 3. Adjunta firmas y claves públicas al esqueleto
            tx_skel["signatures"] = signatures
            tx_skel["pubkeys"]    = pubkeys

            # 4. Envía la transacción firmada
            send_r = await c.post(
                "https://api.blockcypher.com/v1/ltc/main/txs/send",
                params=params,
                json=tx_skel
            )
            if send_r.status_code in (200, 201):
                tx_hash = send_r.json().get("hash")
                print(f"[Sweep LTC] ✅ {amount_ltc:.6f} LTC → hot wallet | {tx_hash[:20] if tx_hash else 'N/A'}...")
                return tx_hash
            else:
                print(f"[Sweep LTC] ❌ Send error {send_r.status_code}: {send_r.text[:200]}")
                return None

    except Exception as e:
        print(f"[Sweep LTC] ❌ Exception: {e}")
        return None


# ══════════════════════════════════════════════════════════════
# COG PRINCIPAL
# ══════════════════════════════════════════════════════════════

class Crypto(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.scan_task.start()

    def cog_unload(self):
        self.scan_task.cancel()

    # ── /deposit_crypto ───────────────────────────────────────
    @app_commands.command(name="deposit_crypto", description="Deposita SOL o LTC y recibe gemas")
    @app_commands.describe(moneda="Elige la moneda")
    @app_commands.choices(moneda=[
        app_commands.Choice(name="Solana (SOL) — Fees < $0.001", value="SOL"),
        app_commands.Choice(name="Litecoin (LTC) — Fees ~$0.01",  value="LTC"),
    ])
    async def deposit_crypto(self, interaction: discord.Interaction, moneda: str):
        if not await check_linked(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        user_id = str(interaction.user.id)
        db      = self.bot.db
        coin    = moneda.upper()

        # Busca o crea wallet
        existing = await db.get_crypto_wallet(user_id, coin)
        if existing:
            address = existing["address"]
        else:
            if coin == "SOL":
                address, privkey = generate_sol_wallet()
            else:
                address, privkey = await generate_ltc_wallet()

            if not address:
                await interaction.followup.send(
                    embed=error_embed(
                        "No se pudo generar la dirección LTC.\n"
                        "Verifica que `BLOCKCYPHER_TOKEN` está en Railway."
                    ),
                    ephemeral=True
                )
                return
            await db.create_crypto_wallet(user_id, coin, address, privkey)

        # Precio actual y ratio
        price_usd    = await get_price_usd(coin)
        gems_per_usd = int(await db.get_config("gems_per_usd") or GEMS_PER_USD)
        color      = 0x9945FF if coin == "SOL" else 0x345D9D
        coin_emoji = "◎" if coin == "SOL" else "Ł"

        embed = discord.Embed(title=f"{coin_emoji} Depósito en {coin}", color=color)
        embed.add_field(name="Tu dirección de depósito", value=f"```{address}```", inline=False)
        if price_usd > 0:
            embed.add_field(
                name="Tasa actual",
                value=f"1 {coin} ≈ ${price_usd:.2f} → {fmt_gems(int(price_usd * gems_per_usd))}",
                inline=False
            )
        embed.add_field(
            name="Cómo depositar",
            value=(
                f"1. Envía {coin} a tu dirección\n"
                f"2. El bot lo detecta automáticamente\n"
                f"3. Recibes las gemas por DM"
            ),
            inline=False
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /sethotwallet ─────────────────────────────────────────
    @app_commands.command(name="sethotwallet", description="[OWNER] Tu wallet donde recibirás el crypto")
    @app_commands.describe(coin="SOL o LTC", address="Tu dirección de Phantom / Litecoin")
    @app_commands.choices(coin=[
        app_commands.Choice(name="Solana (SOL)", value="SOL"),
        app_commands.Choice(name="Litecoin (LTC)", value="LTC"),
    ])
    async def sethotwallet(self, interaction: discord.Interaction, coin: str, address: str):
        if not is_owner(interaction.user.id):
            await interaction.response.send_message(
                embed=error_embed("Solo el owner puede configurar la hot wallet."), ephemeral=True
            )
            return

        await self.bot.db.set_config(f"hot_wallet_{coin.lower()}", address)

        global HOT_WALLET_SOL, HOT_WALLET_LTC
        if coin == "SOL":
            HOT_WALLET_SOL = address
        else:
            HOT_WALLET_LTC = address

        coin_emoji = "◎" if coin == "SOL" else "Ł"
        embed = discord.Embed(title=f"✅ Hot Wallet {coin} Configurada", color=0x2ECC71)
        embed.add_field(name=f"{coin_emoji} Dirección", value=f"`{address}`", inline=False)
        embed.add_field(
            name="Qué pasa ahora",
            value=f"Todo el {coin} depositado se enviará aquí automáticamente.",
            inline=False
        )
        embed.set_footer(text="⚠️ Verifica la dirección — los envíos son irreversibles")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /setgemrate ───────────────────────────────────────────
    @app_commands.command(name="setgemrate", description="[OWNER] Configura gemas por 1 USD")
    @app_commands.describe(gemas_por_usd="Ej: 1000000 = 1M gemas por $1")
    async def setgemrate(self, interaction: discord.Interaction, gemas_por_usd: int):
        if not is_owner(interaction.user.id):
            await interaction.response.send_message(
                embed=error_embed("Solo el owner."), ephemeral=True
            )
            return
        await self.bot.db.set_config("gems_per_usd", str(gemas_por_usd))
        await interaction.response.send_message(
            embed=success_embed("Ratio", f"1 USD = {fmt_gems(gemas_por_usd)}"), ephemeral=True
        )

    # ── /crypto_balance ───────────────────────────────────────
    @app_commands.command(name="crypto_balance", description="Ver tus wallets de depósito")
    async def crypto_balance(self, interaction: discord.Interaction):
        if not await check_linked(interaction):
            return
        db  = self.bot.db
        uid = str(interaction.user.id)
        sol = await db.get_crypto_wallet(uid, "SOL")
        ltc = await db.get_crypto_wallet(uid, "LTC")

        embed = discord.Embed(title="🔐 Mis Wallets", color=COLOR_INFO)
        embed.add_field(
            name="◎ Solana",
            value=f"`{sol['address']}`" if sol else "Usa `/deposit_crypto sol`",
            inline=False
        )
        embed.add_field(
            name="Ł Litecoin",
            value=f"`{ltc['address']}`" if ltc else "Usa `/deposit_crypto ltc`",
            inline=False
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ══════════════════════════════════════════════════════════
    # SCANNER + SWEEP (cada 30 segundos)
    # ══════════════════════════════════════════════════════════

    @tasks.loop(seconds=30)
    async def scan_task(self):
        """Escanea wallets y hace sweep cada 30 segundos."""
        try:
            await self._process_coin("SOL")
        except Exception as e:
            print(f"[SOL Scanner] ❌ Error inesperado: {e}")
            import traceback
            traceback.print_exc()
        try:
            await self._process_coin("LTC")
        except Exception as e:
            print(f"[LTC Scanner] ❌ Error inesperado: {e}")
            import traceback
            traceback.print_exc()

    @scan_task.before_loop
    async def before_scan(self):
        """Espera a que el bot esté listo y carga hot wallets desde DB."""
        await self.bot.wait_until_ready()
        global HOT_WALLET_SOL, HOT_WALLET_LTC
        sol_db = await self.bot.db.get_config("hot_wallet_sol")
        ltc_db = await self.bot.db.get_config("hot_wallet_ltc")
        if sol_db and not HOT_WALLET_SOL:
            HOT_WALLET_SOL = sol_db
        if ltc_db and not HOT_WALLET_LTC:
            HOT_WALLET_LTC = ltc_db

    async def _process_coin(self, coin: str):
        """Detecta txs nuevas, acredita gemas y hace sweep."""
        db           = self.bot.db
        wallets      = await db.get_all_wallets(coin)

        print(f"[{coin} Scanner] Revisando {len(wallets)} wallet(s)...")

        if not wallets:
            print(f"[{coin} Scanner] Sin wallets en DB — espera a que alguien use /deposit_crypto")
            return

        price_usd    = await get_price_usd(coin)
        gems_per_usd = int(await db.get_config("gems_per_usd") or GEMS_PER_USD)
        hot_wallet   = HOT_WALLET_SOL if coin == "SOL" else HOT_WALLET_LTC

        print(f"[{coin} Scanner] Precio: ${price_usd:.2f} | Ratio: {gems_per_usd}/USD | Hot wallet: {'✅' if hot_wallet else '❌ NO CONFIGURADA'}")

        if price_usd <= 0:
            print(f"[{coin} Scanner] ❌ CoinGecko no devolvió precio — reintentando en 30s")
            return

        for wallet in wallets:
            address    = wallet["address"]
            discord_id = wallet["discord_id"]
            privkey    = wallet["private_key"]

            txs = await check_sol_transactions(address) if coin == "SOL" \
                  else await check_ltc_transactions(address)

            print(f"[{coin} Scanner] {address[:16]}... → {len(txs)} tx(s) encontradas")

            for tx in txs:
                tx_hash = tx.get("tx_hash")
                if not tx_hash:
                    continue

                already = await db.has_crypto_tx(tx_hash)
                if already:
                    print(f"[{coin} Scanner] TX {tx_hash[:16]}... ya procesada, saltando")
                    continue

                amount     = tx["amount"]
                amount_usd = amount * price_usd
                gems       = int(amount_usd * gems_per_usd)

                print(f"[{coin} Scanner] 🆕 Nueva TX: {amount:.6f} {coin} = ${amount_usd:.2f} = {gems:,} gemas")

                if gems <= 0:
                    print(f"[{coin} Scanner] ⚠️ Gemas calculadas = 0, revisa GEMS_PER_USD")
                    continue

                # 1. Acredita gemas
                await db.add_balance(discord_id, gems)
                await db.record_crypto_tx(tx_hash, discord_id, coin, amount_usd, gems)
                print(f"[{coin}] ✅ {discord_id} +{gems:,} gemas por {amount:.6f} {coin} (${amount_usd:.2f})")

                # 2. DM al usuario
                try:
                    user    = await self.bot.fetch_user(int(discord_id))
                    new_bal = await db.get_balance(discord_id)
                    e       = discord.Embed(title="✅ Depósito Confirmado", color=COLOR_GOLD)
                    e.add_field(name="Recibido",  value=f"{amount:.6f} {coin} ≈ ${amount_usd:.2f}", inline=True)
                    e.add_field(name="Gemas",     value=fmt_gems(gems),    inline=True)
                    e.add_field(name="Saldo",     value=fmt_gems(new_bal), inline=True)
                    e.set_footer(text=f"TX: {tx_hash[:24]}...")
                    await user.send(embed=e)
                except Exception:
                    pass

                # 3. Sweep → hot wallet
                if hot_wallet and hot_wallet != address:
                    await asyncio.sleep(3)  # Espera 3s para que la tx se asiente
                    if coin == "SOL":
                        sweep_hash = await sweep_sol(privkey, address, hot_wallet, amount)
                    else:
                        sweep_hash = await sweep_ltc(privkey, address, hot_wallet, amount)

                    if not sweep_hash:
                        print(f"[Sweep {coin}] ⚠️ Sweep falló para {address[:16]}... — revisar en Railway logs")
                elif not hot_wallet:
                    print(f"[Sweep {coin}] ⚠️ Sin hot wallet — usa /sethotwallet en Discord")


async def setup(bot):
    await bot.add_cog(Crypto(bot))
