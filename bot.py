import os
import base64
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
import anthropic
import httpx
from system_prompt import get_system_prompt
from db import init_db, cargar_memoria, agregar_hecho, borrar_hecho, formatear_memoria, cargar_historial, guardar_mensaje

from dotenv import load_dotenv
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

MAX_HISTORIAL = 20

logging.basicConfig(level=logging.INFO)
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

init_db()


async def consultar_gemini(pregunta_original: str, respuesta_claude: str) -> str:
    prompt = f"""Eres un consejero inteligente. Claude ya respondio una pregunta.
1. Si la respuesta es completa responde SOLO: [SIN_ADICION]
2. Si hay algo que agregar responde con UN parrafo corto y directo.
No repitas lo que dijo Claude. Responde en espanol.

PREGUNTA: {pregunta_original}
RESPUESTA DE CLAUDE: {respuesta_claude}"""
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 300, "temperature": 0.7}
        }
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(url, json=payload)
            texto = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            return "" if "[SIN_ADICION]" in texto else texto
    except Exception as e:
        logging.error(f"Error Gemini consejero: {e}")
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
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{"parts": [
                {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(video_bytes).decode()}},
                {"text": "Describe detalladamente lo que ocurre en este video. Personas, acciones, ambiente, texto visible. Responde en espanol."}
            ]}],
            "generationConfig": {"maxOutputTokens": 600, "temperature": 0.4}
        }
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(url, json=payload)
            return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        logging.error(f"Error describiendo video: {e}")
        return ""


async def responder_con_claude(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                messages_payload: list, texto_para_historial: str):
    historial = cargar_historial(MAX_HISTORIAL)
    system = get_system_prompt(formatear_memoria())

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        response = claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            system=system,
            messages=historial + messages_payload
        )
        respuesta_claude = response.content[0].text
        adicion_gemini = await consultar_gemini(texto_para_historial, respuesta_claude)

        respuesta_final = (
            f"{respuesta_claude}\n\n[Consejero] {adicion_gemini}"
            if adicion_gemini else respuesta_claude
        )

        guardar_mensaje("user", texto_para_historial)
        guardar_mensaje("assistant", respuesta_claude)

        await update.message.reply_text(respuesta_final)

    except Exception as e:
        logging.error(f"Error Claude: {e}")
        await update.message.reply_text("Algo fallo. Intenta de nuevo.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Cortana en linea.\n\n"
        "Estoy aqui, Diego.\n\n"
        "/recuerda [hecho] — Guardo algo permanentemente\n"
        "/memoria — Te muestro todo lo que recuerdo\n"
        "/olvida [numero] — Borro un hecho especifico\n"
        "/reset — Limpio la conversacion activa"
    )


async def recuerda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = " ".join(context.args)
    if not texto:
        await update.message.reply_text("Dime que guardar.")
        return
    agregar_hecho(texto)
    await update.message.reply_text(f"Guardado:\n- {texto}")


async def ver_memoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memoria = cargar_memoria()
    if not memoria["hechos"]:
        await update.message.reply_text("No hay nada guardado.")
        return
    texto = "Memoria de Cortana:\n\n"
    for i, item in enumerate(memoria["hechos"], 1):
        texto += f"{i}. [{item['fecha']}] {item['hecho']}\n"
    await update.message.reply_text(texto)


async def olvida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Indica el numero. Ejemplo:\n/olvida 3")
        return
    try:
        numero = int(context.args[0]) - 1
        borrado = borrar_hecho(numero)
        if borrado is None:
            await update.message.reply_text("Ese numero no existe. Usa /memoria.")
            return
        await update.message.reply_text(f"Borrado:\n- {borrado}")
    except ValueError:
        await update.message.reply_text("Usa un numero. Ejemplo:\n/olvida 2")


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import psycopg2
    DATABASE_URL = os.getenv("DATABASE_URL")
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("DELETE FROM historial")
    conn.commit()
    cur.close()
    conn.close()
    await update.message.reply_text("Conversacion reiniciada. Memoria permanente intacta.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    await responder_con_claude(
        update, context,
        messages_payload=[{"role": "user", "content": user_text}],
        texto_para_historial=user_text
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caption = update.message.caption or "Que ves en esta imagen? Describela y comenta lo relevante."
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    photo_b64 = base64.b64encode(await file.download_as_bytearray()).decode()

    mensaje = {
        "role": "user",
        "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": photo_b64}},
            {"type": "text", "text": caption}
        ]
    }
    await responder_con_claude(update, context, [mensaje], f"[Imagen] {caption}")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    file = await context.bot.get_file(update.message.voice.file_id)
    audio_bytes = bytes(await file.download_as_bytearray())

    await update.message.reply_text("Escuchando...")
    transcripcion = await transcribir_audio_gemini(audio_bytes, "audio/ogg")

    if not transcripcion:
        await update.message.reply_text("No pude entender el audio.")
        return

    await update.message.reply_text(f"Entendi: {transcripcion}")
    await responder_con_claude(
        update, context,
        messages_payload=[{"role": "user", "content": transcripcion}],
        texto_para_historial=f"[Audio] {transcripcion}"
    )


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    audio = update.message.audio
    file = await context.bot.get_file(audio.file_id)
    audio_bytes = bytes(await file.download_as_bytearray())
    mime = audio.mime_type or "audio/mpeg"

    await update.message.reply_text("Procesando audio...")
    transcripcion = await transcribir_audio_gemini(audio_bytes, mime)

    if not transcripcion:
        await update.message.reply_text("No pude procesar este audio.")
        return

    await update.message.reply_text(f"Contenido:\n{transcripcion}")
    await responder_con_claude(
        update, context,
        messages_payload=[{"role": "user", "content": transcripcion}],
        texto_para_historial=f"[Audio] {transcripcion}"
    )


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    video = update.message.video
    if video.file_size and video.file_size > 20 * 1024 * 1024:
        await update.message.reply_text("El video es muy pesado. Menos de 20MB.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    await update.message.reply_text("Analizando video...")

    file = await context.bot.get_file(video.file_id)
    video_bytes = bytes(await file.download_as_bytearray())
    mime = video.mime_type or "video/mp4"
    descripcion = await describir_video_gemini(video_bytes, mime)

    if not descripcion:
        await update.message.reply_text("No pude analizar el video.")
        return

    caption = update.message.caption or ""
    if caption:
        contenido = f"Diego mando un video con el mensaje: {caption}. Gemini analizo el video y detecto: {descripcion}. Comenta sobre esto."
    else:
        contenido = f"Diego mando un video. Gemini lo analizo y detecto: {descripcion}. Comenta sobre esto."

    await responder_con_claude(update, context, [{"role": "user", "content": contenido}], f"[Video] {contenido}")


async def handle_video_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    await update.message.reply_text("Analizando video...")

    file = await context.bot.get_file(update.message.video_note.file_id)
    video_bytes = bytes(await file.download_as_bytearray())
    descripcion = await describir_video_gemini(video_bytes, "video/mp4")

    if not descripcion:
        await update.message.reply_text("No pude analizar el video.")
        return

    contenido = f"Diego mando un video circular. Gemini lo analizo y detecto: {descripcion}. Comenta sobre esto."
    await responder_con_claude(update, context, [{"role": "user", "content": contenido}], f"[Video circular] {descripcion}")


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
