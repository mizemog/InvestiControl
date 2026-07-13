import os
import sys
import django

# Diagnóstico de entorno inmediato
print(f"DEBUG: Python está ejecutándose desde: {sys.executable}")
print(f"DEBUG: El script está intentando abrirse en la carpeta: {os.getcwd()}")
print(f"DEBUG: Archivos en esta carpeta: {os.listdir('.')}")

# 1. Configuración de rutas para que Django reconozca el proyecto
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

# 2. Importaciones de tus modelos y servicios
from gestion.models import Proyecto, VersionDocumento
from gestion.copyleaks_service import enviar_documento_a_escanear

# ==========================================
# CONFIGURACIÓN DE PRUEBA
# ==========================================
ID_PROYECTO_PRUEBA = 10 

try:
    print(f"--- Iniciando diagnóstico TOTAL para el Proyecto ID: {ID_PROYECTO_PRUEBA} ---")
    
    if not Proyecto.objects.filter(id=ID_PROYECTO_PRUEBA).exists():
        print(f"Error: El proyecto con ID {ID_PROYECTO_PRUEBA} no existe en la base de datos.")
    else:
        proyecto = Proyecto.objects.get(id=ID_PROYECTO_PRUEBA)
        print(f"Proyecto localizado: {proyecto.titulo}")

        versiones = VersionDocumento.objects.filter(proyecto_id=ID_PROYECTO_PRUEBA).order_by('-fecha_subida')
        total_versiones = versiones.count()
        
        print(f"Total de versiones encontradas en tabla VersionDocumento para este ID: {total_versiones}")
        
        if total_versiones > 0:
            version = versiones.first()
            print(f"Última versión detectada: v{version.numero_version}")
            
            if hasattr(version, 'archivo') and version.archivo:
                path_archivo = version.archivo.path
                print(f"Ruta del archivo: {path_archivo}")
                
                if os.path.exists(path_archivo):
                    print("¡Archivo confirmado en disco! Enviando a Copyleaks...")
                    resultado = enviar_documento_a_escanear(
                        id_tesis=f"proyecto_{proyecto.id}", 
                        ruta_archivo_pdf=path_archivo
                    )
                    print("RESULTADO:", resultado)
                else:
                    print(f"Error: El archivo no existe en el disco en: {path_archivo}")
            else:
                print("Error: La versión existe pero el campo 'archivo' está vacío.")
        else:
            print("Error: No hay registros en la tabla VersionDocumento asociados a este ID de proyecto.")

except Exception as e:
    print(f"Error técnico inesperado: {e}")