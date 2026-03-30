import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = [
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/documents',
    'https://www.googleapis.com/auth/drive'
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
    body = {'values': valores}
    result = service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=rango,
        valueInputOption='USER_ENTERED',
        insertDataOption='INSERT_ROWS',
        body=body
    ).execute()
    return result

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
