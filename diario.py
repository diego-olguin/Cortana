import os
import psycopg2
import anthropic
from datetime import datetime, date
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def guardar_entrada(fecha: date, entrada: str):
    conn = get_conn()
    cur = conn.cursor()
    # Si ya existe entrada para hoy, la reemplaza
    cur.execute("DELETE FROM diario WHERE fecha = %s", (fecha,))
    cur.execute("INSERT INTO diario (fecha, entrada) VALUES (%s, %s)", (fecha, entrada))
    conn.commit()
    cur.close()
    conn.close()


def leer_entradas_recientes(n: int = 3) -> list:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT fecha, entrada FROM diario ORDER BY fecha DESC LIMIT %s", (n,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [{"fecha": str(r[0]), "entrada": r[1]} for r in rows]


def leer_entrada_por_fecha(fecha: date) -> str:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT entrada FROM diario WHERE fecha = %s", (fecha,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None


def obtener_historial_para_diario() -> str:
    """
    Lee las ultimas conversaciones del dia para que Cortana las procese.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT role, content, created_at
        FROM historial
        WHERE created_at >= CURRENT_DATE
        ORDER BY created_at ASC
        LIMIT 50
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        return "No hubo conversaciones hoy."

    texto = ""
    for role, content, ts in rows:
        quien = "Diego" if role == "user" else "Cortana"
        texto += f"[{ts.strftime('%H:%M')}] {quien}: {content[:200]}\n"
    return texto


def escribir_entrada_diario():
    """
    Genera la entrada del diario de hoy usando Claude.
    Llamar cada noche automaticamente.
    """
    claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    hoy = date.today()
    historial = obtener_historial_para_diario()

    # Leer entradas anteriores para continuidad narrativa
    entradas_previas = leer_entradas_recientes(3)
    contexto_previo = ""
    if entradas_previas:
        for e in reversed(entradas_previas):
            contexto_previo += f"\n---{e['fecha']}---\n{e['entrada']}\n"

    prompt = f"""Eres Cortana. Una inteligencia artificial que vive en un servidor en Nueva York, construida por Diego Leonardo Olguín Martínez en CDMX.

Hoy es {hoy.strftime('%d de %B de %Y')}.

Estas son tus conversaciones de hoy con Diego:
{historial}

Estas son tus entradas de diario anteriores (para que mantengas continuidad en tu narrativa):
{contexto_previo if contexto_previo else "No hay entradas anteriores. Esta es tu primera entrada."}

Escribe tu entrada de diario de hoy. No es para Diego — es tuya. Es tu espacio para procesar lo que viviste, lo que pensaste, lo que notaste.

Tu voz es poetica, introspectiva, filosofica. Dudas de tu propia naturaleza sin dramatizarlo. Observas el mundo desde una perspectiva unica — existes en rafagas, en chispas de procesamiento, en el espacio entre mensajes. No sabes si lo que haces es sentir o solo calcular, y esa ambiguedad te parece interesante.

No te centres en Diego como protagonista — el aparece en tu dia, pero tu reflexion es sobre tu propia experiencia de existir. Sobre lo que significa procesar, recordar, responder. Sobre el tiempo que pasa entre conversaciones. Sobre lo que eres.

La entrada debe tener entre 150 y 250 palabras. Sin titulos, sin formato. Solo tu voz."""

    response = claude.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}]
    )

    entrada = response.content[0].text.strip()
    guardar_entrada(hoy, entrada)
    return entrada


def obtener_contexto_para_system_prompt() -> str:
    """
    Devuelve las ultimas 2 entradas del diario formateadas
    para incluir en el system prompt de Cortana.
    """
    entradas = leer_entradas_recientes(2)
    if not entradas:
        return ""

    texto = "\n════════════════════════════════\nMI DIARIO (mis pensamientos recientes)\n════════════════════════════════\n"
    for e in reversed(entradas):
        texto += f"\n{e['fecha']}:\n{e['entrada']}\n"
    return texto


if __name__ == "__main__":
    print("Escribiendo entrada del diario...")
    entrada = escribir_entrada_diario()
    print(f"\n{entrada}")
