import os
import json
import logging
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
import anthropic
import httpx
from system_prompt import get_system_prompt

# ─────────────────────────────────────────
# CONFIG — pon tus keys aquí
# ─────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# ─────────────────────────────────────────
# ARCHIVOS DE MEMORIA
# ─────────────────────────────────────────
MEMORIA_ARCHIVO = "memoria.json"
HISTORIAL_ARCHIVO = "historial.json"
MAX_HISTORIAL = 20

# ─────────────────────────────────────────
# SETUP
# ─────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


# ─────────────────────────────────────────
# FUNCIONES DE MEMORIA
# ─────────────────────────────────────────

def cargar_memoria():
    if os.path.exists(MEMORIA_ARCHIVO):
        with open(MEMORIA_ARCHIVO, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"hechos": [], "ultima_actualizacion": ""}


def guardar_memoria(memoria):
    memoria["ultima_actualizacion"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(MEMORIA_ARCHIVO, "w", encoding="utf-8") as f:
        json.dump(memoria, f, ensure_ascii=False, indent=2)


def agregar_hecho(hecho: str):
    memoria = cargar_memoria()
    memoria["hechos"].append({
        "hecho": hecho,
        "fecha": datetime.now().strftime("%Y-%m-%d")
    })
    guardar_memoria(memoria)


def formatear_memoria():
    memoria = cargar_memoria()
    if not memoria["hechos"]:
        return "Sin hechos guardados aún."
    lineas = []
    for item in memoria["hechos"]:
        lineas.append(f"- [{item['fecha']}] {item['hecho']}")
    return "\n".join(lineas)


def cargar_historial():
    if os.path.exists(HISTORIAL_ARCHIVO):
        with open(HISTORIAL_ARCHIVO, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def guardar_historial(historial):
    historial_recortado = historial[-MAX_HISTORIAL:]
    with open(HISTORIAL_ARCHIVO, "w", encoding="utf-8") as f:
        json.dump(historial_recortado, f, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────
# FUNCIÓN GEMINI — EL CONSEJERO
# ─────────────────────────────────────────

async def consultar_gemini(pregunta_original: str, respuesta_claude: str) -> str:
    """
    Gemini actúa como consejero: revisa la respuesta de Claude
    y solo habla si tiene algo relevante que agregar o corregir.
    """
    prompt_consejero = f"""Eres un consejero inteligente. Claude (el cerebro principal) ya respondió una pregunta.
Tu trabajo es revisar esa respuesta y decidir:

1. Si la respuesta de Claude es completa y correcta → responde SOLO con: [SIN_ADICION]
2. Si hay algo importante que matizar, corregir o agregar → responde con UN párrafo corto y directo con esa adición.

No repitas lo que ya dijo Claude. Solo agrega valor real o calla.
Responde siempre en español.

PREGUNTA ORIGINAL DE DIEGO:
{pregunta_original}

RESPUESTA DE CLAUDE:
{respuesta_claude}

¿Tienes algo relevante que agregar?"""

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{"parts": [{"text": prompt_consejero}]}],
            "generationConfig": {"maxOutputTokens": 300, "temperature": 0.7}
        }
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(url, json=payload)
            data = response.json()
            texto = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            if "[SIN_ADICION]" in texto:
                return ""
            return texto
    except Exception as e:
        logging.error(f"Error Gemini: {e}")
        return ""


# ─────────────────────────────────────────
# HANDLERS
# ─────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Cortana en línea.\n\n"
        "Estoy aquí, Diego. ¿Por dónde empezamos?\n\n"
        "Comandos disponibles:\n"
        "/recuerda [hecho] — Guardo algo permanentemente\n"
        "/memoria — Te muestro todo lo que recuerdo\n"
        "/olvida [número] — Borro un hecho específico\n"
        "/reset — Limpio la conversación activa"
    )


async def recuerda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = " ".join(context.args)
    if not texto:
        await update.message.reply_text("Dime qué guardar. Ejemplo:\n/recuerda Conseguí cliente nuevo: Marca XYZ")
        return
    agregar_hecho(texto)
    await update.message.reply_text(f"Guardado en memoria permanente:\n— {texto}")


async def ver_memoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memoria = cargar_memoria()
    if not memoria["hechos"]:
        await update.message.reply_text("No hay nada guardado en memoria aún.")
        return
    texto = "📁 Memoria de Cortana:\n\n"
    for i, item in enumerate(memoria["hechos"], 1):
        texto += f"{i}. [{item['fecha']}] {item['hecho']}\n"
    await update.message.reply_text(texto)


async def olvida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Indica el número del hecho. Ejemplo:\n/olvida 3")
        return
    try:
        numero = int(context.args[0]) - 1
        memoria = cargar_memoria()
        if numero < 0 or numero >= len(memoria["hechos"]):
            await update.message.reply_text("Ese número no existe. Usa /memoria para ver la lista.")
            return
        hecho_borrado = memoria["hechos"].pop(numero)
        guardar_memoria(memoria)
        await update.message.reply_text(f"Borrado de memoria:\n— {hecho_borrado['hecho']}")
    except ValueError:
        await update.message.reply_text("Usa un número. Ejemplo:\n/olvida 2")


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    guardar_historial([])
    await update.message.reply_text("Conversación reiniciada. La memoria permanente sigue intacta.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    historial = cargar_historial()

    historial.append({"role": "user", "content": user_text})

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing"
    )

    try:
        # ── CLAUDE responde primero (el rey) ──
        system = get_system_prompt(formatear_memoria())
        response = claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            system=system,
            messages=historial[-MAX_HISTORIAL:]
        )
        respuesta_claude = response.content[0].text

        # ── GEMINI revisa como consejero ──
        adicion_gemini = await consultar_gemini(user_text, respuesta_claude)

        # ── Construir respuesta final ──
        if adicion_gemini:
            respuesta_final = f"{respuesta_claude}\n\n〔Consejero〕 {adicion_gemini}"
        else:
            respuesta_final = respuesta_claude

        historial.append({"role": "assistant", "content": respuesta_claude})
        guardar_historial(historial)

        await update.message.reply_text(respuesta_final)

    except Exception as e:
        logging.error(f"Error: {e}")
        await update.message.reply_text("Algo falló. Intenta de nuevo.")


# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────

if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("recuerda", recuerda))
    app.add_handler(CommandHandler("memoria", ver_memoria))
    app.add_handler(CommandHandler("olvida", olvida))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Cortana en línea — cerebro doble activo...")
    app.run_polling()
