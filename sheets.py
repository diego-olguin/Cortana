import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = [
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/documents',
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/calendar'
]

CATEGORIAS_VALIDAS = [
    "COMIDAS", "TRANSPORTE", "TRABAJO", "Gastos hormiga", "GASTOS FIJOS",
    "OTRO", "TDC", "Salud", "Gym", "SALIDAS DIVERTIDAS", "Gato",
    "DEUDAS", "Dates", "Inversiones", "Comidas fuera", "Marihuana", "MUNCHIES"
]

# Rangos fijos del sheet
EGRESOS_FILA_INICIO = 3
EGRESOS_FILA_FIN = 264

INGRESOS_EXTRA_FILA_INICIO = 26
INGRESOS_EXTRA_FILA_FIN = 51
INGRESOS_EXTRA_COL_DESC = "G"
INGRESOS_EXTRA_COL_DEBEN = "H"
INGRESOS_EXTRA_COL_PAGADO = "I"

INGRESOS_FIJOS_FILA_INICIO = 5
INGRESOS_FIJOS_FILA_FIN = 24
INGRESOS_FIJOS_COL_DESC = "G"
INGRESOS_FIJOS_COL_DEBEN = "H"
INGRESOS_FIJOS_COL_PAGADO = "I"


def get_sheets_service():
    creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build('sheets', 'v4', credentials=creds)


def normalizar_categoria(categoria_input: str) -> str:
    cat_lower = categoria_input.lower().strip()
    for cat in CATEGORIAS_VALIDAS:
        if cat.lower() == cat_lower:
            return cat
        if cat_lower in cat.lower() or cat.lower() in cat_lower:
            return cat
    return "OTRO"


def encontrar_siguiente_fila_vacia(service, spreadsheet_id: str, pestana: str,
                                   columna: str, fila_inicio: int, fila_fin: int) -> int:
    """
    Busca la primera fila vacia en el rango especificado.
    Devuelve numero de fila (1-indexed).
    """
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{pestana}!{columna}{fila_inicio}:{columna}{fila_fin}"
    ).execute()
    valores = result.get("values", [])

    for i, fila in enumerate(valores):
        if not fila or not str(fila[0]).strip():
            return fila_inicio + i

    raise ValueError(f"No hay espacio disponible en el rango ({columna}{fila_inicio}:{columna}{fila_fin}).")


def registrar_egreso(spreadsheet_id: str, pestana: str, categoria: str, descripcion: str,
                     importe: float, fecha: str, metodo: str) -> int:
    """
    Registra un gasto en A3:E264 en orden cronologico.
    A=CATEGORIA, B=DESCRIPCION, C=IMPORTE, D=FECHA, E=METODO
    """
    service = get_sheets_service()
    categoria_valida = normalizar_categoria(categoria)

    fila = encontrar_siguiente_fila_vacia(
        service, spreadsheet_id, pestana,
        "B", EGRESOS_FILA_INICIO, EGRESOS_FILA_FIN
    )

    rango = f"{pestana}!A{fila}:E{fila}"
    valores = [[categoria_valida, descripcion, importe, fecha, metodo]]
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=rango,
        valueInputOption="USER_ENTERED",
        body={"values": valores}
    ).execute()

    return fila


def registrar_ingreso_extra(spreadsheet_id: str, pestana: str, descripcion: str,
                             monto_deben: float, monto_pagado: float) -> int:
    """
    Registra un ingreso freelance en G26:I51.
    G=Descripcion, H=Deben (pendiente por cobrar), I=Pagado (ya cobrado)
    """
    service = get_sheets_service()

    fila = encontrar_siguiente_fila_vacia(
        service, spreadsheet_id, pestana,
        INGRESOS_EXTRA_COL_DESC,
        INGRESOS_EXTRA_FILA_INICIO,
        INGRESOS_EXTRA_FILA_FIN
    )

    rango = f"{pestana}!{INGRESOS_EXTRA_COL_DESC}{fila}:{INGRESOS_EXTRA_COL_PAGADO}{fila}"
    valores = [[descripcion, monto_deben if monto_deben else "", monto_pagado if monto_pagado else ""]]
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=rango,
        valueInputOption="USER_ENTERED",
        body={"values": valores}
    ).execute()

    return fila


def registrar_ingreso_fijo(spreadsheet_id: str, pestana: str, descripcion: str,
                            monto_deben: float, monto_pagado: float) -> int:
    """
    Registra un ingreso fijo (cliente recurrente) en G5:I24.
    G=Descripcion, H=Deben, I=Pagado
    """
    service = get_sheets_service()

    fila = encontrar_siguiente_fila_vacia(
        service, spreadsheet_id, pestana,
        INGRESOS_FIJOS_COL_DESC,
        INGRESOS_FIJOS_FILA_INICIO,
        INGRESOS_FIJOS_FILA_FIN
    )

    rango = f"{pestana}!{INGRESOS_FIJOS_COL_DESC}{fila}:{INGRESOS_FIJOS_COL_PAGADO}{fila}"
    valores = [[descripcion, monto_deben if monto_deben else "", monto_pagado if monto_pagado else ""]]
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=rango,
        valueInputOption="USER_ENTERED",
        body={"values": valores}
    ).execute()

    return fila


def leer_sheet(spreadsheet_id: str, rango: str) -> list:
    service = get_sheets_service()
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=rango
    ).execute()
    return result.get('values', [])


def explorar_sheet(spreadsheet_id: str) -> dict:
    service = get_sheets_service()
    spreadsheet = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    return {
        "titulo": spreadsheet.get("properties", {}).get("title", ""),
        "pestanas": [s["properties"]["title"] for s in spreadsheet.get("sheets", [])]
    }


def escribir_sheet(spreadsheet_id: str, rango: str, valores: list) -> int:
    service = get_sheets_service()
    pestana = rango.split('!')[0]
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"{pestana}!A:A"
    ).execute()
    filas = result.get("values", [])
    siguiente_fila = len(filas) + 1
    col_final = chr(ord('A') + len(valores[0]) - 1) if valores else 'E'
    rango_exacto = f"{pestana}!A{siguiente_fila}:{col_final}{siguiente_fila}"
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=rango_exacto,
        valueInputOption='USER_ENTERED',
        body={'values': valores}
    ).execute()
    return siguiente_fila


def escribir_en_sheet(spreadsheet_id: str, pestana: str, datos: dict, headers: list) -> int:
    return escribir_sheet(spreadsheet_id, f"{pestana}!A:E", [list(datos.values())])
