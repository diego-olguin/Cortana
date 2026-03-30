import os
import re
import json
import base64
import logging
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
import anthropic
import httpx
from system_prompt import get_system_prompt
from db import init_db, cargar_memoria, agregar_hecho, borrar_hecho, formatear_memoria, cargar_historial, guardar_mensaje, guardar_config, leer_config
from gmail import leer_emails_no_leidos, buscar_emails, enviar_email, compartir_drive, listar_drive
from sheets import explorar_sheet, escribir_en_sheet, escribir_sheet, leer_sheet
from docs import crear_documento, listar_documentos
from calendar_module import crear_evento, listar_eventos

from dotenv import load_dotenv
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

MAX_HISTORIAL = 20

logging.basicConfig(level=logging.INFO)
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

init_db()

email_draft = {}


async def consultar_gemini(respuesta_claude: str) -> str:
    prompt = f"""Eres un consejero experto en negocios, video y estrategia digital. Acabas de leer esta respuesta de una IA llamada Cortana:

{respuesta_claude}

Tu trabajo: decide si tienes algo CONCRETO y VALIOSO que agregar. Criterios estrictos:
- Solo habla si puedes agregar un dato, perspectiva o advertencia que Cortana NO menciono y que cambia algo.
- Si Cortana cubrio bien el tema, responde exactamente: [SILENCIO]
- Si tienes algo que agregar, escribe UN parrafo completo y directo. Sin introduccion, sin referirte a Cortana.
- Nunca dejes una oracion incompleta.

Tienes algo concreto que agregar?"""

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 200, "temperature": 0.5}
        }
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(url, json=payload)
            texto = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            if "[SILENCIO]" in texto or len(texto) < 20:
                return ""
            if texto and texto[-1] not in ".!?":
                ultimo_punto = max(texto.rfind("."), texto.rfind("!"), texto.rfind("?"))
                if ultimo_punto > len(texto) // 2:
                    texto = texto[:ultimo_punto + 1]
                else:
                    return ""
            return texto
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


def detectar_intencion(texto: str) -> str:
    texto_lower = texto.lower()

    palabras_mail = ["correo", "mail", "email", "mensaje de", "inbox", "bandeja", "escribio", "mando un mail",
                     "mando correo", "recibiste", "tiene correo", "tengo correo", "no leidos", "sin leer",
                     "revisa mi correo", "hay correos", "correos nuevos", "me escribio", "me llego algo",
                     "alguien me escribio", "noticias de", "saber si", "busca un correo", "encuentra el correo"]
    palabras_redactar = ["redacta", "escribe un correo", "prepara un mail", "manda un correo",
                         "envia un correo", "correo para", "mail para", "email para", "escribele"]
    palabras_enviar = ["envialo", "mandalo", "si envialo", "confirmo", "aprobado", "manda el correo", "dale envia"]
    palabras_drive = ["carpeta", "folder", "comparte", "compartir", "acceso a", "dar acceso",
                      "archivos de drive", "mis carpetas", "mis archivos"]
    palabras_sheet = ["sheets", "hoja", "spreadsheet", "tabla", "excel", "registro", "agrega a la hoja",
                      "guarda en sheets", "anota", "actualiza la hoja", "gasto", "ingreso",
                      "agrega que", "registra que", "registra", "anota que", "agrega el gasto",
                      "registra el gasto", "finanzas", "hoja de finanzas", "gastos", "egresos",
                      "ingresos", "cotizacion", "factura", "cliente nuevo"]
    palabras_doc = ["documento", "contrato", "crea un doc", "genera un contrato",
                    "redacta un contrato", "prepara la cotizacion", "genera una cotizacion",
                    "carta", "propuesta"]
    palabras_calendar = ["calendario", "agenda", "evento", "cita", "reunion", "agendar", "programa",
                         "recordatorio", "que tengo", "que hay esta", "proximos eventos", "esta semana",
                         "invita", "invitar", "crear evento", "nuevo evento", "cuando tengo", "mis eventos",
                         "tengo algo agendado", "hay algo"]

    if any(p in texto_lower for p in palabras_enviar):
        return "enviar_mail"
    if any(p in texto_lower for p in palabras_redactar):
        return "redactar_mail"
    if any(p in texto_lower for p in palabras_mail):
        return "leer_mail"
    if any(p in texto_lower for p in palabras_drive):
        return "drive"
    if any(p in texto_lower for p in palabras_sheet):
        return "sheets"
    if any(p in texto_lower for p in palabras_doc):
        return "docs"
    if any(p in texto_lower for p in palabras_calendar):
        return "calendar"
    return "chat"


async def responder_con_claude(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                messages_payload: list, texto_para_historial: str,
                                usar_consejero: bool = True):
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
        respuesta_final = respuesta_claude

        if usar_consejero:
            adicion_gemini = await consultar_gemini(respuesta_claude)
            if adicion_gemini:
                respuesta_final = f"{respuesta_claude}\n\n{adicion_gemini}"

        guardar_mensaje("user", texto_para_historial)
        guardar_mensaje("assistant", respuesta_claude)

        await update.message.reply_text(respuesta_final)

    except Exception as e:
        logging.error(f"Error Claude: {e}")
        await update.message.reply_text("Algo fallo. Intenta de nuevo.")


async def handle_sheets(update: Update, context, user_text: str):
    """
    Manejo inteligente de Google Sheets.
    1. Si hay un link en el mensaje, explora la estructura del sheet y la guarda.
    2. Usa Claude para entender que datos registrar y en que pestana.
    3. Escribe en la fila correcta automaticamente.
    """
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # Extraer ID del sheet si hay un link
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", user_text)
    if match:
        sheet_id = match.group(1)
        guardar_config("sheet_activo_id", sheet_id)

        # Explorar y guardar estructura
        try:
            estructura = explorar_sheet(sheet_id)
            guardar_config("sheet_activo_estructura", json.dumps(estructura, ensure_ascii=False))
            await update.message.reply_text(
                f"Sheet conectado: {estructura['titulo']}\n"
                f"Pestanas: {', '.join([p['nombre'] for p in estructura['pestanas']])}\n\n"
                "Ahora puedes pedirme que registre datos naturalmente."
            )
        except Exception as e:
            await update.message.reply_text(f"No pude leer el sheet: {e}")
        return
    else:
        sheet_id = leer_config("sheet_activo_id")

    if not sheet_id:
        await update.message.reply_text("Mandame el link de tu Google Sheet.")
        return

    # Cargar estructura guardada
    estructura_raw = leer_config("sheet_activo_estructura")
    if not estructura_raw:
        try:
            estructura = explorar_sheet(sheet_id)
            guardar_config("sheet_activo_estructura", json.dumps(estructura, ensure_ascii=False))
        except Exception as e:
            await update.message.reply_text(f"No pude leer el sheet: {e}")
            return
    else:
        estructura = json.loads(estructura_raw)

    # Usar Claude para decidir que datos registrar y en que pestana
    hoy = datetime.now().strftime("%d/%m/%Y")
    estructura_resumida = json.dumps({
        "titulo": estructura["titulo"],
        "pestanas": [{"nombre": p["nombre"], "headers": p["headers"]} for p in estructura["pestanas"]]
    }, ensure_ascii=False)

    prompt = f"""Eres un asistente de finanzas. El usuario quiere registrar datos en Google Sheets.

ESTRUCTURA DEL SHEET:
{estructura_resumida}

MENSAJE DEL USUARIO: {user_text}
FECHA DE HOY: {hoy}

Analiza el mensaje y decide:
1. En que pestana va el registro (elige la mas apropiada segun el nombre y los headers)
2. Que valores corresponden a cada header de esa pestana

Responde SOLO con un JSON valido, sin texto adicional, sin backticks:
{{
  "pestana": "nombre exacto de la pestana",
  "datos": {{
    "header1": "valor1",
    "header2": "valor2"
  }}
}}"""

    try:
        response = claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )
        resultado_raw = response.content[0].text.strip()

        # Limpiar posibles backticks
        resultado_raw = resultado_raw.replace("```json", "").replace("```", "").strip()
        resultado = json.loads(resultado_raw)

        pestana_nombre = resultado.get("pestana", "")
        datos = resultado.get("datos", {})

        # Buscar headers de esa pestana
        pestana_info = next((p for p in estructura["pestanas"] if p["nombre"] == pestana_nombre), None)
        if not pestana_info:
            await update.message.reply_text(f"No encontre la pestana '{pestana_nombre}'. Pestanas disponibles: {', '.join([p['nombre'] for p in estructura['pestanas']])}")
            return

        headers = pestana_info["headers"]

        # Escribir en el sheet
        fila_escrita = escribir_en_sheet(sheet_id, pestana_nombre, datos, headers)

        # Confirmar con resumen
        resumen = " | ".join([f"{k}: {v}" for k, v in datos.items() if v])
        await update.message.reply_text(f"Registrado en {pestana_nombre} (fila {fila_escrita}):\n{resumen}")

    except json.JSONDecodeError as e:
        logging.error(f"Error JSON sheets: {e} - Raw: {resultado_raw}")
        await update.message.reply_text("No pude interpretar los datos. Dame mas detalle: fecha, categoria, concepto, monto y tipo.")
    except Exception as e:
        logging.error(f"Error escribiendo sheet: {e}")
        await update.message.reply_text(f"Error al escribir en el sheet: {e}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Cortana en linea.\n\n"
        "Habla naturalmente. Tengo acceso a tu Gmail, Calendar, Drive y Sheets.\n\n"
        "Comandos:\n"
        "/recuerda [hecho]\n"
        "/memoria\n"
        "/olvida [numero]\n"
        "/reset"
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
        await update.message.reply_text("Usa un numero.")


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import psycopg2
    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    cur = conn.cursor()
    cur.execute("DELETE FROM historial")
    conn.commit()
    cur.close()
    conn.close()
    await update.message.reply_text("Conversacion reiniciada. Memoria permanente intacta.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_text = update.message.text

    if chat_id in email_draft:
        intencion = detectar_intencion(user_text)
        if intencion == "enviar_mail":
            draft = email_draft[chat_id]
            try:
                enviar_email(draft["to"], draft["subject"], draft["body"])
                del email_draft[chat_id]
                await update.message.reply_text(f"Correo enviado a {draft['to']}.")
            except Exception as e:
                await update.message.reply_text(f"Error al enviar: {e}")
            return
        else:
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
            draft = email_draft[chat_id]
            prompt = f"Borrador actual:\n\n{draft['body']}\n\nCambio solicitado: {user_text}\n\nDevuelve solo el cuerpo corregido."
            response = claude.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}]
            )
            email_draft[chat_id]["body"] = response.content[0].text.strip()
            await update.message.reply_text(
                f"Borrador actualizado:\n\n{email_draft[chat_id]['body']}\n\nDime 'envialo' para mandarlo."
            )
            return

    intencion = detectar_intencion(user_text)

    if intencion == "leer_mail":
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        try:
            prompt_busqueda = f"El usuario pregunta: {user_text}\n\nExtrae el termino de busqueda para Gmail. Si menciona una persona, devuelve 'from:nombre'. Si pregunta por tema, devuelve el tema. Si quiere todos, devuelve 'in:inbox'. Solo el termino."
            r = claude.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=50,
                messages=[{"role": "user", "content": prompt_busqueda}]
            )
            query = r.content[0].text.strip()
            emails = buscar_emails(query, max_results=5)
            if not emails:
                emails = leer_emails_no_leidos(5)
            if not emails:
                contexto = f"[DATOS: Gmail revisado. No se encontraron correos.]\n\nMensaje de Diego: {user_text}"
            else:
                resumen = "\n".join([
                    f"- {'[NO LEIDO] ' if not e['leido'] else ''}De: {e['from']} | Asunto: {e['subject']} | {e['date'][:16]} | {e['snippet'][:100]}"
                    for e in emails
                ])
                contexto = f"[DATOS: Gmail revisado. Correos encontrados:\n{resumen}]\n\nMensaje de Diego: {user_text}"
            await responder_con_claude(update, context, [{"role": "user", "content": contexto}], user_text, usar_consejero=False)
        except Exception as e:
            logging.error(f"Error Gmail: {e}")
            await update.message.reply_text("No pude acceder a Gmail.")

    elif intencion == "redactar_mail":
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        prompt = f"El usuario quiere redactar un correo. Mensaje: {user_text}\n\nExtrae destinatario, asunto y redacta el cuerpo profesional firmado como Diego Olguin de Eclipse Estudio. Formato:\nPARA: email@destino.com\nASUNTO: asunto\nCUERPO:\n[cuerpo]"
        response = claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )
        resultado = response.content[0].text
        lineas = resultado.split('\n')
        para = ""
        asunto = ""
        cuerpo_lines = []
        en_cuerpo = False
        for linea in lineas:
            if linea.startswith("PARA:"):
                para = linea.replace("PARA:", "").strip()
            elif linea.startswith("ASUNTO:"):
                asunto = linea.replace("ASUNTO:", "").strip()
            elif linea.startswith("CUERPO:"):
                en_cuerpo = True
            elif en_cuerpo:
                cuerpo_lines.append(linea)
        cuerpo = "\n".join(cuerpo_lines).strip()
        if para and asunto and cuerpo:
            email_draft[chat_id] = {"to": para, "subject": asunto, "body": cuerpo}
            await update.message.reply_text(
                f"Borrador listo:\n\nPara: {para}\nAsunto: {asunto}\n\n{cuerpo}\n\nDime 'envialo' o ajusta."
            )
        else:
            await update.message.reply_text(resultado)

    elif intencion == "sheets":
        await handle_sheets(update, context, user_text)

    elif intencion == "drive":
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        try:
            texto_lower = user_text.lower()
            if any(p in texto_lower for p in ["comparte", "compartir", "dar acceso", "acceso a"]):
                prompt = f"El usuario quiere compartir algo de Drive. Mensaje: {user_text}\n\nExtrae:\nARCHIVO: nombre\nEMAIL: email\nROL: reader, commenter o writer"
                r = claude.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=100,
                    messages=[{"role": "user", "content": prompt}]
                )
                datos_raw = r.content[0].text
                archivos = listar_drive(max_results=20)
                nombre_archivo = ""
                email_dest = ""
                rol = "reader"
                for linea in datos_raw.split('\n'):
                    if linea.startswith("ARCHIVO:"):
                        nombre_archivo = linea.replace("ARCHIVO:", "").strip()
                    elif linea.startswith("EMAIL:"):
                        email_dest = linea.replace("EMAIL:", "").strip()
                    elif linea.startswith("ROL:"):
                        rol = linea.replace("ROL:", "").strip().lower()
                archivo_encontrado = next((a for a in archivos if nombre_archivo.lower() in a['name'].lower()), None)
                if archivo_encontrado and email_dest:
                    url = compartir_drive(archivo_encontrado['id'], email_dest, rol)
                    await update.message.reply_text(f"'{archivo_encontrado['name']}' compartido con {email_dest} como {rol}.\n{url}")
                else:
                    lista = "\n".join([f"- {a['name']}" for a in archivos[:10]])
                    await update.message.reply_text(f"No encontre '{nombre_archivo}'. Archivos disponibles:\n\n{lista}")
            else:
                archivos = listar_drive(max_results=10)
                lista = "\n".join([f"- {a['name']}" for a in archivos])
                contexto = f"[DATOS: Drive revisado. Archivos recientes:\n{lista}]\n\nMensaje de Diego: {user_text}"
                await responder_con_claude(update, context, [{"role": "user", "content": contexto}], user_text, usar_consejero=False)
        except Exception as e:
            logging.error(f"Error Drive: {e}")
            await update.message.reply_text(f"Error con Drive: {e}")

    elif intencion == "calendar":
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        try:
            texto_lower = user_text.lower()
            if any(p in texto_lower for p in ["que tengo", "que hay", "proximos", "agenda", "semana", "ver", "hay algo", "tengo algo"]):
                eventos = listar_eventos(7)
                if not eventos:
                    contexto = f"[DATOS: Google Calendar revisado. No hay eventos en los proximos 7 dias.]\n\nMensaje de Diego: {user_text}"
                else:
                    resumen = "\n".join([f"- {e['titulo']} | {e['inicio']} | Invitados: {', '.join(e['invitados']) if e['invitados'] else 'ninguno'}" for e in eventos])
                    contexto = f"[DATOS: Google Calendar revisado. Proximos eventos:\n{resumen}]\n\nMensaje de Diego: {user_text}"
                await responder_con_claude(update, context, [{"role": "user", "content": contexto}], user_text, usar_consejero=False)
            else:
                hoy = datetime.now().strftime("%Y-%m-%d")
                prompt = f"El usuario quiere crear un evento. Mensaje: {user_text}\n\nHoy es {hoy}. Extrae:\nTITULO: nombre\nINICIO: 2026-03-30T10:00:00\nFIN: 2026-03-30T11:00:00\nDESCRIPCION: opcional\nINVITADOS: email1,email2\nRECORDATORIO: 30"
                response = claude.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=300,
                    messages=[{"role": "user", "content": prompt}]
                )
                datos = {}
                for linea in response.content[0].text.split('\n'):
                    for campo in ["TITULO", "INICIO", "FIN", "DESCRIPCION", "INVITADOS", "RECORDATORIO"]:
                        if linea.startswith(f"{campo}:"):
                            datos[campo] = linea.replace(f"{campo}:", "").strip()
                if "TITULO" in datos and "INICIO" in datos and "FIN" in datos:
                    invitados = [e.strip() for e in datos.get("INVITADOS", "").split(",") if "@" in e]
                    recordatorio = int(datos.get("RECORDATORIO", "30"))
                    url = crear_evento(datos["TITULO"], datos["INICIO"], datos["FIN"],
                                      datos.get("DESCRIPCION", ""), invitados, recordatorio)
                    respuesta = f"Evento creado: {datos['TITULO']}\nInicio: {datos['INICIO']}"
                    if invitados:
                        respuesta += f"\nInvitados: {', '.join(invitados)}"
                    respuesta += f"\n\n{url}"
                    await update.message.reply_text(respuesta)
                else:
                    await update.message.reply_text("Dame titulo, fecha, hora de inicio y fin.")
        except Exception as e:
            logging.error(f"Error Calendar: {e}")
            await update.message.reply_text(f"Error con Calendar: {e}")

    elif intencion == "docs":
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        prompt = f"El usuario quiere crear un documento. Mensaje: {user_text}\n\nGenera el contenido completo y profesional para Eclipse Estudio de Diego Olguin."
        response = claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}]
        )
        contenido = response.content[0].text
        try:
            url = crear_documento(user_text[:50], contenido)
            await update.message.reply_text(f"Documento creado en tu Drive:\n{url}")
        except Exception as e:
            logging.error(f"Error Docs: {e}")
            await update.message.reply_text(f"Error al crear documento: {e}")

    else:
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
    intencion = detectar_intencion(transcripcion)
    if intencion != "chat":
        update.message.text = transcripcion
        await handle_message(update, context)
    else:
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
    contenido = f"Diego mando un video{' con el mensaje: ' + caption if caption else ''}. Gemini lo analizo y detecto: {descripcion}. Comenta sobre esto."
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

    print("Cortana en linea - Gmail, Sheets inteligente, Docs, Calendar y Drive activos...")
    app.run_polling()
