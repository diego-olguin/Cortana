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

def get_sheets_service():
    creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build('sheets', 'v4', credentials=creds)


def explorar_sheet(spreadsheet_id: str) -> dict:
    """
    Lee la estructura completa del sheet:
    - Nombres de pestanas
    - Headers de cada pestana (primera fila con datos)
    - Cuantas filas tienen datos
    Devuelve un dict con toda la info para que Claude decida donde escribir.
    """
    service = get_sheets_service()
    spreadsheet = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()

    estructura = {
        "titulo": spreadsheet.get("properties", {}).get("title", ""),
        "pestanas": []
    }

    for sheet in spreadsheet.get("sheets", []):
        nombre = sheet["properties"]["title"]
        # Leer las primeras 3 filas para detectar headers
        try:
            result = service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id,
                range=f"{nombre}!A1:Z3"
            ).execute()
            primeras_filas = result.get("values", [])

            # Detectar cual fila tiene los headers (busca fila con mas texto)
            headers = []
            header_row = 1
            for i, fila in enumerate(primeras_filas):
                if len(fila) > len(headers):
                    headers = fila
                    header_row = i + 1

            # Contar filas con datos en columna A
            result_a = service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id,
                range=f"{nombre}!A:A"
            ).execute()
            todas_filas_a = result_a.get("values", [])
            ultima_fila_con_dato = len(todas_filas_a)

            estructura["pestanas"].append({
                "nombre": nombre,
                "headers": headers,
                "fila_headers": header_row,
                "ultima_fila_datos": ultima_fila_con_dato,
                "siguiente_fila_libre": ultima_fila_con_dato + 1
            })
        except Exception:
            estructura["pestanas"].append({
                "nombre": nombre,
                "headers": [],
                "fila_headers": 1,
                "ultima_fila_datos": 1,
                "siguiente_fila_libre": 2
            })

    return estructura


def leer_sheet(spreadsheet_id: str, rango: str) -> list:
    service = get_sheets_service()
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=rango
    ).execute()
    return result.get('values', [])


def escribir_en_sheet(spreadsheet_id: str, pestana: str, datos: dict, headers: list) -> int:
    """
    Escribe datos en el sheet de forma inteligente.
    - pestana: nombre exacto de la pestana
    - datos: dict con los valores {header: valor}
    - headers: lista de headers de esa pestana en orden
    Detecta automaticamente la siguiente fila libre y construye la fila en el orden correcto.
    """
    service = get_sheets_service()

    # Detectar siguiente fila libre buscando la primera columna con datos
    primera_columna = headers[0] if headers else "A"
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{pestana}!A:A"
    ).execute()
    filas_existentes = result.get("values", [])
    siguiente_fila = len(filas_existentes) + 1

    # Construir la fila en el orden de los headers
    fila = []
    for header in headers:
        # Buscar el valor en datos (insensible a mayusculas/acentos)
        valor = ""
        header_lower = header.lower().strip()
        for clave, val in datos.items():
            clave_lower = clave.lower().strip()
            if clave_lower == header_lower or clave_lower in header_lower or header_lower in clave_lower:
                valor = val
                break
        fila.append(valor)

    # Determinar rango de escritura
    col_final = chr(ord('A') + len(headers) - 1)
    rango_exacto = f"{pestana}!A{siguiente_fila}:{col_final}{siguiente_fila}"

    body = {'values': [fila]}
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=rango_exacto,
        valueInputOption='USER_ENTERED',
        body=body
    ).execute()

    return siguiente_fila


def escribir_sheet(spreadsheet_id: str, rango: str, valores: list) -> int:
    """
    Compatibilidad con llamadas anteriores.
    Detecta la siguiente fila libre en la pestana especificada.
    """
    service = get_sheets_service()
    pestana = rango.split('!')[0]

    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{pestana}!A:A"
    ).execute()
    filas_existentes = result.get("values", [])
    siguiente_fila = len(filas_existentes) + 1

    col_final = chr(ord('A') + len(valores[0]) - 1) if valores else 'E'
    rango_exacto = f"{pestana}!A{siguiente_fila}:{col_final}{siguiente_fila}"

    body = {'values': valores}
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=rango_exacto,
        valueInputOption='USER_ENTERED',
        body=body
    ).execute()

    return siguiente_fila


def actualizar_celda(spreadsheet_id: str, rango: str, valor) -> None:
    service = get_sheets_service()
    body = {'values': [[valor]]}
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=rango,
        valueInputOption='USER_ENTERED',
        body=body
    ).execute()
