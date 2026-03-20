# ============================================================
# main.py — Punto de entrada principal del bot
# ============================================================
# Este archivo inicia el bot, carga todos los módulos (cogs)
# y conecta con Discord usando el token del archivo .env
# ============================================================

import discord                          # Librería principal de Discord
from discord.ext import commands        # Sistema de comandos y slash commands
import asyncio                          # Para operaciones asíncronas
import os                               # Para leer variables de entorno
import threading                        # Para correr el servidor HTTP en paralelo
from http.server import HTTPServer, BaseHTTPRequestHandler  # Servidor HTTP minimal
from dotenv import load_dotenv          # Para cargar el archivo .env
from database import Database           # Nuestra clase de base de datos

# Carga las variables del archivo .env (TOKEN, IDs, etc.)
load_dotenv()

# ── Configuración de intents ──────────────────────────────────
# Los intents definen qué eventos puede "escuchar" el bot
intents = discord.Intents.default()     # Intents básicos
intents.message_content = True          # Necesario para leer mensajes
intents.members = True                  # Necesario para gestionar miembros

# ── Creación del bot ──────────────────────────────────────────
# Prefijo "!" para comandos de texto (aunque usaremos slash commands)
bot = commands.Bot(command_prefix="!", intents=intents)

# ── Instancia global de la base de datos ─────────────────────
bot.db = Database()                     # Conecta con SQLite al iniciar

# ── Lista de cogs (módulos) a cargar ─────────────────────────
# Cada cog agrupa comandos relacionados entre sí
COGS = [
    "cogs.economy",          # Comandos: /link, /balance, /deposit, /withdraw, /tip, /rakeback
    "cogs.admin",            # Comandos: /setagentlimit, /sethouseedge, etc.
    "cogs.logs_cog",         # Comandos: /logs
    "cogs.rain",             # Comando: /rain (lluvia de gemas)
    "cogs.codes",            # Comandos: /code create|redeem|delete|list
    "cogs.crypto",           # Comandos: /deposit_crypto, /crypto_balance, /setgemrate
    "cogs.games.blackjack",  # Juego: Blackjack
    "cogs.games.dice",       # Juego: Dados
    "cogs.games.hilo",       # Juego: Hi-Lo
    "cogs.games.coinflip",   # Juego: Coinflip PvP
    "cogs.games.mines",      # Juego: Minas
    "cogs.games.keno",       # Juego: Keno
]

# ── Evento: Bot listo ─────────────────────────────────────────
@bot.event
async def on_ready():
    """Se ejecuta cuando el bot se conecta correctamente a Discord."""
    await bot.db.initialize()                       # Crea las tablas si no existen
    print(f"✅ Bot conectado como: {bot.user}")     # Muestra el nombre del bot
    print(f"📡 Servidores activos: {len(bot.guilds)}")  # Cantidad de servidores

    # Sincroniza los slash commands con Discord
    try:
        synced = await bot.tree.sync()              # Sube los comandos a Discord
        print(f"🔄 {len(synced)} slash commands sincronizados")
    except Exception as e:
        print(f"❌ Error al sincronizar comandos: {e}")

# ── Servidor HTTP para Railway health check ──────────────────
# Railway requiere que el proceso web responda en el puerto 8080.
# Este mini servidor responde con 200 OK para que Railway no lo mate.
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args):
        pass  # Silencia los logs del servidor HTTP

def start_health_server():
    """Arranca el servidor HTTP en un hilo separado."""
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"🌐 Health server corriendo en puerto {port}")
    server.serve_forever()

# ── Función principal asíncrona ───────────────────────────────
async def main():
    """Carga todos los módulos e inicia el bot."""
    # Carga cada cog de la lista
    for cog in COGS:
        try:
            await bot.load_extension(cog)           # Carga el módulo
            print(f"  ✔ Módulo cargado: {cog}")
        except Exception as e:
            print(f"  ✘ Error cargando {cog}: {e}") # Muestra error si falla

    # Lee el token del archivo .env y conecta el bot
    token = os.getenv("DISCORD_TOKEN")              # Lee DISCORD_TOKEN del .env
    if not token:
        raise ValueError("❌ No se encontró DISCORD_TOKEN en el archivo .env")

    # Arranca el servidor HTTP en un hilo separado (no bloquea el bot)
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()

    await bot.start(token)                          # Inicia la conexión con Discord

# ── Punto de entrada del script ───────────────────────────────
if __name__ == "__main__":
    asyncio.run(main())                             # Ejecuta la función principal
