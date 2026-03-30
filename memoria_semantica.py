import os
import json
import psycopg2
import google.generativeai as genai
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def generar_embedding(texto: str) -> list:
    """
    Genera un embedding de 768 dimensiones usando Gemini.
    """
    result = genai.embed_content(
        model="models/text-embedding-004",
        content=texto,
        task_type="retrieval_document"
    )
    return result['embedding']


def guardar_recuerdo(contenido: str, tipo: str = "conversacion"):
    """
    Guarda un texto en la memoria semantica con su embedding.
    Solo guarda si el contenido es suficientemente largo y relevante.
    """
    if len(contenido.strip()) < 20:
        return

    try:
        embedding = generar_embedding(contenido)
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO memoria_semantica (contenido, embedding, tipo) VALUES (%s, %s::vector, %s)",
            (contenido, str(embedding), tipo)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error guardando recuerdo: {e}")


def buscar_recuerdos(query: str, limite: int = 5, umbral: float = 0.7) -> list:
    """
    Busca recuerdos semanticamente similares a la query.
    Devuelve los mas relevantes ordenados por similitud.
    """
    try:
        embedding_query = generar_embedding(query)
        conn = get_conn()
        cur = conn.cursor()

        # Busca por similitud coseno — 1 es identico, 0 es sin relacion
        cur.execute("""
            SELECT contenido, tipo, fecha,
                   1 - (embedding <=> %s::vector) AS similitud
            FROM memoria_semantica
            WHERE 1 - (embedding <=> %s::vector) > %s
            ORDER BY similitud DESC
            LIMIT %s
        """, (str(embedding_query), str(embedding_query), umbral, limite))

        rows = cur.fetchall()
        cur.close()
        conn.close()

        return [
            {
                "contenido": r[0],
                "tipo": r[1],
                "fecha": str(r[2]),
                "similitud": round(float(r[3]), 3)
            }
            for r in rows
        ]
    except Exception as e:
        print(f"Error buscando recuerdos: {e}")
        return []


def guardar_conversacion_completa(mensajes: list):
    """
    Procesa una lista de mensajes y guarda los relevantes como recuerdos.
    Filtra mensajes cortos o sin contenido util.
    """
    for msg in mensajes:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if isinstance(content, str) and len(content) > 30:
            # Solo guarda mensajes sustanciales
            tipo = "diego" if role == "user" else "cortana"
            guardar_recuerdo(content, tipo)


def formatear_recuerdos_para_contexto(recuerdos: list) -> str:
    """
    Formatea los recuerdos para incluirlos en el system prompt.
    """
    if not recuerdos:
        return ""

    texto = "\n════════════════════════════════\nRECUERDOS RELEVANTES\n════════════════════════════════\n"
    for r in recuerdos:
        fecha = r['fecha'][:10] if r['fecha'] else ""
        texto += f"[{fecha}] {r['contenido'][:200]}\n\n"
    return texto


def limpiar_recuerdos_viejos(dias: int = 90):
    """
    Elimina recuerdos de mas de X dias para no saturar la DB.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM memoria_semantica WHERE fecha < NOW() - INTERVAL '%s days'",
        (dias,)
    )
    eliminados = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    return eliminados


if __name__ == "__main__":
    # Test
    print("Probando memoria semantica...")
    guardar_recuerdo("Diego tiene un proyecto con Hammer Nutrition para reels cinematograficos con atletas", "proyecto")
    guardar_recuerdo("Eclipse Estudio tiene identidad minimalista en blanco y negro estilo revista digital", "contexto")

    resultados = buscar_recuerdos("que proyectos tiene Diego con marcas de deporte")
    for r in resultados:
        print(f"Similitud: {r['similitud']} - {r['contenido'][:100]}")
