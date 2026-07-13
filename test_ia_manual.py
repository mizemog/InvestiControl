import os
import sys
import django

# 1. Configuración de rutas para que Django reconozca el proyecto
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

# 2. Importaciones de modelos
from gestion.models import VersionDocumento

def buscar_y_auditar_archivos_pdf():
    """
    Busca archivos PDF físicamente en el directorio y verifica si están registrados
    en la tabla VersionDocumento de la base de datos.
    """
    print("--- Diagnóstico: Auditoría de archivos vs Base de Datos ---")
    carpeta_base = os.getcwd()
    
    archivos_encontrados = []
    
    # 1. Búsqueda física de PDFs
    for raiz, directorios, archivos in os.walk(carpeta_base):
        for archivo in archivos:
            if archivo.endswith(".pdf"):
                ruta_completa = os.path.join(raiz, archivo)
                archivos_encontrados.append(ruta_completa)
    
    print(f"Se encontraron {len(archivos_encontrados)} archivos PDF en disco.")
    
    # 2. Auditoría con Base de Datos
    registros_db = VersionDocumento.objects.all()
    rutas_en_db = [v.archivo.name for v in registros_db if v.archivo]
    
    print(f"Registros encontrados en tabla VersionDocumento: {registros_db.count()}")
    
    for ruta in archivos_encontrados:
        nombre_archivo = os.path.basename(ruta)
        # Verificamos si el nombre del archivo está en la base de datos
        esta_en_db = any(nombre_archivo in ruta_db for ruta_db in rutas_en_db)
        
        status = "✅ REGISTRADO" if esta_en_db else "❌ HUÉRFANO (NO REGISTRADO)"
        print(f"Archivo: {nombre_archivo[:30]}... | Estado: {status}")

if __name__ == "__main__":
    buscar_y_auditar_archivos_pdf()