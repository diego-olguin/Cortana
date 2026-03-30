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

EGRESOS_FILA_INICIO = 3
EGRESOS_FILA_FIN = 264

INGRESOS_EXTRA_FILA_INICIO = 26
INGRESOS_EXTRA_FILA_FIN = 51

INGRESOS_FIJOS_FILA_INICIO = 5
INGRESOS_FIJOS_FILA_FIN = 24


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


def encontrar_siguiente_fila_egreso(service, spreadsheet_id: str, pestana: str) -> int:
    """
    Busca la ULTIMA fila con dato en la columna B dentro del rango B3:B264.
    Escribe en la fila siguiente a esa (orden cronologico hacia abajo).
    """
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{pestana}!B{EGRESOS_FILA_INICIO}:B{EGRESOS_FILA_FIN}"
    ).execute()
    valores = result.get("values", [])

    ultima_fila_con_dato = None
    for i, fila in enumerate(valores):
        if fila and str(fila[0]).strip():
            ultima_fila_con_dato = EGRESOS_FILA_INICIO + i

    if ultima_fila_con_dato is None:
        # No hay datos aun, empezar desde el inicio del rango
        return EGRESOS_FILA_INICIO

    siguiente = ultima_fila_con_dato + 1
    if siguiente > EGRESOS_FILA_FIN:
        raise ValueError(f"No hay espacio en el rango de egresos (A{EGRESOS_FILA_INICIO}:E{EGRESOS_FILA_FIN}).")

    return siguiente


def encontrar_siguiente_fila_ingreso(service, spreadsheet_id: str, pestana: str,
                                      fila_inicio: int, fila_fin: int) -> int:
    """
    Busca la ULTIMA fila con dato en la columna G dentro del rango dado.
    Escribe en la siguiente fila.
    """
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{pestana}!G{fila_inicio}:G{fila_fin}"
    ).execute()
    valores = result.get("values", [])

    ultima_fila_con_dato = None
    for i, fila in enumerate(valores):
        if fila and str(fila[0]).strip():
            ultima_fila_con_dato = fila_inicio + i

    if ultima_fila_con_dato is None:
        return fila_inicio

    siguiente = ultima_fila_con_dato + 1
    if siguiente > fila_fin:
        raise ValueError(f"No hay espacio en el rango de ingresos (G{fila_inicio}:I{fila_fin}).")

    return siguiente


def registrar_egreso(spreadsheet_id: str, pestana: str, categoria: str, descripcion: str,
                     importe: float, fecha: str, metodo: str) -> int:
    """
    Registra un gasto en A3:E264 despues del ultimo dato existente.
    A=CATEGORIA, B=DESCRIPCION, C=IMPORTE, D=FECHA, E=METODO
    """
    service = get_sheets_service()
    categoria_valida = normalizar_categoria(categoria)
    fila = encontrar_siguiente_fila_egreso(service, spreadsheet_id, pestana)

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
    G=Descripcion, H=Deben, I=Pagado
    """
    service = get_sheets_service()
    fila = encontrar_siguiente_fila_ingreso(
        service, spreadsheet_id, pestana,
        INGRESOS_EXTRA_FILA_INICIO, INGRESOS_EXTRA_FILA_FIN
    )
    rango = f"{pestana}!G{fila}:I{fila}"
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
    Registra un ingreso fijo en G5:I24.
    G=Descripcion, H=Deben, I=Pagado
    """
    service = get_sheets_service()
    fila = encontrar_siguiente_fila_ingreso(
        service, spreadsheet_id, pestana,
        INGRESOS_FIJOS_FILA_INICIO, INGRESOS_FIJOS_FILA_FIN
    )
    rango = f"{pestana}!G{fila}:I{fila}"
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
