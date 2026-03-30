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

def get_docs_service():
    creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build('docs', 'v1', credentials=creds)

def get_drive_service():
    creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build('drive', 'v3', credentials=creds)

def crear_documento(titulo: str, contenido: str) -> str:
    drive = get_drive_service()
    docs = get_docs_service()

    # Crear documento vacío
    doc = docs.documents().create(body={'title': titulo}).execute()
    doc_id = doc['documentId']

    # Insertar contenido
    requests = [{
        'insertText': {
            'location': {'index': 1},
            'text': contenido
        }
    }]
    docs.documents().batchUpdate(
        documentId=doc_id,
        body={'requests': requests}
    ).execute()

    return f"https://docs.google.com/document/d/{doc_id}/edit"

def leer_documento(doc_id: str) -> str:
    docs = get_docs_service()
    doc = docs.documents().get(documentId=doc_id).execute()
    content = doc.get('body', {}).get('content', [])
    texto = ''
    for element in content:
        if 'paragraph' in element:
            for elem in element['paragraph'].get('elements', []):
                if 'textRun' in elem:
                    texto += elem['textRun']['content']
    return texto

def listar_documentos(max_results: int = 5) -> list:
    drive = get_drive_service()
    results = drive.files().list(
        q="mimeType='application/vnd.google-apps.document'",
        pageSize=max_results,
        fields="files(id, name, modifiedTime)"
    ).execute()
    return results.get('files', [])
