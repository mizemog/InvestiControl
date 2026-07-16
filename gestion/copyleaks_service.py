import os
import requests
import base64
import json
import time
import uuid  # Importamos uuid para generar identificadores únicos

def obtener_token_copyleaks():
    """
    Se comunica con Copyleaks usando tus credenciales del .env 
    para obtener un 'token' (un pase temporal de 48 horas) para usar la API.
    """
    email = os.environ.get('COPYLEAKS_EMAIL')
    api_key = os.environ.get('COPYLEAKS_API_KEY')

    if not email or not api_key:
        print("Error: Faltan credenciales de Copyleaks en el archivo .env")
        return None

    url = "https://id.copyleaks.com/v3/account/login/api"
    
    payload = {
        "email": email,
        "key": api_key
    }
    
    headers = {
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status() 
        
        datos = response.json()
        return datos.get("access_token")
        
    except requests.exceptions.RequestException as e:
        print(f"Error al conectar con Copyleaks: {e}")
        if 'response' in locals() and response is not None:
            print(f"Detalle de la respuesta: {response.text}")
        return None

def enviar_documento_a_escanear(id_tesis, ruta_archivo_pdf):
    """
    Toma un PDF, le asigna un ID único para evitar bloqueos de Copyleaks
    y lo envía a análisis.
    """
    token = obtener_token_copyleaks()
    
    if not token:
        return {"error": "No se pudo obtener autorización de Copyleaks."}

    # 1. Convertimos el PDF a base64
    try:
        with open(ruta_archivo_pdf, "rb") as archivo_pdf:
            pdf_base64 = base64.b64encode(archivo_pdf.read()).decode('utf-8')
    except FileNotFoundError:
        return {"error": "No se encontró el archivo PDF en el sistema."}

    # 2. Generamos un ID único usando UUID para evitar colisiones en Copyleaks
    # El ID final será algo como: "proyecto_4_v15-a1b2c3d4"
    unique_id = f"{id_tesis}-{uuid.uuid4().hex[:8]}"
    url = f"https://api.copyleaks.com/v3/education/submit/file/{unique_id}"
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }

    # 3. Configuramos la petición
    TUNEL_URL = "https://kdnfx-38-183-114-107.free.pinggy.net"
    
    payload = {
        "base64": pdf_base64,
        "filename": f"Tesis_{id_tesis}.pdf",
        "properties": {
            "sandbox": False, 
            "aiGeneratedText": {
                "detect": True
            },
            "webhooks": {
                "status": f"{TUNEL_URL}/gestion/webhook/copyleaks/{unique_id}/{{status}}/"
            }
        }
    }

    try:
        response = requests.put(url, headers=headers, json=payload)
        if response.status_code == 201:
            return {"exito": True, "mensaje": "Documento enviado a análisis correctamente."}
        else:
            return {"exito": False, "error": f"Error {response.status_code}: {response.text}"}
            
    except requests.exceptions.RequestException as e:
        return {"exito": False, "error": str(e)}

def consultar_estado_analisis(id_tesis):
    try:
        token = obtener_token_copyleaks()
        headers = {"Authorization": f"Bearer {token}"}
        url = f"https://api.copyleaks.com/v3/scans/{id_tesis}" 
        
        response = requests.get(url, headers=headers)
        return response.json()
    except Exception as e:
        return {"error": str(e)}