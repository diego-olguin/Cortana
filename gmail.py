import os
import base64
from email.mime.text import MIMEText
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

def get_gmail_service():
    creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build('gmail', 'v1', credentials=creds)

def get_drive_service():
    creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build('drive', 'v3', credentials=creds)

def _parsear_mensaje(service, msg_id: str) -> dict:
    data = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
    headers = data['payload']['headers']
    subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'Sin asunto')
    sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Desconocido')
    date = next((h['value'] for h in headers if h['name'] == 'Date'), '')
    snippet = data.get('snippet', '')
    labels = data.get('labelIds', [])
    return {
        'id': msg_id,
        'subject': subject,
        'from': sender,
        'date': date,
        'snippet': snippet,
        'leido': 'UNREAD' not in labels
    }

def leer_emails_no_leidos(max_results=5) -> list:
    service = get_gmail_service()
    result = service.users().messages().list(
        userId='me', labelIds=['INBOX', 'UNREAD'], maxResults=max_results
    ).execute()
    messages = result.get('messages', [])
    return [_parsear_mensaje(service, msg['id']) for msg in messages]

def buscar_emails(query: str, max_results: int = 5) -> list:
    """
    Busca correos por remitente, asunto, o cualquier termino.
    query puede ser: 'from:nombre@email.com', 'subject:texto', 'texto libre'
    """
    service = get_gmail_service()
    result = service.users().messages().list(
        userId='me', q=query, maxResults=max_results
    ).execute()
    messages = result.get('messages', [])
    return [_parsear_mensaje(service, msg['id']) for msg in messages]

def leer_email_completo(msg_id: str) -> str:
    """Lee el cuerpo completo de un correo por su ID."""
    service = get_gmail_service()
    data = service.users().messages().get(userId='me', id=msg_id, format='full').execute()

    def extraer_texto(payload):
        if payload.get('mimeType') == 'text/plain':
            body = payload.get('body', {}).get('data', '')
            if body:
                return base64.urlsafe_b64decode(body).decode('utf-8', errors='ignore')
        if 'parts' in payload:
            for part in payload['parts']:
                texto = extraer_texto(part)
                if texto:
                    return texto
        return ''

    return extraer_texto(data['payload'])

def enviar_email(to: str, subject: str, body: str) -> bool:
    service = get_gmail_service()
    message = MIMEText(body)
    message['to'] = to
    message['subject'] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    service.users().messages().send(userId='me', body={'raw': raw}).execute()
    return True

def compartir_drive(file_id: str, email: str, rol: str = 'reader') -> str:
    """
    Comparte un archivo o carpeta de Drive.
    rol puede ser: 'reader', 'commenter', 'writer'
    file_id: ID del archivo o carpeta de Drive
    """
    drive = get_drive_service()
    permission = {
        'type': 'user',
        'role': rol,
        'emailAddress': email
    }
    drive.permissions().create(
        fileId=file_id,
        body=permission,
        sendNotificationEmail=True
    ).execute()
    file = drive.files().get(fileId=file_id, fields='name,webViewLink').execute()
    return file.get('webViewLink', '')

def listar_drive(max_results: int = 10, tipo: str = None) -> list:
    """
    Lista archivos y carpetas de Drive.
    tipo: 'folder', 'document', 'spreadsheet', None (todos)
    """
    drive = get_drive_service()
    query = ""
    if tipo == 'folder':
        query = "mimeType='application/vnd.google-apps.folder'"
    elif tipo == 'document':
        query = "mimeType='application/vnd.google-apps.document'"
    elif tipo == 'spreadsheet':
        query = "mimeType='application/vnd.google-apps.spreadsheet'"

    params = {
        'pageSize': max_results,
        'fields': 'files(id, name, mimeType, modifiedTime, webViewLink)'
    }
    if query:
        params['q'] = query

    result = drive.files().list(**params).execute()
    return result.get('files', [])
