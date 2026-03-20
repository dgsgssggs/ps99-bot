# 🎰 PS99 Gambling Bot — Guía Completa

Bot de Discord para gambling de gemas de Pet Simulator 99, con sistema económico completo, agentes, tickets y 6 juegos.

---

## 📁 Estructura del Proyecto

```
ps99-bot/
├── main.py              ← Punto de entrada del bot
├── database.py          ← Base de datos SQLite (toda la lógica de datos)
├── utils.py             ← Funciones de utilidad compartidas
├── requirements.txt     ← Librerías necesarias
├── .env.example         ← Plantilla de configuración
├── ps99_bot.db          ← Se crea automáticamente al iniciar
└── cogs/
    ├── economy.py       ← /link /balance /deposit /withdraw
    ├── admin.py         ← /sethouseedge /setagentlimit etc.
    ├── logs_cog.py      ← /logs
    └── games/
        ├── blackjack.py ← Juego Blackjack
        ├── dice.py      ← Juego Dados
        ├── hilo.py      ← Juego Hi-Lo
        ├── coinflip.py  ← Juego Coinflip
        ├── mines.py     ← Juego Minas
        └── keno.py      ← Juego Keno
```

---

## ⚙️ Instalación Paso a Paso

### 1. Requisitos previos
- Python 3.10 o superior
- Una cuenta de Discord Developer

### 2. Crear el bot en Discord
1. Ve a https://discord.com/developers/applications
2. Haz clic en **New Application** y ponle un nombre
3. Ve a **Bot** → **Add Bot**
4. En **Privileged Gateway Intents**, activa:
   - ✅ `SERVER MEMBERS INTENT`
   - ✅ `MESSAGE CONTENT INTENT`
5. Copia el **Token** del bot

### 3. Invitar el bot al servidor
1. Ve a **OAuth2** → **URL Generator**
2. En Scopes selecciona: `bot` y `applications.commands`
3. En Bot Permissions selecciona: `Administrator`
4. Copia la URL generada y ábrela en el navegador

### 4. Configurar el proyecto
```bash
# Clona o descarga el proyecto
# Entra a la carpeta
cd ps99-bot

# Instala las dependencias
pip install -r requirements.txt

# Copia el .env.example y edítalo
cp .env.example .env
```

### 5. Editar el archivo `.env`
```env
DISCORD_TOKEN=aqui_tu_token_del_bot
OWNER_ID=aqui_tu_id_de_discord
```

> Para conseguir tu ID de Discord: **Ajustes → Avanzado → Modo Desarrollador** activado.
> Luego haz clic derecho en tu nombre → **Copiar ID de usuario**.

### 6. Iniciar el bot
```bash
python main.py
```

---

## 🔧 Configuración Inicial (en Discord)

Una vez el bot esté corriendo, usa estos comandos como owner:

```
/setchannel tipo:deposit  canal:#depositos
/setchannel tipo:withdraw canal:#retiros
/setchannel tipo:log      canal:#logs-bot
/setchannel tipo:coinflip canal:#coinflip
/setagentrole rol:@Agente
```

### Asignar agentes
```
/setagentlimit agente:@Usuario cantidad:10000000
```

### Configurar roles de wager
```
/addwagerrole cantidad:1000000  rol:@Bronze
/addwagerrole cantidad:10000000 rol:@Silver
/addwagerrole cantidad:100000000 rol:@Gold
```

### Ajustar house edge (opcional)
```
/sethouseedge juego:blackjack porcentaje:3
/sethouseedge juego:dice      porcentaje:4
/sethouseedge juego:coinflip  porcentaje:2
```

---

## 🎮 Comandos de Jugadores

| Comando | Descripción |
|---------|-------------|
| `/link <usuario_roblox>` | Vincula tu cuenta de Roblox |
| `/balance` | Ver tus gemas actuales |
| `/deposit <cantidad>` | Solicitar un depósito |
| `/withdraw <cantidad>` | Solicitar un retiro |
| `/blackjack <apuesta>` | Jugar Blackjack |
| `/dice <apuesta>` | Jugar Dados |
| `/hilo <apuesta>` | Jugar Hi-Lo |
| `/coinflip <apuesta> <cara/cruz>` | Jugar Coinflip |
| `/mines <apuesta> [minas]` | Jugar Minas |
| `/keno <apuesta> <numeros>` | Jugar Keno |
| `/logs [usuario] [games/deposits]` | Ver historial |

---

## 👮 Comandos de Agentes

Los agentes confirman depósitos y retiros haciendo clic en los botones de los tickets.
- ✅ **Confirmar Depósito** — Solo si tienen límite suficiente
- ❌ **Rechazar** — Cancela la solicitud

---

## 👑 Comandos de Owner

| Comando | Descripción |
|---------|-------------|
| `/sethouseedge <juego> <pct>` | House edge de un juego |
| `/setagentlimit <agente> <cantidad>` | Límite de depósitos a agente |
| `/resetagent <agente>` | Resetea el límite usado |
| `/agentstatus <agente>` | Ver estado del agente |
| `/setchannel <tipo> <canal>` | Configura canales |
| `/setagentrole <rol>` | Rol de agentes |
| `/addwagerrole <cantidad> <rol>` | Añadir rol automático |
| `/removewagerrole <rol>` | Eliminar rol de wager |
| `/setbalance <usuario> <cantidad>` | Forzar balance |
| `/houseedges` | Ver todos los house edges |

---

## 🎲 Reglas de los Juegos

### 🃏 Blackjack
- Contra la banca (Hit o Stand)
- Blackjack natural paga **1.5x**
- La banca siempre pide carta con 16 o menos

### 🎲 Dados
- Número exacto: paga **5x** (menos house edge)
- Alto/Bajo: paga **~2x**

### 🎴 Hi-Lo
- Adivina si la siguiente carta es mayor o menor
- Multiplicador sube con cada acierto
- Cobra en cualquier momento con el botón Cobrar

### 🪙 Coinflip
- Solo en el canal de coinflip configurado
- 50/50, paga **2x** (menos house edge)

### 💣 Minas
- Grid 5x5 con minas escondidas
- Multiplicador progresivo por cada casilla segura
- Cobra antes de explotar

### 🎰 Keno
- Elige entre 1 y 10 números del 1 al 40
- Se sortean 20 números
- Pago según tabla de aciertos

---

## 🔒 Notas de Seguridad

- Los tokens nunca se comparten ni suben a repositorios
- El `.env` está en `.gitignore`
- Las apuestas se descuentan **antes** de jugar (anti-exploit)
- Los agentes tienen límites configurables por el owner
- Los retiros descuentan el saldo inmediatamente y lo devuelven si se rechazan

---

## 💬 Soporte

Si tienes preguntas o encuentras bugs, revisa que:
1. El archivo `.env` tiene los valores correctos
2. El bot tiene permisos de `Administrator` en el servidor
3. Los intents están activados en el portal de desarrolladores
4. Python es versión 3.10+
