# ============================================================
# dashboard.py — Panel web de configuración del bot
# ============================================================
# Ejecuta este archivo POR SEPARADO al bot con:
#   python dashboard.py
# Luego abre en el navegador: http://localhost:5000
#
# IMPORTANTE: Este panel es SOLO para el owner.
# Por seguridad solo funciona en localhost (tu propio PC).
# ============================================================

from flask import Flask, render_template_string, request, redirect, url_for, jsonify
import asyncio                          # Para ejecutar código asíncrono desde Flask
import aiosqlite                        # Para leer la base de datos directamente
import os                               # Para variables de entorno
from dotenv import load_dotenv          # Para leer el .env

load_dotenv()                           # Carga las variables del .env

app = Flask(__name__)                   # Crea la aplicación Flask
DB_PATH = "ps99_bot.db"                # Ruta a la base de datos del bot

# ── Función auxiliar: ejecutar query async desde Flask ───────
def run_query(coro):
    """Ejecuta una corrutina async desde el contexto síncrono de Flask."""
    loop = asyncio.new_event_loop()     # Crea un nuevo event loop
    result = loop.run_until_complete(coro)  # Ejecuta la corrutina
    loop.close()                        # Cierra el loop
    return result                       # Retorna el resultado

# ── Funciones de base de datos ────────────────────────────────
async def db_fetchall(query, params=()):
    """Ejecuta una consulta SELECT y retorna todas las filas."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row  # Acceso por nombre de columna
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]  # Convierte a lista de dicts

async def db_fetchone(query, params=()):
    """Ejecuta una consulta SELECT y retorna una sola fila."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(query, params)
        row = await cursor.fetchone()
        return dict(row) if row else None

async def db_execute(query, params=()):
    """Ejecuta INSERT, UPDATE o DELETE y guarda los cambios."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(query, params)
        await db.commit()               # Confirma los cambios

# ── Plantilla HTML del dashboard ──────────────────────────────
# Toda la interfaz está en esta cadena de texto.
# Usa Bootstrap 5 para el diseño responsivo.
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PS99 Bot — Panel de Control</title>
    <!-- Bootstrap 5 para el diseño -->
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <!-- Bootstrap Icons -->
    <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.0/font/bootstrap-icons.css" rel="stylesheet">
    <style>
        /* ── Estilos personalizados ─────────────────────── */
        body {
            background: #0d0f14;        /* Fondo oscuro */
            color: #e2e8f0;             /* Texto claro */
            font-family: 'Segoe UI', sans-serif;
        }
        .sidebar {
            background: #161922;        /* Panel lateral más oscuro */
            min-height: 100vh;
            border-right: 1px solid #2d3748;
            padding: 20px 0;
            position: sticky;
            top: 0;
        }
        .sidebar .brand {
            padding: 10px 20px 24px;
            border-bottom: 1px solid #2d3748;
            margin-bottom: 16px;
        }
        .sidebar .nav-link {
            color: #94a3b8;
            padding: 10px 20px;
            border-radius: 0;
            transition: all 0.2s;
        }
        .sidebar .nav-link:hover,
        .sidebar .nav-link.active {
            color: #a78bfa;             /* Morado para hover/activo */
            background: rgba(167,139,250,0.1);
        }
        .sidebar .nav-link i {
            width: 20px;
            margin-right: 8px;
        }
        .card {
            background: #1e2330;        /* Fondo de tarjetas */
            border: 1px solid #2d3748;
            border-radius: 12px;
        }
        .card-header {
            background: #252d3d;
            border-bottom: 1px solid #2d3748;
            border-radius: 12px 12px 0 0 !important;
            font-weight: 600;
        }
        .form-control, .form-select {
            background: #252d3d;        /* Inputs oscuros */
            border: 1px solid #2d3748;
            color: #e2e8f0;
        }
        .form-control:focus, .form-select:focus {
            background: #2d3748;
            border-color: #a78bfa;
            color: #e2e8f0;
            box-shadow: 0 0 0 3px rgba(167,139,250,0.2);
        }
        .btn-primary {
            background: #7c3aed;        /* Botón morado */
            border-color: #7c3aed;
        }
        .btn-primary:hover {
            background: #6d28d9;
            border-color: #6d28d9;
        }
        .table {
            color: #e2e8f0;
        }
        .table > :not(caption) > * > * {
            background-color: transparent;
            border-bottom-color: #2d3748;
        }
        .badge-gem {
            background: rgba(167,139,250,0.2);
            color: #a78bfa;
            padding: 3px 8px;
            border-radius: 6px;
            font-size: 0.8rem;
        }
        .stat-card {
            background: linear-gradient(135deg, #1e2330, #252d3d);
            border: 1px solid #2d3748;
            border-radius: 12px;
            padding: 20px;
            transition: transform 0.2s;
        }
        .stat-card:hover {
            transform: translateY(-2px);
        }
        .stat-number {
            font-size: 1.8rem;
            font-weight: 700;
            color: #a78bfa;
        }
        .alert-success {
            background: rgba(16,185,129,0.1);
            border-color: rgba(16,185,129,0.3);
            color: #6ee7b7;
        }
        .section { display: none; }     /* Secciones ocultas por defecto */
        .section.active { display: block; }
        h5 { color: #a78bfa; }
        .table-hover tbody tr:hover {
            background-color: rgba(167,139,250,0.05);
        }
    </style>
</head>
<body>
<div class="container-fluid">
<div class="row">

    <!-- ── Sidebar ─────────────────────────────────────── -->
    <div class="col-auto sidebar">
        <div class="brand">
            <div style="font-size:1.4rem; font-weight:700; color:#a78bfa">
                💎 PS99 Bot
            </div>
            <div style="color:#64748b; font-size:0.8rem">Panel de Control</div>
        </div>
        <nav class="nav flex-column">
            <!-- Cada enlace activa una sección distinta -->
            <a class="nav-link active" href="#" onclick="showSection('inicio')">
                <i class="bi bi-house"></i> Inicio
            </a>
            <a class="nav-link" href="#" onclick="showSection('canales')">
                <i class="bi bi-hash"></i> Canales
            </a>
            <a class="nav-link" href="#" onclick="showSection('agentes')">
                <i class="bi bi-person-badge"></i> Agentes
            </a>
            <a class="nav-link" href="#" onclick="showSection('houseedge')">
                <i class="bi bi-sliders"></i> House Edge
            </a>
            <a class="nav-link" href="#" onclick="showSection('usuarios')">
                <i class="bi bi-people"></i> Usuarios
            </a>
            <a class="nav-link" href="#" onclick="showSection('wagerroles')">
                <i class="bi bi-trophy"></i> Roles Wager
            </a>
            <a class="nav-link" href="#" onclick="showSection('logs')">
                <i class="bi bi-journal-text"></i> Logs
            </a>
        </nav>
    </div>

    <!-- ── Contenido principal ─────────────────────────── -->
    <div class="col p-4">

        {% if mensaje %}
        <!-- Mensaje de éxito/error tras guardar cambios -->
        <div class="alert alert-success alert-dismissible fade show" role="alert">
            ✅ {{ mensaje }}
            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
        </div>
        {% endif %}

        <!-- ═══════════════════════════════════════════ -->
        <!-- SECCIÓN: INICIO / ESTADÍSTICAS             -->
        <!-- ═══════════════════════════════════════════ -->
        <div id="sec-inicio" class="section active">
            <h4 class="mb-4">📊 Resumen del Servidor</h4>
            <div class="row g-3 mb-4">
                <!-- Tarjeta: total usuarios -->
                <div class="col-md-3">
                    <div class="stat-card">
                        <div style="color:#64748b; font-size:0.85rem">USUARIOS REGISTRADOS</div>
                        <div class="stat-number">{{ stats.total_usuarios }}</div>
                    </div>
                </div>
                <!-- Tarjeta: gemas en circulación -->
                <div class="col-md-3">
                    <div class="stat-card">
                        <div style="color:#64748b; font-size:0.85rem">GEMAS EN CIRCULACIÓN</div>
                        <div class="stat-number">{{ "{:,}".format(stats.total_gemas) }}</div>
                    </div>
                </div>
                <!-- Tarjeta: total wagered -->
                <div class="col-md-3">
                    <div class="stat-card">
                        <div style="color:#64748b; font-size:0.85rem">TOTAL APOSTADO</div>
                        <div class="stat-number">{{ "{:,}".format(stats.total_wagered) }}</div>
                    </div>
                </div>
                <!-- Tarjeta: partidas jugadas -->
                <div class="col-md-3">
                    <div class="stat-card">
                        <div style="color:#64748b; font-size:0.85rem">PARTIDAS JUGADAS</div>
                        <div class="stat-number">{{ stats.total_partidas }}</div>
                    </div>
                </div>
            </div>

            <!-- Últimas partidas -->
            <div class="card mb-4">
                <div class="card-header">🎲 Últimas 10 Partidas</div>
                <div class="card-body p-0">
                    <table class="table table-hover mb-0">
                        <thead><tr>
                            <th>Usuario</th><th>Juego</th><th>Apuesta</th>
                            <th>Resultado</th><th>Ganancia</th><th>Fecha</th>
                        </tr></thead>
                        <tbody>
                        {% for log in logs_juegos %}
                        <tr>
                            <td><code>{{ log.discord_id[:8] }}...</code></td>
                            <td>{{ log.game.capitalize() }}</td>
                            <td><span class="badge-gem">💎 {{ "{:,}".format(log.bet) }}</span></td>
                            <td>
                                {% if log.result == 'win' %}
                                    <span style="color:#6ee7b7">✅ Win</span>
                                {% elif log.result == 'lose' %}
                                    <span style="color:#fca5a5">❌ Lose</span>
                                {% else %}
                                    <span style="color:#fcd34d">🤝 Tie</span>
                                {% endif %}
                            </td>
                            <td style="color:{% if log.profit >= 0 %}#6ee7b7{% else %}#fca5a5{% endif %}">
                                {% if log.profit >= 0 %}+{% endif %}{{ "{:,}".format(log.profit) }}
                            </td>
                            <td style="color:#64748b">{{ log.timestamp[:16] }}</td>
                        </tr>
                        {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- ═══════════════════════════════════════════ -->
        <!-- SECCIÓN: CANALES                           -->
        <!-- ═══════════════════════════════════════════ -->
        <div id="sec-canales" class="section">
            <h4 class="mb-4"># Configuración de Canales</h4>
            <div class="card">
                <div class="card-header">Canales del Bot</div>
                <div class="card-body">
                    <p style="color:#94a3b8">
                        Introduce los <strong>IDs de canal</strong> de Discord.
                        Para obtenerlo: activa el <em>Modo Desarrollador</em> en Ajustes de Discord
                        y haz clic derecho en el canal → <em>Copiar ID</em>.
                    </p>
                    <!-- Formulario de canales -->
                    <form method="POST" action="/guardar_canales">
                        <div class="row g-3">
                            <!-- Canal de depósitos -->
                            <div class="col-md-6">
                                <label class="form-label">📥 Canal de Depósitos</label>
                                <input type="text" class="form-control" name="deposit_channel"
                                    value="{{ config.get('deposit_channel', '') }}"
                                    placeholder="ID del canal (ej: 1234567890)">
                            </div>
                            <!-- Canal de retiros -->
                            <div class="col-md-6">
                                <label class="form-label">📤 Canal de Retiros</label>
                                <input type="text" class="form-control" name="withdraw_channel"
                                    value="{{ config.get('withdraw_channel', '') }}"
                                    placeholder="ID del canal">
                            </div>
                            <!-- Canal de logs -->
                            <div class="col-md-6">
                                <label class="form-label">📋 Canal de Logs</label>
                                <input type="text" class="form-control" name="log_channel"
                                    value="{{ config.get('log_channel', '') }}"
                                    placeholder="ID del canal">
                            </div>
                            <!-- Canal de coinflip -->
                            <div class="col-md-6">
                                <label class="form-label">🪙 Canal de Coinflip</label>
                                <input type="text" class="form-control" name="coinflip_channel"
                                    value="{{ config.get('coinflip_channel', '') }}"
                                    placeholder="ID del canal">
                            </div>
                            <!-- Rol de agentes -->
                            <div class="col-md-6">
                                <label class="form-label">👮 ID del Rol de Agentes</label>
                                <input type="text" class="form-control" name="agent_role"
                                    value="{{ config.get('agent_role', '') }}"
                                    placeholder="ID del rol">
                                <div class="form-text" style="color:#64748b">
                                    Clic derecho en el rol → Copiar ID
                                </div>
                            </div>
                        </div>
                        <div class="mt-3">
                            <button type="submit" class="btn btn-primary">
                                <i class="bi bi-save"></i> Guardar Canales
                            </button>
                        </div>
                    </form>
                </div>
            </div>
        </div>

        <!-- ═══════════════════════════════════════════ -->
        <!-- SECCIÓN: AGENTES                           -->
        <!-- ═══════════════════════════════════════════ -->
        <div id="sec-agentes" class="section">
            <h4 class="mb-4">👮 Gestión de Agentes</h4>

            <!-- Formulario para añadir/actualizar agente -->
            <div class="card mb-4">
                <div class="card-header">Añadir o Actualizar Agente</div>
                <div class="card-body">
                    <form method="POST" action="/guardar_agente">
                        <div class="row g-3">
                            <div class="col-md-5">
                                <label class="form-label">ID de Discord del Agente</label>
                                <input type="text" class="form-control" name="agent_id"
                                    placeholder="ej: 987654321098765432" required>
                                <div class="form-text" style="color:#64748b">
                                    Clic derecho en el usuario → Copiar ID
                                </div>
                            </div>
                            <div class="col-md-5">
                                <label class="form-label">Límite de Gemas (máx. que puede procesar)</label>
                                <input type="number" class="form-control" name="limit"
                                    placeholder="ej: 10000000" min="1" required>
                            </div>
                            <div class="col-md-2 d-flex align-items-end">
                                <button type="submit" class="btn btn-primary w-100">
                                    <i class="bi bi-person-plus"></i> Guardar
                                </button>
                            </div>
                        </div>
                    </form>
                </div>
            </div>

            <!-- Lista de agentes actuales -->
            <div class="card">
                <div class="card-header">Agentes Registrados</div>
                <div class="card-body p-0">
                    <table class="table table-hover mb-0">
                        <thead><tr>
                            <th>Discord ID</th>
                            <th>Límite Total</th>
                            <th>Usado</th>
                            <th>Disponible</th>
                            <th>Acciones</th>
                        </tr></thead>
                        <tbody>
                        {% for agent in agentes %}
                        <tr>
                            <td><code>{{ agent.discord_id }}</code></td>
                            <td><span class="badge-gem">💎 {{ "{:,}".format(agent.limit_total) }}</span></td>
                            <td style="color:#fca5a5">{{ "{:,}".format(agent.limit_used) }}</td>
                            <td style="color:#6ee7b7">
                                {{ "{:,}".format(agent.limit_total - agent.limit_used) }}
                            </td>
                            <td>
                                <!-- Botón para resetear el límite usado -->
                                <form method="POST" action="/resetear_agente" style="display:inline">
                                    <input type="hidden" name="agent_id" value="{{ agent.discord_id }}">
                                    <button type="submit" class="btn btn-sm btn-outline-warning">
                                        <i class="bi bi-arrow-clockwise"></i> Reset
                                    </button>
                                </form>
                            </td>
                        </tr>
                        {% else %}
                        <tr><td colspan="5" style="color:#64748b; text-align:center">No hay agentes registrados</td></tr>
                        {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- ═══════════════════════════════════════════ -->
        <!-- SECCIÓN: HOUSE EDGE                        -->
        <!-- ═══════════════════════════════════════════ -->
        <div id="sec-houseedge" class="section">
            <h4 class="mb-4">🎲 House Edge por Juego</h4>
            <div class="card">
                <div class="card-header">Ventaja de la Casa (%)</div>
                <div class="card-body">
                    <p style="color:#94a3b8">
                        Porcentaje que retiene la casa en cada juego. 
                        <strong>0%</strong> = sin ventaja. <strong>5%</strong> = por defecto. 
                        Máximo recomendado: <strong>15%</strong>.
                    </p>
                    <form method="POST" action="/guardar_houseedge">
                        <div class="row g-3">
                            <!-- Un slider + input por cada juego -->
                            {% for juego, edge in house_edges.items() %}
                            <div class="col-md-6">
                                <label class="form-label">
                                    {% if juego == 'blackjack' %}🃏{% elif juego == 'dice' %}🎲
                                    {% elif juego == 'hilo' %}🎴{% elif juego == 'coinflip' %}🪙
                                    {% elif juego == 'mines' %}💣{% else %}🎰{% endif %}
                                    {{ juego.capitalize() }}
                                    <span id="val-{{ juego }}" style="color:#a78bfa">{{ edge }}%</span>
                                </label>
                                <!-- Slider visual + input numérico -->
                                <div class="d-flex gap-2 align-items-center">
                                    <input type="range" class="form-range flex-grow-1"
                                        min="0" max="25" step="0.5"
                                        value="{{ edge }}"
                                        oninput="document.getElementById('val-{{ juego }}').textContent=this.value+'%'; document.getElementById('num-{{ juego }}').value=this.value">
                                    <input type="number" id="num-{{ juego }}" name="{{ juego }}"
                                        class="form-control" style="width:70px"
                                        min="0" max="25" step="0.5" value="{{ edge }}">
                                </div>
                            </div>
                            {% endfor %}
                        </div>
                        <div class="mt-4">
                            <button type="submit" class="btn btn-primary">
                                <i class="bi bi-save"></i> Guardar House Edges
                            </button>
                        </div>
                    </form>
                </div>
            </div>
        </div>

        <!-- ═══════════════════════════════════════════ -->
        <!-- SECCIÓN: USUARIOS                          -->
        <!-- ═══════════════════════════════════════════ -->
        <div id="sec-usuarios" class="section">
            <h4 class="mb-4">👥 Usuarios Registrados</h4>

            <!-- Buscador rápido -->
            <div class="mb-3">
                <input type="text" class="form-control" id="buscar-usuario"
                    placeholder="🔍 Filtrar por ID o nombre Roblox..."
                    oninput="filtrarUsuarios()">
            </div>

            <div class="card">
                <div class="card-body p-0">
                    <table class="table table-hover mb-0" id="tabla-usuarios">
                        <thead><tr>
                            <th>Discord ID</th>
                            <th>Roblox</th>
                            <th>Balance</th>
                            <th>Total Apostado</th>
                            <th>Desde</th>
                            <th>Acciones</th>
                        </tr></thead>
                        <tbody>
                        {% for user in usuarios %}
                        <tr>
                            <td><code>{{ user.discord_id }}</code></td>
                            <td>{{ user.roblox_name }}</td>
                            <td><span class="badge-gem">💎 {{ "{:,}".format(user.balance) }}</span></td>
                            <td style="color:#a78bfa">{{ "{:,}".format(user.total_wagered) }}</td>
                            <td style="color:#64748b">{{ user.created_at[:10] if user.created_at else '—' }}</td>
                            <td>
                                <!-- Botón para modificar balance -->
                                <button class="btn btn-sm btn-outline-secondary"
                                    onclick="abrirModalBalance('{{ user.discord_id }}', {{ user.balance }})">
                                    <i class="bi bi-pencil"></i>
                                </button>
                            </td>
                        </tr>
                        {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- Modal para editar balance -->
            <div class="modal fade" id="modalBalance" tabindex="-1">
                <div class="modal-dialog">
                    <div class="modal-content" style="background:#1e2330; border:1px solid #2d3748">
                        <div class="modal-header" style="border-color:#2d3748">
                            <h5 class="modal-title">💎 Editar Balance</h5>
                            <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                        </div>
                        <form method="POST" action="/editar_balance">
                            <div class="modal-body">
                                <input type="hidden" id="modal-user-id" name="user_id">
                                <label class="form-label">Nuevo Balance (gemas)</label>
                                <input type="number" class="form-control" id="modal-balance"
                                    name="balance" min="0">
                            </div>
                            <div class="modal-footer" style="border-color:#2d3748">
                                <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancelar</button>
                                <button type="submit" class="btn btn-primary">Guardar</button>
                            </div>
                        </form>
                    </div>
                </div>
            </div>
        </div>

        <!-- ═══════════════════════════════════════════ -->
        <!-- SECCIÓN: ROLES DE WAGER                    -->
        <!-- ═══════════════════════════════════════════ -->
        <div id="sec-wagerroles" class="section">
            <h4 class="mb-4">🏆 Roles Automáticos por Wager</h4>

            <!-- Formulario para añadir rol -->
            <div class="card mb-4">
                <div class="card-header">Añadir Rol de Wager</div>
                <div class="card-body">
                    <form method="POST" action="/añadir_wager_rol">
                        <div class="row g-3">
                            <div class="col-md-5">
                                <label class="form-label">Gemas Apostadas Requeridas</label>
                                <input type="number" class="form-control" name="threshold"
                                    placeholder="ej: 1000000" min="1" required>
                            </div>
                            <div class="col-md-5">
                                <label class="form-label">ID del Rol a Asignar</label>
                                <input type="text" class="form-control" name="role_id"
                                    placeholder="ID del rol de Discord" required>
                            </div>
                            <div class="col-md-2 d-flex align-items-end">
                                <button type="submit" class="btn btn-primary w-100">
                                    <i class="bi bi-plus-circle"></i> Añadir
                                </button>
                            </div>
                        </div>
                    </form>
                </div>
            </div>

            <!-- Lista de roles actuales -->
            <div class="card">
                <div class="card-header">Roles Configurados</div>
                <div class="card-body p-0">
                    <table class="table table-hover mb-0">
                        <thead><tr>
                            <th>Gemas Requeridas</th>
                            <th>ID del Rol</th>
                            <th>Acciones</th>
                        </tr></thead>
                        <tbody>
                        {% for rol in wager_roles %}
                        <tr>
                            <td><span class="badge-gem">💎 {{ "{:,}".format(rol.threshold) }}</span></td>
                            <td><code>{{ rol.role_id }}</code></td>
                            <td>
                                <form method="POST" action="/eliminar_wager_rol" style="display:inline">
                                    <input type="hidden" name="role_id" value="{{ rol.role_id }}">
                                    <button type="submit" class="btn btn-sm btn-outline-danger">
                                        <i class="bi bi-trash"></i> Eliminar
                                    </button>
                                </form>
                            </td>
                        </tr>
                        {% else %}
                        <tr><td colspan="3" style="color:#64748b; text-align:center">No hay roles configurados</td></tr>
                        {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- ═══════════════════════════════════════════ -->
        <!-- SECCIÓN: LOGS                              -->
        <!-- ═══════════════════════════════════════════ -->
        <div id="sec-logs" class="section">
            <h4 class="mb-4">📋 Logs del Sistema</h4>

            <!-- Pestañas: juegos / transacciones -->
            <ul class="nav nav-tabs mb-3" id="logTabs">
                <li class="nav-item">
                    <a class="nav-link active" href="#" onclick="mostrarLog('juegos')" style="color:#a78bfa">
                        🎲 Juegos
                    </a>
                </li>
                <li class="nav-item">
                    <a class="nav-link" href="#" onclick="mostrarLog('transacciones')" style="color:#94a3b8">
                        💳 Transacciones
                    </a>
                </li>
            </ul>

            <!-- Log de juegos -->
            <div id="log-juegos" class="card">
                <div class="card-body p-0">
                    <table class="table table-hover mb-0">
                        <thead><tr>
                            <th>Usuario</th><th>Juego</th><th>Apuesta</th>
                            <th>Resultado</th><th>Profit</th><th>Fecha</th>
                        </tr></thead>
                        <tbody>
                        {% for log in todos_logs_juegos %}
                        <tr>
                            <td><code>{{ log.discord_id[:10] }}...</code></td>
                            <td>{{ log.game.capitalize() }}</td>
                            <td><span class="badge-gem">💎 {{ "{:,}".format(log.bet) }}</span></td>
                            <td>
                                {% if log.result == 'win' %}
                                    <span style="color:#6ee7b7">✅ Win</span>
                                {% elif log.result == 'lose' %}
                                    <span style="color:#fca5a5">❌ Lose</span>
                                {% else %}
                                    <span style="color:#fcd34d">🤝 Tie</span>
                                {% endif %}
                            </td>
                            <td style="color:{% if log.profit >= 0 %}#6ee7b7{% else %}#fca5a5{% endif %}">
                                {% if log.profit >= 0 %}+{% endif %}{{ "{:,}".format(log.profit) }}
                            </td>
                            <td style="color:#64748b">{{ log.timestamp[:16] }}</td>
                        </tr>
                        {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- Log de transacciones (oculto por defecto) -->
            <div id="log-transacciones" class="card" style="display:none">
                <div class="card-body p-0">
                    <table class="table table-hover mb-0">
                        <thead><tr>
                            <th>Usuario</th><th>Tipo</th><th>Cantidad</th>
                            <th>Estado</th><th>Agente</th><th>Fecha</th>
                        </tr></thead>
                        <tbody>
                        {% for tx in transacciones %}
                        <tr>
                            <td><code>{{ tx.discord_id[:10] }}...</code></td>
                            <td>
                                {% if tx.type == 'deposit' %}
                                    <span style="color:#6ee7b7">📥 Depósito</span>
                                {% else %}
                                    <span style="color:#fca5a5">📤 Retiro</span>
                                {% endif %}
                            </td>
                            <td><span class="badge-gem">💎 {{ "{:,}".format(tx.amount) }}</span></td>
                            <td>
                                {% if tx.status == 'confirmed' %}
                                    <span style="color:#6ee7b7">✅ Confirmado</span>
                                {% elif tx.status == 'pending' %}
                                    <span style="color:#fcd34d">⏳ Pendiente</span>
                                {% else %}
                                    <span style="color:#fca5a5">❌ Rechazado</span>
                                {% endif %}
                            </td>
                            <td><code>{{ tx.agent_id or '—' }}</code></td>
                            <td style="color:#64748b">{{ tx.timestamp[:16] if tx.timestamp else '—' }}</td>
                        </tr>
                        {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

    </div><!-- fin col contenido -->
</div><!-- fin row -->
</div><!-- fin container -->

<!-- Bootstrap JS -->
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
<script>
// ── Navegación entre secciones ────────────────────────────
function showSection(nombre) {
    // Oculta todas las secciones
    document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
    // Muestra la sección elegida
    document.getElementById('sec-' + nombre).classList.add('active');
    // Actualiza el link activo en el sidebar
    document.querySelectorAll('.sidebar .nav-link').forEach(l => l.classList.remove('active'));
    event.target.classList.add('active');
}

// ── Modal de edición de balance ───────────────────────────
function abrirModalBalance(userId, balanceActual) {
    document.getElementById('modal-user-id').value = userId;
    document.getElementById('modal-balance').value = balanceActual;
    new bootstrap.Modal(document.getElementById('modalBalance')).show();
}

// ── Filtro de usuarios ────────────────────────────────────
function filtrarUsuarios() {
    const texto = document.getElementById('buscar-usuario').value.toLowerCase();
    document.querySelectorAll('#tabla-usuarios tbody tr').forEach(fila => {
        const contenido = fila.textContent.toLowerCase();
        fila.style.display = contenido.includes(texto) ? '' : 'none';
    });
}

// ── Alternar entre tabs de logs ───────────────────────────
function mostrarLog(tipo) {
    document.getElementById('log-juegos').style.display        = tipo === 'juegos' ? 'block' : 'none';
    document.getElementById('log-transacciones').style.display = tipo === 'transacciones' ? 'block' : 'none';
}
</script>
</body>
</html>
"""

# ══════════════════════════════════════════════════════════════
# RUTAS DE FLASK
# ══════════════════════════════════════════════════════════════

@app.route("/")
def index():
    """Página principal — carga todos los datos para mostrar."""
    mensaje = request.args.get("msg", "")   # Mensaje de confirmación tras guardar

    # ── Estadísticas generales ────────────────────────────
    async def get_stats():
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            # Total de usuarios
            c = await db.execute("SELECT COUNT(*) FROM users")
            total_usuarios = (await c.fetchone())[0]
            # Total de gemas en circulación
            c = await db.execute("SELECT COALESCE(SUM(balance), 0) FROM users")
            total_gemas = (await c.fetchone())[0]
            # Total apostado históricamente
            c = await db.execute("SELECT COALESCE(SUM(total_wagered), 0) FROM users")
            total_wagered = (await c.fetchone())[0]
            # Total de partidas
            c = await db.execute("SELECT COUNT(*) FROM game_logs")
            total_partidas = (await c.fetchone())[0]
            return {
                "total_usuarios": total_usuarios,
                "total_gemas": total_gemas,
                "total_wagered": total_wagered,
                "total_partidas": total_partidas
            }

    stats               = run_query(get_stats())
    logs_juegos         = run_query(db_fetchall("SELECT * FROM game_logs ORDER BY timestamp DESC LIMIT 10"))
    todos_logs_juegos   = run_query(db_fetchall("SELECT * FROM game_logs ORDER BY timestamp DESC LIMIT 50"))
    transacciones       = run_query(db_fetchall("SELECT * FROM transactions ORDER BY timestamp DESC LIMIT 50"))
    usuarios            = run_query(db_fetchall("SELECT * FROM users ORDER BY balance DESC"))
    agentes             = run_query(db_fetchall("SELECT * FROM agents"))
    wager_roles         = run_query(db_fetchall("SELECT * FROM wager_roles ORDER BY threshold ASC"))

    # Carga la configuración actual de canales y roles
    async def get_config():
        keys = ["deposit_channel","withdraw_channel","log_channel","coinflip_channel","agent_role"]
        result = {}
        for key in keys:
            row = await db_fetchone("SELECT value FROM config WHERE key = ?", (key,))
            result[key] = row["value"] if row else ""
        return result

    config = run_query(get_config())

    # Carga el house edge de cada juego
    async def get_edges():
        games = ["blackjack", "dice", "hilo", "coinflip", "mines", "keno"]
        result = {}
        for game in games:
            row = await db_fetchone("SELECT edge_pct FROM house_edge WHERE game = ?", (game,))
            result[game] = row["edge_pct"] if row else 5.0
        return result

    house_edges = run_query(get_edges())

    return render_template_string(
        HTML_TEMPLATE,
        mensaje=mensaje,
        stats=stats,
        logs_juegos=logs_juegos,
        todos_logs_juegos=todos_logs_juegos,
        transacciones=transacciones,
        usuarios=usuarios,
        agentes=agentes,
        wager_roles=wager_roles,
        config=config,
        house_edges=house_edges
    )

# ── Guardar canales ───────────────────────────────────────────
@app.route("/guardar_canales", methods=["POST"])
def guardar_canales():
    """Guarda los IDs de canal en la base de datos."""
    fields = ["deposit_channel", "withdraw_channel", "log_channel", "coinflip_channel", "agent_role"]
    for field in fields:
        value = request.form.get(field, "").strip()
        if value:                                   # Solo guarda si hay un valor
            run_query(db_execute(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                (field, value)
            ))
    return redirect("/?msg=Canales+guardados+correctamente")

# ── Guardar agente ────────────────────────────────────────────
@app.route("/guardar_agente", methods=["POST"])
def guardar_agente():
    """Añade o actualiza el límite de un agente."""
    agent_id = request.form.get("agent_id", "").strip()
    limit    = int(request.form.get("limit", 0))
    if agent_id and limit > 0:
        run_query(db_execute(
            """INSERT INTO agents (discord_id, limit_total, limit_used)
               VALUES (?, ?, 0)
               ON CONFLICT(discord_id) DO UPDATE SET limit_total = ?""",
            (agent_id, limit, limit)
        ))
    return redirect("/?msg=Agente+guardado+correctamente#agentes")

# ── Resetear agente ───────────────────────────────────────────
@app.route("/resetear_agente", methods=["POST"])
def resetear_agente():
    """Pone a 0 el límite usado de un agente (recarga su capacidad)."""
    agent_id = request.form.get("agent_id", "")
    run_query(db_execute(
        "UPDATE agents SET limit_used = 0 WHERE discord_id = ?", (agent_id,)
    ))
    return redirect("/?msg=Límite+del+agente+reseteado")

# ── Guardar house edge ────────────────────────────────────────
@app.route("/guardar_houseedge", methods=["POST"])
def guardar_houseedge():
    """Actualiza el house edge de todos los juegos."""
    games = ["blackjack", "dice", "hilo", "coinflip", "mines", "keno"]
    for game in games:
        value = request.form.get(game)
        if value is not None:
            run_query(db_execute(
                "INSERT OR REPLACE INTO house_edge (game, edge_pct) VALUES (?, ?)",
                (game, float(value))
            ))
    return redirect("/?msg=House+edges+guardados")

# ── Editar balance ────────────────────────────────────────────
@app.route("/editar_balance", methods=["POST"])
def editar_balance():
    """Cambia el balance de un usuario directamente."""
    user_id     = request.form.get("user_id")
    new_balance = int(request.form.get("balance", 0))
    run_query(db_execute(
        "UPDATE users SET balance = ? WHERE discord_id = ?",
        (new_balance, user_id)
    ))
    return redirect("/?msg=Balance+actualizado")

# ── Añadir rol de wager ───────────────────────────────────────
@app.route("/añadir_wager_rol", methods=["POST"])
def añadir_wager_rol():
    """Añade un nuevo rol automático por cantidad de wager."""
    threshold = int(request.form.get("threshold", 0))
    role_id   = request.form.get("role_id", "").strip()
    if threshold > 0 and role_id:
        run_query(db_execute(
            "INSERT INTO wager_roles (threshold, role_id) VALUES (?, ?)",
            (threshold, role_id)
        ))
    return redirect("/?msg=Rol+de+wager+añadido")

# ── Eliminar rol de wager ─────────────────────────────────────
@app.route("/eliminar_wager_rol", methods=["POST"])
def eliminar_wager_rol():
    """Elimina un rol del sistema de wager."""
    role_id = request.form.get("role_id")
    run_query(db_execute("DELETE FROM wager_roles WHERE role_id = ?", (role_id,)))
    return redirect("/?msg=Rol+eliminado")


# ── Inicio del servidor ───────────────────────────────────────
if __name__ == "__main__":
    print("🌐 Panel web iniciado en: http://localhost:5000")
    print("⚠️  Solo accesible desde tu PC (localhost)")
    print("   Presiona Ctrl+C para detenerlo\n")
    app.run(
        host="127.0.0.1",               # Solo localhost, NO accesible desde internet
        port=5000,                       # Puerto del navegador
        debug=False                      # Debug OFF en producción
    )
