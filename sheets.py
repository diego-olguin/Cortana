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

def leer_sheet(spreadsheet_id: str, rango: str):
    service = get_sheets_service()
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=rango
    ).execute()
    return result.get('values', [])

def escribir_sheet(spreadsheet_id: str, rango: str, valores: list):
    service = get_sheets_service()

    # Extraer nombre de pestana del rango (ej: "Marzo!A:E" -> "Marzo")
    pestana = rango.split('!')[0]

    # Leer columna A para encontrar la ultima fila con dato
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{pestana}!A:A"
    ).execute()
    filas_existentes = result.get('values', [])
    siguiente_fila = len(filas_existentes) + 1

    # Escribir exactamente en la siguiente fila disponible
    rango_exacto = f"{pestana}!A{siguiente_fila}:E{siguiente_fila}"
    body = {'values': valores}
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=rango_exacto,
        valueInputOption='USER_ENTERED',
        body=body
    ).execute()
    return siguiente_fila

def actualizar_sheet(spreadsheet_id: str, rango: str, valores: list):
    service = get_sheets_service()
    body = {'values': valores}
    result = service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=rango,
        valueInputOption='USER_ENTERED',
        body=body
    ).execute()
    return result
