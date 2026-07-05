import os
import requests
import base64
import json

def obtener_token_copyleaks():
    """
    Se comunica con Copyleaks usando tus credenciales del .env 
    para obtener un 'token' (un pase temporal de 48 horas) para usar la API.
    """
    # Traemos las claves secretas desde el entorno virtual (.env)
    email = os.environ.get('COPYLEAKS_EMAIL')
    api_key = os.environ.get('COPYLEAKS_API_KEY')

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
        response.raise_for_status() # Verifica si hubo algún error en la conexión
        
        datos = response.json()
        return datos.get("access_token")
        
    except requests.exceptions.RequestException as e:
        print(f"Error al conectar con Copyleaks: {e}")
        return None

def enviar_documento_a_escanear(id_tesis, ruta_archivo_pdf):
    """
    Toma un PDF guardado en tu sistema, lo convierte a formato base64 
    y se lo envía a Copyleaks para que busque plagio e IA.
    """
    token = obtener_token_copyleaks()
    
    if not token:
        return {"error": "No se pudo obtener autorización de Copyleaks"}

    # 1. Convertimos el PDF a texto cifrado (Base64) que Copyleaks pueda leer
    try:
        with open(ruta_archivo_pdf, "rb") as archivo_pdf:
            pdf_base64 = base64.b64encode(archivo_pdf.read()).decode('utf-8')
    except FileNotFoundError:
        return {"error": "No se encontró el archivo PDF en el sistema."}

    # 2. Preparamos el envío
    # Usamos 'education' para buscar plagio + IA de forma exhaustiva
    url = f"https://api.copyleaks.com/v3/education/submit/file/{id_tesis}"
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }

    # 3. Configuramos la petición a la API
    # NOTA: Cambiaremos la URL del webhook cuando publiquemos la app en internet
    payload = {
        "base64": pdf_base64,
        "filename": f"Tesis_{id_tesis}.pdf",
        "properties": {
            "sandbox": True, # ¡SÚPER IMPORTANTE! Esto evita que gastes créditos reales mientras probamos
            "aiGeneratedText": {
                "detect": True # Activa la detección de ChatGPT/IA
            },
            "webhooks": {
                # Aquí Copyleaks tocará la puerta cuando termine de analizar (lo configuraremos luego)
                "status": f"https://tusistema.com/gestion/webhook/copyleaks/{id_tesis}/{{STATUS}}/"
            }
        }
    }

    try:
        response = requests.put(url, headers=headers, json=payload)
        # Si responde código 201, significa que lo recibió y empezó a analizar
        if response.status_code == 201:
            return {"exito": True, "mensaje": "Documento enviado a análisis correctamente."}
        else:
            return {"exito": False, "error": response.text}
            
    except requests.exceptions.RequestException as e:
        return {"exito": False, "error": str(e)}