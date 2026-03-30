import os
import json
import base64
import logging
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
import anthropic
import httpx
from system_prompt import get_system_prompt

# ─────────────────────────────────────────
# CONFIG
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
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
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
# GEMINI — TRANSCRIPCIÓN DE AUDIO/VOZ
# ─────────────────────────────────────────

async def transcribir_audio_gemini(audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{"parts": [
                {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(audio_bytes).decode()}},
                {"text": "Transcribe exactamente lo que se dice en este audio. Solo la transcripción, sin comentarios."}
            ]}],
            "generationConfig": {"maxOutputTokens": 500, "temperature": 0.2}
        }
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, json=payload)
            data = r.json()
            logging.info(f"Gemini audio response: {data}")  # <-- línea nueva
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        logging.error(f"Error transcribiendo audio: {e}")
        return ""


# ─────────────────────────────────────────
# GEMINI — DESCRIPCIÓN DE VIDEO
# ─────────────────────────────────────────

async def describir_video_gemini(video_bytes: bytes, mime_type: str = "video/mp4") -> str:
    """
    Envía video a Gemini y devuelve una descripción de lo que ocurre.
    Solo funciona bien con videos cortos (< 1 min recomendado).
    """
    try:
        video_b64 = base64.b64encode(video_bytes).decode()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": video_b64
                        }
                    },
                    {
                        "text": "Describe detalladamente lo que ocurre en este video. Sé específico sobre personas, acciones, ambiente, texto visible y cualquier detalle relevante. Responde en español."
                    }
                ]
            }],
            "generationConfig": {"maxOutputTokens": 600, "temperature": 0.4}
        }
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(url, json=payload)
            data = response.json()
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        logging.error(f"Error describiendo video: {e}")
        return ""


# ─────────────────────────────────────────
# FUNCIÓN CENTRAL — CLAUDE RESPONDE
# ─────────────────────────────────────────

async def responder_con_claude(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                messages_payload: list, texto_para_historial: str):
    """
    Llama a Claude con el payload construido, agrega consejero Gemini
    y devuelve la respuesta final al usuario.
    """
    historial = cargar_historial()
    system = get_system_prompt(formatear_memoria())

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing"
    )

    try:
        response = claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            system=system,
            messages=historial[-MAX_HISTORIAL:] + messages_payload
        )
        respuesta_claude = response.content[0].text

        # Gemini como consejero
        adicion_gemini = await consultar_gemini(texto_para_historial, respuesta_claude)

        respuesta_final = (
            f"{respuesta_claude}\n\n〔Consejero〕 {adicion_gemini}"
            if adicion_gemini else respuesta_claude
        )

        # Guardar en historial como texto plano
        historial.append({"role": "user", "content": texto_para_historial})
        historial.append({"role": "assistant", "content": respuesta_claude})
        guardar_historial(historial)

        await update.message.reply_text(respuesta_final)

    except Exception as e:
        logging.error(f"Error Claude: {e}")
        await update.message.reply_text("Algo falló. Intenta de nuevo.")


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


# ── TEXTO ──
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    await responder_con_claude(
        update, context,
        messages_payload=[{"role": "user", "content": user_text}],
        texto_para_historial=user_text
    )


# ── IMAGEN ── (Claude Vision nativo)
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caption = update.message.caption or "¿Qué ves en esta imagen? Descríbela y comenta lo que consideres relevante para mí."

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # Descargar imagen en memoria
    photo = update.message.photo[-1]  # mayor resolución
    file = await context.bot.get_file(photo.file_id)
    photo_bytes = await file.download_as_bytearray()
    photo_b64 = base64.b64encode(photo_bytes).decode()

    # Payload con visión para Claude
    mensaje_con_imagen = {
        "role": "user",
        "content": [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": photo_b64
                }
            },
            {"type": "text", "text": caption}
        ]
    }

    texto_historial = f"[Imagen enviada] {caption}"
    await responder_con_claude(update, context, [mensaje_con_imagen], texto_historial)


# ── VOZ ── (Gemini transcribe → Claude responde)
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    voice = update.message.voice
    file = await context.bot.get_file(voice.file_id)
    audio_bytes = await file.download_as_bytearray()

    await update.message.reply_text("🎙️ Escuchando...")

    transcripcion = await transcribir_audio_gemini(bytes(audio_bytes), mime_type="audio/ogg")

    if not transcripcion:
        await update.message.reply_text("No pude entender el audio. Intenta de nuevo o escríbelo.")
        return

    await update.message.reply_text(f"📝 Entendí: _{transcripcion}_", parse_mode="Markdown")

    # Claude responde a la transcripción
    await responder_con_claude(
        update, context,
        messages_payload=[{"role": "user", "content": transcripcion}],
        texto_para_historial=f"[Audio transcrito] {transcripcion}"
    )


# ── AUDIO (archivos de música/audio enviados como archivo) ──
async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    audio = update.message.audio
    file = await context.bot.get_file(audio.file_id)
    audio_bytes = await file.download_as_bytearray()

    mime = audio.mime_type or "audio/mpeg"
    await update.message.reply_text("🎵 Procesando audio...")

    transcripcion = await transcribir_audio_gemini(bytes(audio_bytes), mime_type=mime)

    if not transcripcion:
        await update.message.reply_text("No pude procesar este audio.")
        return

    await update.message.reply_text(f"📝 Contenido del audio:\n_{transcripcion}_", parse_mode="Markdown")
    await responder_con_claude(
        update, context,
        messages_payload=[{"role": "user", "content": transcripcion}],
        texto_para_historial=f"[Audio transcrito] {transcripcion}"
    )


# ── VIDEO ── (Gemini describe → Claude responde)
async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caption = update.message.caption or ""
    video = update.message.video

    # Advertir si el video es muy pesado (Telegram limita a ~50MB en bots)
    if video.file_size and video.file_size > 20 * 1024 * 1024:
        await update.message.reply_text(
            "⚠️ El video es muy pesado para procesarlo en línea. "
            "Envíame uno de menos de 20MB o cuéntame qué hay en él."
        )
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    await update.message.reply_text("🎬 Analizando video...")

    file = await context.bot.get_file(video.file_id)
    video_bytes = await file.download_as_bytearray()

    mime = video.mime_type or "video/mp4"
    descripcion = await describir_video_gemini(bytes(video_bytes), mime_type=mime)

    if not descripcion:
        await update.message.reply_text("No pude analizar el video.")
        return

    contexto_completo = f"{caption}\n\nGemini analizó el video que te mandé y detectó: {descripcion}. Responde sobre el contenido del video.".strip() if caption else 

    await responder_con_claude(
        update, context,
        messages_payload=[{"role": "user", "content": contexto_completo}],
        texto_para_historial=f"[Video enviado] {contexto_completo}"
    )


# ── VIDEO NOTE (video circulito de Telegram) ──
async def handle_video_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    await update.message.reply_text("🎬 Analizando video...")

    video_note = update.message.video_note
    file = await context.bot.get_file(video_note.file_id)
    video_bytes = await file.download_as_bytearray()

    descripcion = await describir_video_gemini(bytes(video_bytes), mime_type="video/mp4")

    if not descripcion:
        await update.message.reply_text("No pude analizar el video.")
        return

    await responder_con_claude(
        update, context,
        messages_payload=[{"role": "user", "content": f"Descripción del video: {descripcion}"}],
        texto_para_historial=f"[Video circular] {descripcion}"
    )


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

    # Media handlers
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video))
    app.add_handler(MessageHandler(filters.VIDEO_NOTE, handle_video_note))

    # Texto al final (siempre)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Cortana en línea — multimedia activo...")
    app.run_polling()
