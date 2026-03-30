import os
from datetime import datetime, timedelta
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

def get_calendar_service():
    creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build('calendar', 'v3', credentials=creds)

def crear_evento(titulo: str, fecha_inicio: str, fecha_fin: str,
                 descripcion: str = "", invitados: list = [], recordatorio_minutos: int = 30) -> str:
    """
    fecha_inicio y fecha_fin en formato ISO: '2026-03-30T10:00:00'
    invitados: lista de emails ['email@ejemplo.com']
    """
    service = get_calendar_service()

    attendees = [{'email': email} for email in invitados]

    evento = {
        'summary': titulo,
        'description': descripcion,
        'start': {
            'dateTime': fecha_inicio,
            'timeZone': 'America/Mexico_City'
        },
        'end': {
            'dateTime': fecha_fin,
            'timeZone': 'America/Mexico_City'
        },
        'attendees': attendees,
        'reminders': {
            'useDefault': False,
            'overrides': [
                {'method': 'email', 'minutes': recordatorio_minutos},
                {'method': 'popup', 'minutes': 10}
            ]
        },
        'sendUpdates': 'all'
    }

    resultado = service.events().insert(calendarId='primary', body=evento).execute()
    return resultado.get('htmlLink', '')

def listar_eventos(dias: int = 7) -> list:
    service = get_calendar_service()

    ahora = datetime.utcnow().isoformat() + 'Z'
    limite = (datetime.utcnow() + timedelta(days=dias)).isoformat() + 'Z'

    resultado = service.events().list(
        calendarId='primary',
        timeMin=ahora,
        timeMax=limite,
        maxResults=10,
        singleEvents=True,
        orderBy='startTime'
    ).execute()

    eventos = resultado.get('items', [])
    lista = []
    for e in eventos:
        inicio = e['start'].get('dateTime', e['start'].get('date'))
        lista.append({
            'titulo': e.get('summary', 'Sin titulo'),
            'inicio': inicio,
            'descripcion': e.get('description', ''),
            'invitados': [a['email'] for a in e.get('attendees', [])]
        })
    return lista

def eliminar_evento(evento_id: str):
    service = get_calendar_service()
    service.events().delete(calendarId='primary', eventId=evento_id).execute()
