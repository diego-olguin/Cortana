import os
import re
import json
import base64
import logging
from datetime import datetime, date, time
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
import anthropic
import httpx
from system_prompt import get_system_prompt
from db import init_db, cargar_memoria, agregar_hecho, borrar_hecho, formatear_memoria, cargar_historial, guardar_mensaje, guardar_config, leer_config
from gmail import leer_emails_no_leidos, buscar_emails, enviar_email, compartir_drive, listar_drive
from sheets import registrar_egreso, registrar_ingreso_extra, registrar_ingreso_fijo, leer_sheet, CATEGORIAS_VALIDAS
from docs import crear_documento
from calendar_module import crear_evento, listar_eventos
from diario import escribir_entrada_diario, leer_entradas_recientes, obtener_contexto_para_system_prompt

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
ingreso_pendiente = {}

SHEET_FINANZAS_ID = "1vNs6j2ZaYuwIKw2DVrBa1n3CVKLDuIwEtQJU9BTWpf0"
SHEET_FINANZAS_PESTANA = "Marzo"

# Hora en que Cortana escribe su diario (hora UTC — 11pm CDMX = 4am UTC)
HORA_DIARIO_UTC = 4
ultimo_diario_escrito = None


async def check_diario(context):
    """
    Tarea programada — escribe el diario si es la hora correcta y no se ha escrito hoy.
    """
    global ultimo_diario_escrito
    ahora = datetime.utcnow()
    hoy = date.today()

    if ahora.hour == HORA_DIARIO_UTC and ultimo_diario_escrito != hoy:
        try:
            logging.info("Escribiendo entrada del diario de Cortana...")
            escribir_entrada_diario()
            ultimo_diario_escrito = hoy
            logging.info("Diario escrito correctamente.")
        except Exception as e:
            logging.error(f"Error escribiendo diario: {e}")


async def consultar_gemini(respuesta_claude: str) -> str:
    prompt = f"""Eres un consejero experto en negocios, video y estrategia digital. Acabas de leer esta respuesta de Cortana:

{respuesta_claude}

Criterios estrictos:
- Solo habla si puedes agregar algo CONCRETO que Cortana NO menciono y que cambia algo.
- Si Cortana cubrio bien el tema, responde exactamente: [SILENCIO]
- Si tienes algo que agregar, UN parrafo completo y directo. Nunca incompleto.

Tienes algo que agregar?"""
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"maxOutputTokens": 200, "temperature": 0.5}}
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(url, json=payload)
            texto = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            if "[SILENCIO]" in texto or len(texto) < 20:
                return ""
            if texto[-1] not in ".!?":
                ultimo = max(texto.rfind("."), texto.rfind("!"), texto.rfind("?"))
                texto = texto[:ultimo + 1] if ultimo > len(texto) // 2 else ""
            return texto
    except Exception as e:
        logging.error(f"Error Gemini: {e}")
        return ""


async def transcribir_audio_gemini(audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {"contents": [{"parts": [
            {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(audio_bytes).decode()}},
            {"text": "Transcribe exactamente lo que se dice. Solo la transcripcion."}
        ]}], "generationConfig": {"maxOutputTokens": 500, "temperature": 0.2}}
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, json=payload)
            return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        logging.error(f"Error audio: {e}")
        return ""


async def describir_video_gemini(video_bytes: bytes, mime_type: str = "video/mp4") -> str:
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {"contents": [{"parts": [
            {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(video_bytes).decode()}},
            {"text": "Describe detalladamente el video. Personas, acciones, ambiente, texto visible. En espanol."}
        ]}], "generationConfig": {"maxOutputTokens": 600, "temperature": 0.4}}
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(url, json=payload)
            return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        logging.error(f"Error video: {e}")
        return ""


def detectar_intencion(texto: str) -> str:
    t = texto.lower()

    if any(p in t for p in ["envialo", "mandalo", "si envialo", "confirmo", "aprobado", "dale envia"]):
        return "enviar_mail"
    if any(p in t for p in ["redacta un correo", "escribe un correo", "prepara un mail",
                             "manda un correo", "envia un correo", "correo para", "mail para",
                             "email para", "escribele un correo"]):
        return "redactar_mail"
    if any(p in t for p in ["revisa mi correo", "tengo correos", "correos no leidos",
                             "que correos tengo", "hay correos", "busca un correo",
                             "me escribio", "me mando un mail", "me llego un correo"]):
        return "leer_mail"
    if any(p in t for p in ["comparte la carpeta", "compartir carpeta", "dar acceso",
                             "comparte el archivo", "mis archivos de drive"]):
        return "drive"
    if any(p in t for p in ["registra en mi hoja", "agrega a mi hoja", "anota en mi hoja",
                             "registra en sheets", "agrega en sheets", "guarda en sheets",
                             "registra el gasto", "agrega el gasto", "anota el gasto",
                             "registra el ingreso", "agrega el ingreso",
                             "registra en finanzas", "agrega a finanzas",
                             "ya me pagaron", "me deben", "por cobrar",
                             "registra que gaste", "registra que pague",
                             "anota que gaste", "anota que pague",
                             "gasté", "gaste", "pagué", "pague",
                             "compré", "compre"]):
        return "sheets"
    if any(p in t for p in ["genera un contrato", "redacta un contrato", "crea un contrato",
                             "genera una cotizacion", "prepara una cotizacion", "crea una propuesta"]):
        return "docs"
    if any(p in t for p in ["que tengo en mi agenda", "que hay en mi calendario",
                             "proximos eventos", "crea un evento", "agenda una reunion",
                             "programa una cita", "agregar al calendario", "nuevo evento"]):
        return "calendar"
    return "chat"


async def responder_con_claude(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                messages_payload: list, texto_para_historial: str,
                                usar_consejero: bool = True):
    historial = cargar_historial(MAX_HISTORIAL)

    # Incluir contexto del diario en el system prompt
    diario_contexto = obtener_contexto_para_system_prompt()
    system = get_system_prompt(formatear_memoria()) + diario_contexto

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
            adicion = await consultar_gemini(respuesta_claude)
            if adicion:
                respuesta_final = f"{respuesta_claude}\n\n{adicion}"
        guardar_mensaje("user", texto_para_historial)
        guardar_mensaje("assistant", respuesta_claude)
        await update.message.reply_text(respuesta_final)
    except Exception as e:
        logging.error(f"Error Claude: {e}")
        await update.message.reply_text("Algo fallo. Intenta de nuevo.")


async def handle_sheets(update: Update, context, user_text: str):
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    hoy = datetime.now().strftime("%d/%m/%Y")
    categorias_str = ", ".join(CATEGORIAS_VALIDAS)

    prompt = f"""Eres un asistente financiero. El usuario menciono un movimiento de dinero.

FECHA HOY: {hoy}
CATEGORIAS VALIDAS PARA EGRESOS: {categorias_str}

MENSAJE: {user_text}

Devuelve SOLO un JSON valido sin texto adicional ni backticks:

Si es GASTO (pago, compra, gasto):
{{"tipo_flujo": "egreso", "datos": {{"categoria": "categoria valida", "descripcion": "lugar o concepto", "importe": 160.00, "fecha": "{hoy}", "metodo": "Efectivo"}}}}

Si es INGRESO:
{{"tipo_flujo": "ingreso", "datos": {{"descripcion": "cliente o concepto", "monto_deben": 0, "monto_pagado": 5000.00, "fecha": "{hoy}"}}}}

Si es CONSULTA de saldos:
{{"tipo_flujo": "consulta", "pregunta": "que quiere saber"}}

Si NO hay suficiente informacion para registrar:
{{"tipo_flujo": "falta_info", "mensaje": "que falta"}}"""

    try:
        response = claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
        )
        resultado_raw = response.content[0].text.strip().replace("```json", "").replace("```", "").strip()
        resultado = json.loads(resultado_raw)
        tipo = resultado.get("tipo_flujo")
        datos = resultado.get("datos", {})

        if tipo == "egreso":
            # Si falta el metodo de pago, preguntar
            if not datos.get("metodo") or datos.get("metodo") == "":
                guardar_config("egreso_pendiente", json.dumps(datos))
                await update.message.reply_text(
                    f"Detecto: {datos.get('categoria')} | {datos.get('descripcion')} | ${datos.get('importe')}\n\n"
                    "Como pagaste? Efectivo, BBVA, Santander, Nu?"
                )
                return

            fila = registrar_egreso(
                SHEET_FINANZAS_ID, SHEET_FINANZAS_PESTANA,
                datos.get("categoria", "OTRO"),
                datos.get("descripcion", ""),
                datos.get("importe", 0),
                datos.get("fecha", hoy),
                datos.get("metodo", "")
            )
            await update.message.reply_text(
                f"Registrado en Marzo (fila {fila}):\n"
                f"{datos.get('categoria')} | {datos.get('descripcion')} | "
                f"${datos.get('importe')} | {datos.get('metodo')}"
            )

        elif tipo == "ingreso":
            ingreso_pendiente[chat_id] = datos
            monto = datos.get("monto_pagado") or datos.get("monto_deben")
            estado = "por cobrar" if datos.get("monto_deben") else "ya cobrado"
            await update.message.reply_text(
                f"Ingreso: {datos.get('descripcion')} | ${monto} | {estado}\n\n"
                "Es FIJO (cliente recurrente) o EXTRA (proyecto freelance)?"
            )

        elif tipo == "consulta":
            datos_sheet = leer_sheet(SHEET_FINANZAS_ID, f"{SHEET_FINANZAS_PESTANA}!A3:E30")
            contexto = f"[DATOS FINANZAS MARZO:\n{datos_sheet}]\n\nPregunta: {user_text}"
            await responder_con_claude(update, context, [{"role": "user", "content": contexto}], user_text, usar_consejero=False)

        else:
            await update.message.reply_text("No entendi bien. Dame: que fue, cuanto y como pagaste.")

    except json.JSONDecodeError:
        await update.message.reply_text("No pude interpretar. Dame: que compraste/pagaste, cuanto y con que metodo.")
    except Exception as e:
        logging.error(f"Error sheets: {e}")
        await update.message.reply_text(f"Error al registrar: {e}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Cortana en linea.\n\n"
        "Habla naturalmente. Tengo acceso a tu Gmail, Calendar, Drive y Sheets.\n\n"
        "Comandos:\n"
        "/recuerda [hecho]\n"
        "/memoria\n"
        "/olvida [numero]\n"
        "/reset\n"
        "/diario — leer mis entradas recientes\n"
        "/diario_hoy — escribir entrada de hoy ahora"
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
        await update.message.reply_text("Indica el numero.")
        return
    try:
        numero = int(context.args[0]) - 1
        borrado = borrar_hecho(numero)
        if borrado is None:
            await update.message.reply_text("Ese numero no existe.")
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
    ingreso_pendiente.clear()
    email_draft.clear()
    await update.message.reply_text("Conversacion reiniciada. Memoria permanente intacta.")


async def ver_diario(update: Update, context: ContextTypes.DEFAULT_TYPE):
    entradas = leer_entradas_recientes(3)
    if not entradas:
        await update.message.reply_text("No hay entradas en el diario todavia. Usa /diario_hoy para escribir la primera.")
        return
    texto = ""
    for e in reversed(entradas):
        texto += f"— {e['fecha']} —\n{e['entrada']}\n\n"
    await update.message.reply_text(texto)


async def diario_hoy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Escribiendo entrada del diario...")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    try:
        entrada = escribir_entrada_diario()
        await update.message.reply_text(f"— {date.today()} —\n\n{entrada}")
    except Exception as e:
        await update.message.reply_text(f"Error al escribir el diario: {e}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_text = update.message.text
    texto_lower = user_text.lower().strip()

    # Flujo de metodo de pago pendiente
    egreso_raw = leer_config("egreso_pendiente")
    if egreso_raw:
        try:
            datos = json.loads(egreso_raw)
            # Detectar metodo de pago en la respuesta
            metodo = ""
            if any(p in texto_lower for p in ["efectivo", "cash"]):
                metodo = "Efectivo"
            elif any(p in texto_lower for p in ["bbva", "banamex"]):
                metodo = "BBVA"
            elif any(p in texto_lower for p in ["santander"]):
                metodo = "Santander"
            elif any(p in texto_lower for p in ["nu", "nubank"]):
                metodo = "Nu"
            elif any(p in texto_lower for p in ["tarjeta", "credito", "debito"]):
                metodo = "Tarjeta"
            elif any(p in texto_lower for p in ["transferencia", "spei"]):
                metodo = "Transferencia"

            if metodo:
                datos["metodo"] = metodo
                guardar_config("egreso_pendiente", "")
                hoy = datetime.now().strftime("%d/%m/%Y")
                fila = registrar_egreso(
                    SHEET_FINANZAS_ID, SHEET_FINANZAS_PESTANA,
                    datos.get("categoria", "OTRO"),
                    datos.get("descripcion", ""),
                    datos.get("importe", 0),
                    datos.get("fecha", hoy),
                    metodo
                )
                await update.message.reply_text(
                    f"Registrado (fila {fila}):\n"
                    f"{datos.get('categoria')} | {datos.get('descripcion')} | "
                    f"${datos.get('importe')} | {metodo}"
                )
                return
            else:
                guardar_config("egreso_pendiente", "")
        except Exception:
            guardar_config("egreso_pendiente", "")

    # Flujo de tipo de ingreso
    if chat_id in ingreso_pendiente:
        if any(p in texto_lower for p in ["no", "cancel", "olvida", "nada", "dejalo"]):
            del ingreso_pendiente[chat_id]
            await update.message.reply_text("Ok, cancelado.")
            return
        elif "fijo" in texto_lower:
            try:
                datos = ingreso_pendiente[chat_id]
                fila = registrar_ingreso_fijo(
                    SHEET_FINANZAS_ID, SHEET_FINANZAS_PESTANA,
                    datos.get("descripcion", ""),
                    datos.get("monto_deben", 0),
                    datos.get("monto_pagado", 0)
                )
                del ingreso_pendiente[chat_id]
                monto = datos.get("monto_pagado") or datos.get("monto_deben")
                await update.message.reply_text(f"Ingreso fijo registrado (fila {fila}):\n{datos.get('descripcion')} | ${monto}")
            except Exception as e:
                await update.message.reply_text(f"Error: {e}")
            return
        elif any(p in texto_lower for p in ["extra", "freelance", "proyecto", "variable"]):
            try:
                datos = ingreso_pendiente[chat_id]
                fila = registrar_ingreso_extra(
                    SHEET_FINANZAS_ID, SHEET_FINANZAS_PESTANA,
                    datos.get("descripcion", ""),
                    datos.get("monto_deben", 0),
                    datos.get("monto_pagado", 0)
                )
                del ingreso_pendiente[chat_id]
                monto = datos.get("monto_pagado") or datos.get("monto_deben")
                await update.message.reply_text(f"Ingreso extra registrado (fila {fila}):\n{datos.get('descripcion')} | ${monto}")
            except Exception as e:
                await update.message.reply_text(f"Error: {e}")
            return
        else:
            del ingreso_pendiente[chat_id]

    # Flujo de email
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
        elif any(p in texto_lower for p in ["no", "cancel", "olvida"]):
            del email_draft[chat_id]
            await update.message.reply_text("Borrador cancelado.")
            return
        else:
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
            prompt = f"Borrador:\n\n{email_draft[chat_id]['body']}\n\nCambio: {user_text}\n\nDevuelve solo el cuerpo corregido."
            response = claude.messages.create(model="claude-sonnet-4-20250514", max_tokens=1024,
                                               messages=[{"role": "user", "content": prompt}])
            email_draft[chat_id]["body"] = response.content[0].text.strip()
            await update.message.reply_text(f"Borrador actualizado:\n\n{email_draft[chat_id]['body']}\n\nDime 'envialo'.")
            return

    intencion = detectar_intencion(user_text)

    if intencion == "leer_mail":
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        try:
            prompt_q = f"El usuario pregunta: {user_text}\n\nExtrae termino de busqueda para Gmail. Si menciona persona: 'from:nombre'. Si tema: el tema. Si todos: 'in:inbox'. Solo el termino."
            r = claude.messages.create(model="claude-sonnet-4-20250514", max_tokens=50, messages=[{"role": "user", "content": prompt_q}])
            query = r.content[0].text.strip()
            emails = buscar_emails(query, max_results=5) or leer_emails_no_leidos(5)
            if not emails:
                contexto = f"[DATOS: Gmail revisado. No se encontraron correos.]\n\nMensaje: {user_text}"
            else:
                resumen = "\n".join([f"- {'[NO LEIDO] ' if not e['leido'] else ''}De: {e['from']} | {e['subject']} | {e['date'][:16]} | {e['snippet'][:80]}" for e in emails])
                contexto = f"[DATOS: Gmail. Correos:\n{resumen}]\n\nMensaje: {user_text}"
            await responder_con_claude(update, context, [{"role": "user", "content": contexto}], user_text, usar_consejero=False)
        except Exception:
            await update.message.reply_text("No pude acceder a Gmail.")

    elif intencion == "redactar_mail":
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        prompt = f"Redacta un correo profesional. Mensaje: {user_text}\n\nFormato:\nPARA: email\nASUNTO: asunto\nCUERPO:\n[cuerpo firmado como Diego Olguin, Eclipse Estudio]"
        response = claude.messages.create(model="claude-sonnet-4-20250514", max_tokens=1024, messages=[{"role": "user", "content": prompt}])
        resultado = response.content[0].text
        para = asunto = ""
        cuerpo_lines = []
        en_cuerpo = False
        for linea in resultado.split('\n'):
            if linea.startswith("PARA:"): para = linea.replace("PARA:", "").strip()
            elif linea.startswith("ASUNTO:"): asunto = linea.replace("ASUNTO:", "").strip()
            elif linea.startswith("CUERPO:"): en_cuerpo = True
            elif en_cuerpo: cuerpo_lines.append(linea)
        cuerpo = "\n".join(cuerpo_lines).strip()
        if para and asunto and cuerpo:
            email_draft[chat_id] = {"to": para, "subject": asunto, "body": cuerpo}
            await update.message.reply_text(f"Borrador:\n\nPara: {para}\nAsunto: {asunto}\n\n{cuerpo}\n\nDime 'envialo' o ajusta.")
        else:
            await update.message.reply_text(resultado)

    elif intencion == "sheets":
        await handle_sheets(update, context, user_text)

    elif intencion == "drive":
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        try:
            if any(p in texto_lower for p in ["comparte", "compartir", "dar acceso"]):
                prompt = f"Quiere compartir de Drive. Mensaje: {user_text}\n\nExtrae:\nARCHIVO: nombre\nEMAIL: email\nROL: reader/commenter/writer"
                r = claude.messages.create(model="claude-sonnet-4-20250514", max_tokens=100, messages=[{"role": "user", "content": prompt}])
                archivos = listar_drive(max_results=20)
                nombre = email_dest = ""
                rol = "reader"
                for linea in r.content[0].text.split('\n'):
                    if linea.startswith("ARCHIVO:"): nombre = linea.replace("ARCHIVO:", "").strip()
                    elif linea.startswith("EMAIL:"): email_dest = linea.replace("EMAIL:", "").strip()
                    elif linea.startswith("ROL:"): rol = linea.replace("ROL:", "").strip().lower()
                encontrado = next((a for a in archivos if nombre.lower() in a['name'].lower()), None)
                if encontrado and email_dest:
                    from gmail import compartir_drive
                    url = compartir_drive(encontrado['id'], email_dest, rol)
                    await update.message.reply_text(f"'{encontrado['name']}' compartido con {email_dest} como {rol}.\n{url}")
                else:
                    lista = "\n".join([f"- {a['name']}" for a in archivos[:10]])
                    await update.message.reply_text(f"No encontre '{nombre}'. Archivos:\n{lista}")
        except Exception as e:
            await update.message.reply_text(f"Error Drive: {e}")

    elif intencion == "calendar":
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        try:
            if any(p in texto_lower for p in ["que tengo", "que hay", "proximos", "agenda", "semana", "hay algo"]):
                eventos = listar_eventos(7)
                if not eventos:
                    contexto = f"[DATOS: Calendar. No hay eventos proximos.]\n\nMensaje: {user_text}"
                else:
                    resumen = "\n".join([f"- {e['titulo']} | {e['inicio']}" for e in eventos])
                    contexto = f"[DATOS: Calendar. Proximos eventos:\n{resumen}]\n\nMensaje: {user_text}"
                await responder_con_claude(update, context, [{"role": "user", "content": contexto}], user_text, usar_consejero=False)
            else:
                hoy_str = datetime.now().strftime("%Y-%m-%d")
                prompt = f"Crear evento. Mensaje: {user_text}\nHoy: {hoy_str}\n\nFormato:\nTITULO:\nINICIO: 2026-03-30T10:00:00\nFIN: 2026-03-30T11:00:00\nDESCRIPCION:\nINVITADOS: email1,email2\nRECORDATORIO: 30"
                response = claude.messages.create(model="claude-sonnet-4-20250514", max_tokens=300, messages=[{"role": "user", "content": prompt}])
                datos = {}
                for linea in response.content[0].text.split('\n'):
                    for campo in ["TITULO", "INICIO", "FIN", "DESCRIPCION", "INVITADOS", "RECORDATORIO"]:
                        if linea.startswith(f"{campo}:"): datos[campo] = linea.replace(f"{campo}:", "").strip()
                if "TITULO" in datos and "INICIO" in datos:
                    invitados = [e.strip() for e in datos.get("INVITADOS", "").split(",") if "@" in e]
                    url = crear_evento(datos["TITULO"], datos["INICIO"], datos["FIN"], datos.get("DESCRIPCION", ""), invitados, int(datos.get("RECORDATORIO", 30)))
                    resp = f"Evento: {datos['TITULO']}\n{datos['INICIO']}"
                    if invitados: resp += f"\nInvitados: {', '.join(invitados)}"
                    resp += f"\n\n{url}"
                    await update.message.reply_text(resp)
                else:
                    await update.message.reply_text("Dame titulo, fecha y hora.")
        except Exception as e:
            await update.message.reply_text(f"Error Calendar: {e}")

    elif intencion == "docs":
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        prompt = f"Genera contenido completo y profesional para Eclipse Estudio de Diego Olguin. Mensaje: {user_text}"
        response = claude.messages.create(model="claude-sonnet-4-20250514", max_tokens=2048, messages=[{"role": "user", "content": prompt}])
        try:
            url = crear_documento(user_text[:50], response.content[0].text)
            await update.message.reply_text(f"Documento creado en tu Drive:\n{url}")
        except Exception as e:
            await update.message.reply_text(f"Error Docs: {e}")

    else:
        await responder_con_claude(update, context, [{"role": "user", "content": user_text}], user_text)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caption = update.message.caption or "Que ves en esta imagen? Describela y comenta lo relevante."
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    photo_b64 = base64.b64encode(await file.download_as_bytearray()).decode()
    mensaje = {"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": photo_b64}},
        {"type": "text", "text": caption}
    ]}
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
        await responder_con_claude(update, context, [{"role": "user", "content": transcripcion}], f"[Audio] {transcripcion}")


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    file = await context.bot.get_file(update.message.audio.file_id)
    audio_bytes = bytes(await file.download_as_bytearray())
    mime = update.message.audio.mime_type or "audio/mpeg"
    await update.message.reply_text("Procesando audio...")
    transcripcion = await transcribir_audio_gemini(audio_bytes, mime)
    if not transcripcion:
        await update.message.reply_text("No pude procesar este audio.")
        return
    await update.message.reply_text(f"Contenido:\n{transcripcion}")
    await responder_con_claude(update, context, [{"role": "user", "content": transcripcion}], f"[Audio] {transcripcion}")


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    video = update.message.video
    if video.file_size and video.file_size > 20 * 1024 * 1024:
        await update.message.reply_text("El video es muy pesado. Menos de 20MB.")
        return
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    await update.message.reply_text("Analizando video...")
    file = await context.bot.get_file(video.file_id)
    video_bytes = bytes(await file.download_as_bytearray())
    descripcion = await describir_video_gemini(video_bytes, video.mime_type or "video/mp4")
    if not descripcion:
        await update.message.reply_text("No pude analizar el video.")
        return
    caption = update.message.caption or ""
    contenido = f"Diego mando un video{' con el mensaje: ' + caption if caption else ''}. Gemini detecto: {descripcion}. Comenta."
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
    contenido = f"Diego mando un video circular. Gemini detecto: {descripcion}. Comenta."
    await responder_con_claude(update, context, [{"role": "user", "content": contenido}], f"[Video circular] {descripcion}")


if __name__ == "__main__":
    from telegram.ext import JobQueue
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Tarea nocturna del diario — cada hora revisa si es momento de escribir
    app.job_queue.run_repeating(check_diario, interval=3600, first=10)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("recuerda", recuerda))
    app.add_handler(CommandHandler("memoria", ver_memoria))
    app.add_handler(CommandHandler("olvida", olvida))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("diario", ver_diario))
    app.add_handler(CommandHandler("diario_hoy", diario_hoy))

    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video))
    app.add_handler(MessageHandler(filters.VIDEO_NOTE, handle_video_note))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Cortana en linea - Diario activo...")
    app.run_polling()
