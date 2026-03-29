# ============================================================
# database.py — Gestión completa de la base de datos SQLite
# ============================================================
# Aquí se definen todas las tablas y operaciones de datos.
# Usamos SQLite (archivo local) para máxima simplicidad.
# Cada método corresponde a una operación de base de datos.
# ============================================================

import aiosqlite                        # SQLite asíncrono (compatible con discord.py)
import os                               # Para rutas de archivos
from datetime import datetime           # Para timestamps en logs

# Ruta al archivo de base de datos
DB_PATH = "ps99_bot.db"

class Database:
    """Clase principal que maneja todas las operaciones de base de datos."""

    def __init__(self):
        self.path = DB_PATH             # Guarda la ruta del archivo .db

    # ── INICIALIZACIÓN ────────────────────────────────────────
    async def initialize(self):
        """Crea todas las tablas si no existen al iniciar el bot."""
        async with aiosqlite.connect(self.path) as db:

            # Tabla: usuarios registrados del bot
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    discord_id   TEXT PRIMARY KEY,   -- ID único de Discord
                    roblox_name  TEXT,               -- Nombre en Roblox
                    balance      INTEGER DEFAULT 0,  -- Gemas actuales
                    total_wagered INTEGER DEFAULT 0, -- Total apostado históricamente
                    created_at   TEXT                -- Fecha de registro
                )
            """)

            # Tabla: configuración de agentes y sus límites
            await db.execute("""
                CREATE TABLE IF NOT EXISTS agents (
                    discord_id    TEXT PRIMARY KEY,  -- ID del agente
                    limit_total   INTEGER DEFAULT 0, -- Límite máximo asignado
                    limit_used    INTEGER DEFAULT 0  -- Cuánto ha procesado ya
                )
            """)

            # Tabla: historial de transacciones (depósitos y retiros)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    discord_id TEXT,                 -- Usuario que hizo la transacción
                    type       TEXT,                 -- 'deposit' o 'withdraw'
                    amount     INTEGER,              -- Cantidad de gemas
                    agent_id   TEXT,                 -- Agente que lo confirmó
                    status     TEXT DEFAULT 'pending', -- pending / confirmed / rejected
                    timestamp  TEXT                  -- Fecha y hora
                )
            """)

            # Tabla: historial de partidas jugadas
            await db.execute("""
                CREATE TABLE IF NOT EXISTS game_logs (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    discord_id TEXT,                 -- Quién jugó
                    game       TEXT,                 -- Nombre del juego
                    bet        INTEGER,              -- Cuánto apostó
                    result     TEXT,                 -- win / lose / tie
                    profit     INTEGER,              -- Ganancia neta (puede ser negativa)
                    timestamp  TEXT                  -- Cuándo jugó
                )
            """)

            # Tabla: configuración general del servidor
            await db.execute("""
                CREATE TABLE IF NOT EXISTS config (
                    key   TEXT PRIMARY KEY,          -- Nombre de la configuración
                    value TEXT                       -- Valor de la configuración
                )
            """)

            # Tabla: roles automáticos según wager total
            await db.execute("""
                CREATE TABLE IF NOT EXISTS wager_roles (
                    threshold INTEGER,               -- Gemas apostadas requeridas
                    role_id   TEXT                   -- ID del rol a asignar
                )
            """)

            # Tabla: house edge por juego
            await db.execute("""
                CREATE TABLE IF NOT EXISTS house_edge (
                    game       TEXT PRIMARY KEY,
                    edge_pct   REAL DEFAULT 5.0
                )
            """)

            # Tabla: rakeback pendiente por usuario
            await db.execute("""
                CREATE TABLE IF NOT EXISTS rakeback (
                    discord_id TEXT PRIMARY KEY,
                    pending    INTEGER DEFAULT 0
                )
            """)

            # Tabla: wager requerido antes de poder retirar
            # Se añade con cada depósito, tip, rain o código recibido
            # Se reduce con cada apuesta hecha en los juegos
            await db.execute("""
                CREATE TABLE IF NOT EXISTS wager_requirement (
                    discord_id      TEXT PRIMARY KEY,
                    pending_wager   INTEGER DEFAULT 0
                )
            """)

            # Tabla: wallets de crypto generadas por usuario
            await db.execute("""
                CREATE TABLE IF NOT EXISTS crypto_wallets (
                    discord_id  TEXT,
                    coin        TEXT,              -- 'SOL' o 'LTC'
                    address     TEXT,              -- Dirección pública
                    private_key TEXT,              -- Clave privada cifrada
                    PRIMARY KEY (discord_id, coin)
                )
            """)

            # Tabla: transacciones de crypto ya procesadas
            # Evita procesar el mismo pago dos veces
            await db.execute("""
                CREATE TABLE IF NOT EXISTS crypto_transactions (
                    tx_hash     TEXT PRIMARY KEY,  -- Hash único de la transacción
                    discord_id  TEXT,              -- A quién se le acreditó
                    coin        TEXT,              -- SOL o LTC
                    amount_usd  REAL,              -- Valor en USD al momento
                    gems_added  INTEGER,           -- Gemas que se añadieron
                    timestamp   TEXT               -- Cuándo se procesó
                )
            """)

            # Tabla: códigos de canje
            # Creados por owner/admin, inyectan gemas sin descontarlas de nadie
            await db.execute("""
                CREATE TABLE IF NOT EXISTS codes (
                    code        TEXT PRIMARY KEY,   -- El código en sí
                    gems        INTEGER,             -- Gemas que da al canjear
                    total_uses  INTEGER,             -- Usos totales permitidos
                    used_count  INTEGER DEFAULT 0,   -- Cuántas veces se ha usado
                    created_by  TEXT,                -- ID del owner/admin que lo creó
                    created_at  TEXT                 -- Fecha de creación
                )
            """)

            # Tabla: registro de quién ha canjeado qué código
            # Previene que un mismo usuario canjee el mismo código dos veces
            await db.execute("""
                CREATE TABLE IF NOT EXISTS code_redemptions (
                    code       TEXT,                 -- Código canjeado
                    discord_id TEXT,                 -- Quién lo canjeó
                    redeemed_at TEXT,                -- Cuándo lo canjeó
                    PRIMARY KEY (code, discord_id)   -- Un usuario = un uso por código
                )
            """)

            # Inserta house edges por defecto si no existen
            games = ["blackjack", "dice", "hilo", "coinflip", "mines", "keno"]
            for game in games:
                await db.execute(
                    "INSERT OR IGNORE INTO house_edge (game, edge_pct) VALUES (?, ?)",
                    (game, 5.0)                      # 5% por defecto
                )

            await db.commit()                        # Guarda todos los cambios

    # ══════════════════════════════════════════════════════════
    # MÉTODOS DE USUARIOS
    # ══════════════════════════════════════════════════════════

    async def get_user(self, discord_id: str):
        """Obtiene los datos de un usuario. Retorna None si no existe."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row            # Permite acceso por nombre de columna
            cursor = await db.execute(
                "SELECT * FROM users WHERE discord_id = ?", (discord_id,)
            )
            return await cursor.fetchone()            # Retorna la fila o None

    async def create_user(self, discord_id: str, roblox_name: str):
        """Crea un nuevo usuario vinculado con su cuenta de Roblox."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO users (discord_id, roblox_name, balance, total_wagered, created_at)
                   VALUES (?, ?, 0, 0, ?)""",
                (discord_id, roblox_name, datetime.utcnow().isoformat())
            )
            await db.commit()                         # Confirma la inserción

    async def update_roblox(self, discord_id: str, roblox_name: str):
        """Actualiza el nombre de Roblox de un usuario existente."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE users SET roblox_name = ? WHERE discord_id = ?",
                (roblox_name, discord_id)
            )
            await db.commit()

    async def get_balance(self, discord_id: str) -> int:
        """Retorna el balance actual de gemas de un usuario."""
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT balance FROM users WHERE discord_id = ?", (discord_id,)
            )
            row = await cursor.fetchone()
            return row[0] if row else 0               # Retorna 0 si no existe

    async def add_balance(self, discord_id: str, amount: int):
        """Añade gemas al balance de un usuario."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE users SET balance = balance + ? WHERE discord_id = ?",
                (amount, discord_id)
            )
            await db.commit()

    async def remove_balance(self, discord_id: str, amount: int) -> bool:
        """
        Descuenta gemas si el saldo es suficiente.
        Retorna True si se hizo con éxito, False si no hay saldo.
        """
        async with aiosqlite.connect(self.path) as db:
            # Verifica que tiene saldo suficiente antes de descontar
            cursor = await db.execute(
                "SELECT balance FROM users WHERE discord_id = ?", (discord_id,)
            )
            row = await cursor.fetchone()
            if not row or row[0] < amount:
                return False                          # No tiene suficiente saldo

            await db.execute(
                "UPDATE users SET balance = balance - ? WHERE discord_id = ?",
                (amount, discord_id)
            )
            await db.commit()
            return True                               # Operación exitosa

    async def add_wager(self, discord_id: str, amount: int):
        """Incrementa el total apostado históricamente por el usuario."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE users SET total_wagered = total_wagered + ? WHERE discord_id = ?",
                (amount, discord_id)
            )
            await db.commit()

    # ══════════════════════════════════════════════════════════
    # MÉTODOS DE AGENTES
    # ══════════════════════════════════════════════════════════

    async def get_agent(self, discord_id: str):
        """Obtiene datos de un agente. Retorna None si no es agente."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM agents WHERE discord_id = ?", (discord_id,)
            )
            return await cursor.fetchone()

    async def set_agent_limit(self, discord_id: str, limit: int):
        """Crea o actualiza el límite total de un agente."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO agents (discord_id, limit_total, limit_used)
                   VALUES (?, ?, 0)
                   ON CONFLICT(discord_id) DO UPDATE SET limit_total = ?""",
                (discord_id, limit, limit)
            )
            await db.commit()

    async def use_agent_limit(self, discord_id: str, amount: int) -> bool:
        """
        Usa una parte del límite del agente al confirmar un depósito.
        Retorna False si no tiene suficiente límite disponible.
        """
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT limit_total, limit_used FROM agents WHERE discord_id = ?",
                (discord_id,)
            )
            row = await cursor.fetchone()
            if not row:
                return False                          # No es agente registrado

            available = row[0] - row[1]              # Límite disponible = total - usado
            if available < amount:
                return False                          # No le alcanza el límite

            await db.execute(
                "UPDATE agents SET limit_used = limit_used + ? WHERE discord_id = ?",
                (amount, discord_id)
            )
            await db.commit()
            return True

    async def reset_agent_limit(self, discord_id: str):
        """Resetea el contador de límite usado de un agente a 0."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE agents SET limit_used = 0 WHERE discord_id = ?",
                (discord_id,)
            )
            await db.commit()

    # ══════════════════════════════════════════════════════════
    # MÉTODOS DE TRANSACCIONES (DEPÓSITOS / RETIROS)
    # ══════════════════════════════════════════════════════════

    async def create_transaction(self, discord_id: str, type: str, amount: int) -> int:
        """
        Crea una nueva transacción pendiente.
        Retorna el ID de la transacción creada.
        """
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """INSERT INTO transactions (discord_id, type, amount, status, timestamp)
                   VALUES (?, ?, ?, 'pending', ?)""",
                (discord_id, type, amount, datetime.utcnow().isoformat())
            )
            await db.commit()
            return cursor.lastrowid               # Retorna el ID auto-generado

    async def confirm_transaction(self, tx_id: int, agent_id: str):
        """Marca una transacción como confirmada por un agente específico."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """UPDATE transactions
                   SET status = 'confirmed', agent_id = ?
                   WHERE id = ?""",
                (agent_id, tx_id)
            )
            await db.commit()

    async def get_transaction(self, tx_id: int):
        """Obtiene una transacción por su ID."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM transactions WHERE id = ?", (tx_id,)
            )
            return await cursor.fetchone()

    async def get_user_transactions(self, discord_id: str, limit: int = 10):
        """Obtiene el historial de transacciones de un usuario (más recientes primero)."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM transactions
                   WHERE discord_id = ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (discord_id, limit)
            )
            return await cursor.fetchall()

    # ══════════════════════════════════════════════════════════
    # MÉTODOS DE LOGS DE JUEGOS
    # ══════════════════════════════════════════════════════════

    async def log_game(self, discord_id: str, game: str, bet: int, result: str, profit: int):
        """Registra una partida jugada en el historial."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO game_logs (discord_id, game, bet, result, profit, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (discord_id, game, bet, result, profit, datetime.utcnow().isoformat())
            )
            await db.commit()

    async def get_game_logs(self, discord_id: str, limit: int = 10):
        """Obtiene el historial de juegos de un usuario."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM game_logs
                   WHERE discord_id = ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (discord_id, limit)
            )
            return await cursor.fetchall()

    # ══════════════════════════════════════════════════════════
    # MÉTODOS DE CONFIGURACIÓN
    # ══════════════════════════════════════════════════════════

    async def get_config(self, key: str):
        """Obtiene un valor de configuración por su clave."""
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT value FROM config WHERE key = ?", (key,)
            )
            row = await cursor.fetchone()
            return row[0] if row else None            # Retorna el valor o None

    async def set_config(self, key: str, value: str):
        """Guarda o actualiza un valor de configuración."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                (key, value)
            )
            await db.commit()

    async def get_house_edge(self, game: str) -> float:
        """Obtiene el house edge de un juego específico."""
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT edge_pct FROM house_edge WHERE game = ?", (game,)
            )
            row = await cursor.fetchone()
            return row[0] if row else 5.0             # 5% por defecto si no existe

    async def set_house_edge(self, game: str, edge: float):
        """Actualiza el house edge de un juego."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO house_edge (game, edge_pct) VALUES (?, ?)",
                (game, edge)
            )
            await db.commit()

    # ══════════════════════════════════════════════════════════
    # MÉTODOS DE ROLES POR WAGER
    # ══════════════════════════════════════════════════════════

    async def get_wager_roles(self):
        """Retorna todos los roles de wager ordenados por threshold (ascendente)."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM wager_roles ORDER BY threshold ASC"
            )
            return await cursor.fetchall()

    async def add_wager_role(self, threshold: int, role_id: str):
        """Añade un nuevo rol de wager al sistema."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO wager_roles (threshold, role_id) VALUES (?, ?)",
                (threshold, role_id)
            )
            await db.commit()

    async def remove_wager_role(self, role_id: str):
        """Elimina un rol de wager del sistema."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "DELETE FROM wager_roles WHERE role_id = ?", (role_id,)
            )
            await db.commit()

    async def get_user_total_wagered(self, discord_id: str) -> int:
        """Obtiene el total apostado históricamente por un usuario."""
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT total_wagered FROM users WHERE discord_id = ?", (discord_id,)
            )
            row = await cursor.fetchone()
            return row[0] if row else 0

    # ══════════════════════════════════════════════════════════
    # MÉTODOS DE RAKEBACK
    # ══════════════════════════════════════════════════════════

    async def get_rakeback(self, discord_id: str) -> int:
        """Obtiene el rakeback pendiente de un usuario."""
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT pending FROM rakeback WHERE discord_id = ?", (discord_id,)
            )
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def claim_rakeback(self, discord_id: str) -> int:
        """Reclama el rakeback: lo añade al balance y lo resetea a 0."""
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT pending FROM rakeback WHERE discord_id = ?", (discord_id,)
            )
            row    = await cursor.fetchone()
            amount = row[0] if row else 0
            if amount <= 0:
                return 0
            await db.execute(
                "UPDATE users SET balance = balance + ? WHERE discord_id = ?",
                (amount, discord_id)
            )
            await db.execute(
                "UPDATE rakeback SET pending = 0 WHERE discord_id = ?", (discord_id,)
            )
            await db.commit()
            return amount

    async def add_rakeback(self, discord_id: str, amount: int):
        """Acumula rakeback al usuario. Se llama tras cada pérdida en un juego."""
        if amount <= 0:
            return
        async with aiosqlite.connect(self.path) as db:
            # Inserta o actualiza acumulando el nuevo rakeback
            await db.execute(
                """INSERT INTO rakeback (discord_id, pending)
                   VALUES (?, ?)
                   ON CONFLICT(discord_id) DO UPDATE SET pending = pending + ?""",
                (discord_id, amount, amount)
            )
            await db.commit()

    async def reset_rakeback(self, discord_id: str):
        """Pone el rakeback pendiente a 0 tras reclamarlo."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE rakeback SET pending = 0 WHERE discord_id = ?", (discord_id,)
            )
            await db.commit()

    # ══════════════════════════════════════════════════════════
    # MÉTODOS DE CRYPTO
    # ══════════════════════════════════════════════════════════

    async def get_crypto_wallet(self, discord_id: str, coin: str):
        """Obtiene la wallet de un usuario para una moneda específica."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM crypto_wallets WHERE discord_id=? AND coin=?",
                (discord_id, coin)
            )
            return await cursor.fetchone()

    async def create_crypto_wallet(self, discord_id: str, coin: str, address: str, private_key: str):
        """Crea y guarda una wallet de crypto para el usuario."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT OR IGNORE INTO crypto_wallets
                   (discord_id, coin, address, private_key) VALUES (?,?,?,?)""",
                (discord_id, coin, address, private_key)
            )
            await db.commit()

    async def get_all_wallets(self, coin: str):
        """Obtiene todas las wallets activas de una moneda (para el scanner)."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM crypto_wallets WHERE coin=?", (coin,)
            )
            return await cursor.fetchall()

    async def has_crypto_tx(self, tx_hash: str) -> bool:
        """Comprueba si una transacción ya fue procesada."""
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM crypto_transactions WHERE tx_hash=?", (tx_hash,)
            )
            return await cursor.fetchone() is not None

    async def record_crypto_tx(self, tx_hash: str, discord_id: str, coin: str, amount_usd: float, gems: int):
        """Registra una transacción de crypto procesada."""
        from datetime import datetime
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT OR IGNORE INTO crypto_transactions
                   (tx_hash, discord_id, coin, amount_usd, gems_added, timestamp)
                   VALUES (?,?,?,?,?,?)""",
                (tx_hash, discord_id, coin, amount_usd, gems, datetime.utcnow().isoformat())
            )
            await db.commit()

    # ══════════════════════════════════════════════════════════
    # MÉTODOS DE CÓDIGOS
    # ══════════════════════════════════════════════════════════

    async def create_code(self, code: str, gems: int, total_uses: int, created_by: str):
        """Crea un nuevo código de canje."""
        from datetime import datetime
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO codes (code, gems, total_uses, used_count, created_by, created_at)
                   VALUES (?, ?, ?, 0, ?, ?)""",
                (code.upper(), gems, total_uses, created_by, datetime.utcnow().isoformat())
            )
            await db.commit()

    async def get_code(self, code: str):
        """Obtiene los datos de un código. Retorna None si no existe."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM codes WHERE code = ?", (code.upper(),)
            )
            return await cursor.fetchone()

    async def delete_code(self, code: str):
        """Elimina un código del sistema."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM codes WHERE code = ?", (code.upper(),))
            await db.commit()

    async def has_redeemed(self, code: str, discord_id: str) -> bool:
        """Retorna True si el usuario ya canjeó este código."""
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM code_redemptions WHERE code = ? AND discord_id = ?",
                (code.upper(), discord_id)
            )
            return await cursor.fetchone() is not None

    async def redeem_code(self, code: str, discord_id: str) -> int:
        """
        Canjea un código para un usuario.
        Añade las gemas, incrementa used_count y registra la redención.
        Retorna las gemas dadas, o 0 si falló.
        """
        from datetime import datetime
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            # Lee el código
            cursor = await db.execute(
                "SELECT * FROM codes WHERE code = ?", (code.upper(),)
            )
            row = await cursor.fetchone()
            if not row:
                return 0
            if row["used_count"] >= row["total_uses"]:
                return 0  # Sin usos disponibles

            # Registra la redención del usuario
            await db.execute(
                "INSERT INTO code_redemptions (code, discord_id, redeemed_at) VALUES (?, ?, ?)",
                (code.upper(), discord_id, datetime.utcnow().isoformat())
            )
            # Incrementa el contador de usos
            await db.execute(
                "UPDATE codes SET used_count = used_count + 1 WHERE code = ?",
                (code.upper(),)
            )
            # Añade las gemas al usuario (inyección sin descontar de nadie)
            await db.execute(
                "UPDATE users SET balance = balance + ? WHERE discord_id = ?",
                (row["gems"], discord_id)
            )
            await db.commit()
            return row["gems"]

    async def list_codes(self):
        """Lista todos los códigos activos."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM codes ORDER BY created_at DESC"
            )
            return await cursor.fetchall()

    # ══════════════════════════════════════════════════════════
    # MÉTODOS DE WAGER REQUIREMENT
    # ══════════════════════════════════════════════════════════

    async def add_wager_requirement(self, discord_id: str, amount: int):
        """Añade wager requerido al usuario (depósito, tip, rain, código)."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO wager_requirement (discord_id, pending_wager)
                   VALUES (?, ?)
                   ON CONFLICT(discord_id) DO UPDATE
                   SET pending_wager = pending_wager + ?""",
                (discord_id, amount, amount)
            )
            await db.commit()

    async def get_wager_requirement(self, discord_id: str) -> int:
        """Obtiene el wager pendiente de un usuario."""
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT pending_wager FROM wager_requirement WHERE discord_id = ?",
                (discord_id,)
            )
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def reduce_wager_requirement(self, discord_id: str, amount: int):
        """Reduce el wager pendiente cuando el usuario apuesta en un juego."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """UPDATE wager_requirement
                   SET pending_wager = MAX(0, pending_wager - ?)
                   WHERE discord_id = ?""",
                (amount, discord_id)
            )
            await db.commit()
