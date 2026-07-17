from django.urls import path, include
from . import views

urlpatterns = [
    # Endpoints auxiliares para gráficos/charts (usados por el dashboard del coordinador)
    path('', include('gestion.urls_chart_patch')),

    path('', views.home_redirect, name='home'),
    path('estudiante/', views.dashboard_estudiante, name='dashboard_estudiante'),
    path('profesor/', views.dashboard_profesor, name='dashboard_profesor'),
    path('coordinador/', views.dashboard_coordinador, name='dashboard_coordinador'),
    path('subir-version/<int:proyecto_id>/', views.subir_version, name='subir_version'),
    path('revisar/<int:version_id>/', views.revisar_version, name='revisar_version'),
    path('historial/<int:proyecto_id>/', views.ver_historial, name='ver_historial'),
    path('notificaciones/', views.ver_notificaciones, name='ver_notificaciones'),
    path('notificaciones/leida/<int:notif_id>/', views.marcar_notificacion_leida, name='marcar_leida'),
    path('notificaciones/marcar-todas-leidas/', views.marcar_notificaciones_todas_leidas, name='marcar_todas_leidas'),
    path('nuevo-proyecto/', views.crear_proyecto, name='crear_proyecto'),
    path('corregir/<int:comentario_id>/', views.enviar_correccion, name='enviar_correccion'),
    
    # Detalle del análisis e inicio manual del análisis IA
    path('analisis-ia/<int:version_id>/', views.ver_analisis_ia, name='ver_analisis_ia'),
    # === ESTA ES LA RUTA QUE FALTABA REGISTRAR ===
    path('analizar-ia/<int:proyecto_id>/', views.disparar_analisis_ia, name='disparar_analisis_ia'),
    
    path('perfil/', views.ver_perfil, name='ver_perfil'),
    path('certificado/<int:proyecto_id>/', views.generar_certificado, name='generar_certificado'),
    
    # Usuarios
    path('usuarios/', views.usuario_lista, name='usuario_lista'),
    path('gestion-usuarios/', views.gestionar_usuarios, name='gestionar_usuarios'),
    path('usuarios/editar/<int:user_id>/', views.usuario_editar, name='usuario_editar'),
    path('usuarios/crear/', views.crear_usuario, name='crear_usuario'),
    path('usuarios/eliminar/<int:user_id>/', views.usuario_eliminar, name='usuario_eliminar'),
    
    # Carreras y Reportes
    path('carreras/', views.carrera_lista, name='carrera_lista'),
    path('carreras/nueva/', views.carrera_crear, name='carrera_crear'),
    path('carreras/editar/<int:carrera_id>/', views.carrera_editar, name='carrera_editar'),
    path('carreras/eliminar/<int:carrera_id>/', views.carrera_eliminar, name='carrera_eliminar'),

    path('proyecto/editar/<int:proyecto_id>/', views.proyecto_editar, name='proyecto_editar'),
    path('proyecto/reporte-avance/<int:proyecto_id>/', views.generar_reporte_avance, name='reporte_avance'),
    
    # --- MÓDULO DE PLANTILLAS ---
    path('plantillas/', views.plantilla_lista, name='plantilla_lista'),
    path('plantillas/nueva/', views.plantilla_configurar, name='plantilla_crear'),
    path('plantillas/editar/<int:plantilla_id>/', views.plantilla_configurar, name='plantilla_editar'),

    # Password Reset
    path('recuperar-password/', views.solicitar_otp, name='enviar_otp'),
    path('verificar-codigo/<str:email>/', views.verificar_otp, name='verificar_otp'),
    
    path('manual/', views.manual_interactivo, name='manual_interactivo'),
    # Gestión de Tesis para Coordinador
    path('gestion-tesis-maestro/', views.gestion_tesis_completa, name='gestion_tesis_maestro'),
    path('cancelar/<int:proyecto_id>/', views.cancelar_proyecto, name='cancelar_proyecto'),
    path('webhook/copyleaks/<str:id_tesis>/<str:status>/', views.webhook_copyleaks, name='webhook_copyleaks'),
]