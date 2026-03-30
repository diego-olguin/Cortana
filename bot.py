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

from dotenv import load_dotenv
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

MEMORIA_ARCHIVO = "memoria.json"
HISTORIAL_ARCHIVO = "historial.json"
MAX_HISTORIAL = 20

logging.basicConfig(level=logging.INFO)
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


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
        return "Sin hechos guardados aun."
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


async def consultar_gemini(pregunta_original: str, respuesta_claude: str) -> str:
    prompt_consejero = f"""Eres un consejero inteligente. Claude (el cerebro principal) ya respondio una pregunta.
Tu trabajo es revisar esa respuesta y decidir:

1. Si la respuesta de Claude es completa y correcta responde SOLO con: [SIN_ADICION]
2. Si hay algo importante que matizar, corregir o agregar responde con UN parrafo corto y directo con esa adicion.

No repitas lo que ya dijo Claude. Solo agrega valor real o calla.
Responde siempre en espanol.

PREGUNTA ORIGINAL DE DIEGO:
{pregunta_original}

RESPUESTA DE CLAUDE:
{respuesta_claude}

Tienes algo relevante que agregar?"""

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
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


async def transcribir_audio_gemini(audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{"parts": [
                {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(audio_bytes).decode()}},
                {"text": "Transcribe exactamente lo que se dice en este audio. Solo la transcripcion, sin comentarios."}
            ]}],
            "generationConfig": {"maxOutputTokens": 500, "temperature": 0.2}
        }
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, json=payload)
            data = r.json()
            logging.info(f"Gemini audio response: {data}")
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        logging.error(f"Error transcribiendo audio: {e}")
        return ""


async def describir_video_gemini(video_bytes: bytes, mime_type: str = "video/mp4") -> str:
    try:
        video_b64 = base64.b64encode(video_bytes).decode()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{
                "parts": [
                    {"inline_data": {"mime_type": mime_type, "data": video_b64}},
                    {"text": "Describe detalladamente lo que ocurre en este video. Se especifico sobre personas, acciones, ambiente, texto visible y cualquier detalle relevante. Responde en espanol."}
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


async def responder_con_claude(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                messages_payload: list, texto_para_historial: str):
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

        adicion_gemini = await consultar_gemini(texto_para_historial, respuesta_claude)

        if adicion_gemini:
            respuesta_final = f"{respuesta_claude}\n\n[Consejero] {adicion_gemini}"
        else:
            respuesta_final = respuesta_claude

        historial.append({"role": "user", "content": texto_para_historial})
        historial.append({"role": "assistant", "content": respuesta_claude})
        guardar_historial(historial)

        await update.message.reply_text(respuesta_final)

    except Exception as e:
        logging.error(f"Error: {e}")
        await update.message.reply_text("Algo fallo. Intenta de nuevo.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Cortana en linea.\n\n"
        "Estoy aqui, Diego. Por donde empezamos?\n\n"
        "Comandos disponibles:\n"
        "/recuerda [hecho] Guardo algo permanentemente\n"
        "/memoria Te muestro todo lo que recuerdo\n"
        "/olvida [numero] Borro un hecho especifico\n"
        "/reset Limpio la conversacion activa"
    )


async def recuerda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = " ".join(context.args)
    if not texto:
        await update.message.reply_text("Dime que guardar. Ejemplo:\n/recuerda Consegui cliente nuevo: Marca XYZ")
        return
    agregar_hecho(texto)
    await update.message.reply_text(f"Guardado en memoria permanente:\n- {texto}")


async def ver_memoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memoria = cargar_memoria()
    if not memoria["hechos"]:
        await update.message.reply_text("No hay nada guardado en memoria aun.")
        return
    texto = "Memoria de Cortana:\n\n"
    for i, item in enumerate(memoria["hechos"], 1):
        texto += f"{i}. [{item['fecha']}] {item['hecho']}\n"
    await update.message.reply_text(texto)


async def olvida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Indica el numero del hecho. Ejemplo:\n/olvida 3")
        return
    try:
        numero = int(context.args[0]) - 1
        memoria = cargar_memoria()
        if numero < 0 or numero >= len(memoria["hechos"]):
            await update.message.reply_text("Ese numero no existe. Usa /memoria para ver la lista.")
            return
        hecho_borrado = memoria["hechos"].pop(numero)
        guardar_memoria(memoria)
        await update.message.reply_text(f"Borrado de memoria:\n- {hecho_borrado['hecho']}")
    except ValueError:
        await update.message.reply_text("Usa un numero. Ejemplo:\n/olvida 2")


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    guardar_historial([])
    await update.message.reply_text("Conversacion reiniciada. La memoria permanente sigue intacta.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    await responder_con_claude(
        update, context,
        messages_payload=[{"role": "user", "content": user_text}],
        texto_para_historial=user_text
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caption = update.message.caption or "Que ves en esta imagen? Describela y comenta lo que consideres relevante para mi."

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    photo_bytes = await file.download_as_bytearray()
    photo_b64 = base64.b64encode(photo_bytes).decode()

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


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    voice = update.message.voice
    file = await context.bot.get_file(voice.file_id)
    audio_bytes = await file.download_as_bytearray()

    await update.message.reply_text("Escuchando...")

    transcripcion = await transcribir_audio_gemini(bytes(audio_bytes), mime_type="audio/ogg")

    if not transcripcion:
        await update.message.reply_text("No pude entender el audio. Intenta de nuevo o escribelo.")
        return

    await update.message.reply_text(f"Entendi: {transcripcion}")

    await responder_con_claude(
        update, context,
        messages_payload=[{"role": "user", "content": transcripcion}],
        texto_para_historial=f"[Audio transcrito] {transcripcion}"
    )


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    audio = update.message.audio
    file = await context.bot.get_file(audio.file_id)
    audio_bytes = await file.download_as_bytearray()

    mime = audio.mime_type or "audio/mpeg"
    await update.message.reply_text("Procesando audio...")

    transcripcion = await transcribir_audio_gemini(bytes(audio_bytes), mime_type=mime)

    if not transcripcion:
        await update.message.reply_text("No pude procesar este audio.")
        return

    await update.message.reply_text(f"Contenido del audio:\n{transcripcion}")
    await responder_con_claude(
        update, context,
        messages_payload=[{"role": "user", "content": transcripcion}],
        texto_para_historial=f"[Audio transcrito] {transcripcion}"
    )


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caption = update.message.caption or ""
    video = update.message.video

    if video.file_size and video.file_size > 20 * 1024 * 1024:
        await update.message.reply_text("El video es muy pesado. Enviame uno de menos de 20MB.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    await update.message.reply_text("Analizando video...")

    file = await context.bot.get_file(video.file_id)
    video_bytes = await file.download_as_bytearray()

    mime = video.mime_type or "video/mp4"
    descripcion = await describir_video_gemini(bytes(video_bytes), mime_type=mime)

    if not descripcion:
        await update.message.reply_text("No pude analizar el video.")
        return

    if caption:
        contexto_completo = f"Diego mando un video con el mensaje: {caption}. Gemini analizo el video y detecto: {descripcion}. Comenta sobre esto."
    else:
        contexto_completo = f"Diego mando un video. Gemini lo analizo y detecto: {descripcion}. Comenta sobre esto."

    await responder_con_claude(
        update, context,
        messages_payload=[{"role": "user", "content": contexto_completo}],
        texto_para_historial=f"[Video enviado] {contexto_completo}"
    )


async def handle_video_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    await update.message.reply_text("Analizando video...")

    video_note = update.message.video_note
    file = await context.bot.get_file(video_note.file_id)
    video_bytes = await file.download_as_bytearray()

    descripcion = await describir_video_gemini(bytes(video_bytes), mime_type="video/mp4")

    if not descripcion:
        await update.message.reply_text("No pude analizar el video.")
        return

    contexto_completo = f"Diego mando un video circular. Gemini lo analizo y detecto: {descripcion}. Comenta sobre esto."

    await responder_con_claude(
        update, context,
        messages_payload=[{"role": "user", "content": contexto_completo}],
        texto_para_historial=f"[Video circular] {descripcion}"
    )


if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("recuerda", recuerda))
    app.add_handler(CommandHandler("memoria", ver_memoria))
    app.add_handler(CommandHandler("olvida", olvida))
    app.add_handler(CommandHandler("reset", reset))

    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video))
    app.add_handler(MessageHandler(filters.VIDEO_NOTE, handle_video_note))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Cortana en linea - multimedia activo...")
    app.run_polling()
