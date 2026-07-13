import random
import re
import pypdf
from urllib3 import request

from .models import PlantillaReporte, Proyecto, ReporteProgreso, Usuario, VersionDocumento, AnalisisIA, Revision, Notificacion,Comentario, Correccion,Carrera

from core import settings

from django.db import transaction
from django.db.models import Count, Q
from django.db import models

from django.shortcuts import render, redirect, get_object_or_404

from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib import messages

from django.utils import timezone

from django.http import JsonResponse
from django.views.decorators.http import require_GET

from django.conf import settings

from django.core.cache import cache
from django.core.mail import send_mail
import logging

#Se agregan las siguientes importaciones para manejar JSON y CSRF
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
import json
from gestion.copyleaks_service import enviar_documento_a_escanear
from .models import VersionDocumento, AnalisisIA

logger = logging.getLogger(__name__)

 
def calcular_progreso(proyecto, version_actual):
    version_anterior = proyecto.versiones.filter(numero_version__lt=version_actual.numero_version).last()
    if not version_anterior:
        return 0.0
    ultima_revision = version_anterior.revisiones.last()
    if not ultima_revision:
        return 0.0
    total_comentarios = ultima_revision.comentarios.count()
    if total_comentarios == 0:
        return 100.0 
    resueltos = ultima_revision.comentarios.filter(verificado_docente=True).count()
    return round((resueltos / total_comentarios) * 100, 2)

@login_required
def home_redirect(req):
    if req.user.rol == 'coordinador' or req.user.is_staff:
        return redirect('dashboard_coordinador')
    elif req.user.rol == 'profesor':
        return redirect('dashboard_profesor')
    else:
        return redirect('dashboard_estudiante')


@login_required
def dashboard_estudiante(req):
    proyectos = Proyecto.objects.filter(estudiantes=req.user).select_related('carrera')
    
    en_progreso = proyectos.filter(estado__in=['En Progreso', 'Observado']).count()
    aprobados = proyectos.filter(estado__in=['Aprobado', 'PROYECTO_APROBADO']).count()
    cancelados = proyectos.filter(estado='Cancelado').count()
    
    for proyecto in proyectos:
        proyecto.tiene_revisiones_previas = proyecto.versiones.filter(revisiones__isnull=False).exists()
        proyecto.estado_actual = proyecto.estado

        ultima_version = proyecto.versiones.last()
        proyecto.siguiente_comentario_id = None
        proyecto.hay_observaciones_pendientes = False
        proyecto.progreso_label = 'Progreso de Observaciones'

        def revision_comentarios_estado(revision):
            comentarios = revision.comentarios.all()
            return (comentarios.exists(), comentarios.exclude(estado='Resuelto').exists())

        if ultima_version:
            ultima_revision = ultima_version.revisiones.last()
            if ultima_revision:
                tiene_comentarios, pendientes = revision_comentarios_estado(ultima_revision)
                proyecto.hay_observaciones_pendientes = pendientes
                if tiene_comentarios:
                    if pendientes:
                        proyecto.progreso_label = 'Progreso de Observaciones'
                    else:
                        proyecto.progreso_label = 'Progreso de observaciones de la versión anterior'
                siguiente = ultima_revision.comentarios.exclude(estado='Resuelto').order_by('id').first()
                if siguiente:
                    proyecto.siguiente_comentario_id = siguiente.id
            else:
                version_anterior = proyecto.versiones.filter(numero_version__lt=ultima_version.numero_version).last()
                if version_anterior and version_anterior.revisiones.exists():
                    anterior_revision = version_anterior.revisiones.last()
                    tiene_comentarios, pendientes = revision_comentarios_estado(anterior_revision)
                    if tiene_comentarios and not pendientes:
                        proyecto.progreso_label = 'Progreso de observaciones de la versión anterior'
    
    return render(
        req, 'dashboard_estudiante.html',
        {
            'proyectos': proyectos,
            'proyectos_en_progreso': en_progreso,
            'proyectos_aprobados': aprobados,
            'proyectos_cancelados':cancelados,
            'total_investigaciones': proyectos.count()
        }
    )

@login_required
def subir_version(req, proyecto_id):
    proyecto = get_object_or_404(Proyecto, id=proyecto_id)
    
    if proyecto.estado in ['Aprobado', 'Cancelado', 'PROYECTO_APROBADO']:
        messages.error(req, "Este proyecto está cerrado y no admite nuevas entregas")
        return redirect("dashboard_estudiante")
    
    # --- PUNTO 2: ALERTA DE SOBRESCRITURA ---
    # Verificar si existe una versión anterior sin revisión
    ultima_version = proyecto.versiones.last()
    tiene_version_pendiente = False
    version_pendiente_numero = None
    
    if ultima_version:
        # Verificar si la última versión tiene revisiones
        tiene_revisiones = ultima_version.revisiones.exists()
        # Si no tiene revisiones, está pendiente de revisión
        if not tiene_revisiones:
            tiene_version_pendiente = True
            version_pendiente_numero = ultima_version.numero_version
        else:
            # Si tiene revisiones, verificar si alguna está en estado 'Pendiente' o similar
            ultima_revision = ultima_version.revisiones.last()
            if ultima_revision and ultima_revision.estado in ['Pendiente', 'En Progreso']:
                tiene_version_pendiente = True
                version_pendiente_numero = ultima_version.numero_version
    
    if req.method == 'POST':
        # --- PUNTO 2: VALIDACIÓN EN BACKEND ---
        # Verificar nuevamente en el POST para evitar bypass
        ultima_version_post = proyecto.versiones.last()
        if ultima_version_post:
            tiene_revisiones_post = ultima_version_post.revisiones.exists()
            if not tiene_revisiones_post:
                # Si hay una versión pendiente, mostramos error y redirigimos
                messages.warning(req, f"La versión v{ultima_version_post.numero_version} aún no ha sido revisada. Debes esperar a que tu profesor la revise antes de subir una nueva.")
                return redirect('dashboard_estudiante')
        
        archivo = req.FILES.get('archivo')
        resumen = req.POST.get('resumen')
        
        ids_resueltos = req.POST.getlist('tareas_completadas')
        
        # --- PUNTO 4: VALIDACIÓN DE OBSERVACIONES COMPLETAS ---
        tareas_pendientes_post = Comentario.objects.none()
        ultima_version_post = proyecto.versiones.last()
        if ultima_version_post and ultima_version_post.revisiones.exists():
            tareas_pendientes_post = ultima_version_post.revisiones.last().comentarios.filter(verificado_docente=False)
        if tareas_pendientes_post.exists() and len(ids_resueltos) != tareas_pendientes_post.count():
            messages.warning(req, 'Debes marcar todas las observaciones como resueltas antes de subir la nueva versión.')
            return redirect('subir_version', proyecto_id=proyecto.id)

        # --- PUNTO 4: SINCRONIZACIÓN DE PROGRESO CON EVIDENCIA ---
        # El progreso se recalcula AQUÍ, basado en los checkboxes que el estudiante marcó
        # al subir el nuevo documento
        
        nueva_version_numero = proyecto.versiones.count() + 1

        # Crear la nueva versión
        nueva_v = VersionDocumento.objects.create(
            proyecto=proyecto,
            numero_version=nueva_version_numero,
            archivo=archivo,
            resumen_cambios=resumen
        )

        # --- PUNTO 4: RECÁLCULO DEL PROGRESO ---
        # Si el estudiante marcó tareas como completadas, actualizamos su estado
        if ids_resueltos:
            Comentario.objects.filter(id__in=ids_resueltos).update(estado='Resuelto')
        
        # Calcular el nuevo progreso basado en la versión ANTERIOR (la que se está corrigiendo)
        # Buscar la versión anterior a la nueva
        version_anterior = proyecto.versiones.filter(numero_version__lt=nueva_version_numero).last()
        
        if version_anterior:
            ultima_revision_anterior = version_anterior.revisiones.last()
            if ultima_revision_anterior:
                total_comentarios = ultima_revision_anterior.comentarios.count()
                if total_comentarios > 0:
                    # Contar cuántos comentarios de la versión anterior están resueltos
                    resueltos = ultima_revision_anterior.comentarios.filter(estado='Resuelto').count()
                    proyecto.porcentaje_avance = round((resueltos / total_comentarios) * 100, 2)
                else:
                    proyecto.porcentaje_avance = 100.0
            else:
                proyecto.porcentaje_avance = 0.0
        else:
            # Primera versión, progreso base
            proyecto.porcentaje_avance = 0.0
        
        # Actualizar estado del proyecto
        proyecto.estado = 'En Progreso'
        proyecto.save()

        texto_extraido = ""
        try:
            reader = pypdf.PdfReader(archivo)
            for page in reader.pages:
                texto_extraido += page.extract_text() + "\n"
            print("--- TEXTO EXTRAIDO DEL PDF ---")
        except Exception as e:
            print(f"Error al leer PDF: {e}")
            
        # --- SOLUCIÓN: Llamada directa al servicio de Copyleaks ---
        identificador = f"proyecto_{proyecto.id}_v{nueva_v.id}"
        enviar_documento_a_escanear(identificador, nueva_v.archivo.path)
        
        # --- PUNTO 1: TRAZABILIDAD EN NOTIFICACIONES ---
        nombre_estudiante = req.user.get_full_name() or req.user.username
        mensaje_notif = f"{nombre_estudiante} subió v{nueva_v.numero_version} del proyecto: [{proyecto.titulo}]."

        for prof in proyecto.profesores.all():
            Notificacion.objects.create(usuario=prof, mensaje=mensaje_notif, estado='No leido')
            try:
                if prof.email:
                    asunto = f"[InvestiControl] Nueva entrega en '{proyecto.titulo}' (v{nueva_v.numero_version})"
                    send_mail(asunto, mensaje_notif, settings.DEFAULT_FROM_EMAIL, [prof.email], fail_silently=False)
                    logger.info("Email enviado: evento=subir_version usuario=%s proyecto=%s", prof.username, proyecto.id)
            except Exception as e:
                logger.exception("Error enviando email: %s", e)

        messages.success(req, f"¡Versión v{nueva_version_numero} subida correctamente!", extra_tags='no_login')
        return redirect('dashboard_estudiante')

    tareas_pendientes = []
    ultima_v = proyecto.versiones.last()
    if ultima_v and ultima_v.revisiones.exists():
        tareas_pendientes = ultima_v.revisiones.last().comentarios.filter(verificado_docente=False)

    return render(req, 'subir_version.html', {
        'proyecto': proyecto,
        'tareas_pendientes': tareas_pendientes,
        'tiene_version_pendiente': tiene_version_pendiente,
        'version_pendiente_numero': version_pendiente_numero
    })

@login_required
def disparar_analisis_ia(request, proyecto_id):
    proyecto = get_object_or_404(Proyecto, id=proyecto_id)
    version_actual = proyecto.versiones.last()

    if not version_actual or not version_actual.archivo:
        messages.error(request, "No hay archivo disponible.")
        return redirect('ver_historial', proyecto_id=proyecto.id)

    # Llamamos al servicio de Copyleaks
    from gestion.copyleaks_service import enviar_documento_a_escanear
    
    resultado = enviar_documento_a_escanear(
        id_tesis=f"proyecto_{proyecto.id}_v{version_actual.id}",
        ruta_archivo_pdf=version_actual.archivo.path
    )
    
    if resultado and resultado.get("exito"):
        messages.success(request, "¡Enviado a Copyleaks correctamente!")
    else:
        messages.error(request, "Error al contactar con Copyleaks.")
            
    return redirect('ver_historial', proyecto_id=proyecto.id)

def dashboard_profesor(req):
    if req.user.rol != 'profesor' and not req.user.is_staff:
        return redirect('dashboard_estudiante')
        
    proyectos = Proyecto.objects.filter(profesores=req.user)
    
    # --- PUNTO 3: VISIBILIDAD CONDICIONAL DEL PROGRESO ---
    # Enriquecer cada proyecto con flags útiles para el template
    for proyecto in proyectos:
        proyecto.tiene_revisiones_previas = proyecto.versiones.filter(revisiones__isnull=False).exists()
        proyecto.estado_actual = proyecto.estado
        proyecto.tiene_correciones_enviadas = False
        ultima_v = proyecto.versiones.last()
        if ultima_v and ultima_v.revisiones.exists() and proyecto.estado == 'Observado':
            proyecto.tiene_correciones_enviadas = True
    
    return render(req, 'dashboard_profesor.html', {'proyectos': proyectos})


@login_required
@transaction.atomic
def revisar_version(req, version_id):
    version = get_object_or_404(VersionDocumento, id=version_id)
    proyecto = version.proyecto
    
    if req.user not in proyecto.profesores.all() and not req.user.is_staff:
        return redirect('dashboard_profesor')
    
    version_anterior = proyecto.versiones.filter(numero_version__lt=version.numero_version).last()
    comentarios_anteriores = []
    if version_anterior and version_anterior.revisiones.exists():
        comentarios_anteriores = version_anterior.revisiones.last().comentarios.all().order_by('id')

    if req.method == 'POST':
        # Punto 11: Validar qué corrigió el alumno realmente de la entrega anterior
        ids_verificados = req.POST.getlist('verificar_comentarios')
        for ca in comentarios_anteriores:
            ca.verificado_docente = str(ca.id) in ids_verificados
            ca.save()

        estado = req.POST.get('estado')
        obs_general = req.POST.get('observaciones')
        
        # Punto 19.3: Observaciones "En Cadena"
        comentarios_lista = req.POST.getlist('comentarios_especificos_bulk')

        nueva_revision = Revision.objects.create(
            version=version, profesor=req.user, estado=estado, observaciones_generales=obs_general
        )

        nuevos_comentarios = 0
        for texto_obs in comentarios_lista:
            if texto_obs.strip():
                Comentario.objects.create(revision=nueva_revision, seccion="Especifica", texto=texto_obs.strip(), prioridad="Alta")
                nuevos_comentarios += 1

        # PUNTO 20.3: FÓRMULA DE PROGRESO MATEMÁTICO DE MIRI
        # El progreso debe resetearse cuando el profesor crea nuevas observaciones.
        if estado == 'Aprobado':
            proyecto.porcentaje_avance = 100.0
        elif nuevos_comentarios > 0:
            proyecto.porcentaje_avance = 0.0
        elif comentarios_anteriores:
            total_anterior = len(comentarios_anteriores)
            validados_hoy = len(ids_verificados)
            proyecto.porcentaje_avance = (validados_hoy / total_anterior) * 100
        else:
            proyecto.porcentaje_avance = 0.0

        proyecto.estado = estado 
        # Punto 21: Aprobación definitiva
        if estado == 'Aprobado' or req.POST.get('finalizar_investigacion') == 'on':
            proyecto.estado = 'PROYECTO_APROBADO'
            proyecto.porcentaje_avance = 100
            ReporteProgreso.objects.create(proyecto=proyecto, porcentaje_avance=100, observacion=f"Aprobación definitiva: Prof. {req.user.get_full_name()}.")
        
        proyecto.save()

        # --- PUNTO 1: TRAZABILIDAD EN NOTIFICACIONES ---
        nombre_profe = req.user.get_full_name() or req.user.username
        mensaje_notif = f"Tu v{version.numero_version} del proyecto [{proyecto.titulo}] fue revisada. Resultado: {estado}."

        for estudiante in proyecto.estudiantes.all():
            Notificacion.objects.create(usuario=estudiante, mensaje=mensaje_notif, estado='No leido')
            if estudiante.email:
                try:
                    send_mail(f"[InvestiControl] Revisión v{version.numero_version} - {proyecto.titulo}", mensaje_notif, settings.DEFAULT_FROM_EMAIL, [estudiante.email])
                except Exception: pass

        return redirect('dashboard_profesor')

    return render(req, 'revisar_version.html', {'version': version, 'comentarios_anteriores': comentarios_anteriores})

@login_required
def ver_notificaciones(req):
    notificaciones = Notificacion.objects.filter(usuario=req.user).order_by('-fecha_envio')
    return render(req, 'notificaciones.html', {'notificaciones': notificaciones})

@login_required
def marcar_notificacion_leida(req, notif_id):
    notif = get_object_or_404(Notificacion, id=notif_id, usuario=req.user)
    notif.estado = 'Leido'
    notif.save()
    return redirect('ver_notificaciones')


@login_required
def marcar_notificaciones_todas_leidas(req):
    Notificacion.objects.filter(usuario=req.user, estado='No leido').update(estado='Leido')
    return redirect('ver_notificaciones')

@login_required
def crear_proyecto(req):
    if req.user.rol != 'coordinador':
        return redirect('home')

    if req.method == 'POST':
        carrera = Carrera.objects.get(id=req.POST.get('carrera'))

        nuevo_p = Proyecto.objects.create(
            titulo=req.POST.get('titulo'),
            tipo=req.POST.get('tipo'),
            descripcion=req.POST.get('descripcion'),
            carrera=carrera
        )

        return redirect('proyecto_editar', proyecto_id=nuevo_p.id)

    return render(req, 'crear_proyecto.html', {'carreras': Carrera.objects.all()})

@login_required
def ver_historial(req, proyecto_id):
    proyecto = get_object_or_404(Proyecto, id=proyecto_id)
    if req.user not in proyecto.estudiantes.all() and req.user not in proyecto.profesores.all() and not req.user.is_staff:
        return redirect('home')
        
    versiones = proyecto.versiones.all().order_by('-fecha_subida')
    
    # Punto 2: Lógica de Anonimato para Artículos Científicos
    es_anonimo = proyecto.tutores_incognito and req.user.rol == 'estudiante'
    
    return render(req, 'historial_proyecto.html', {
        'proyecto': proyecto, 
        'versiones': versiones,
        'es_anonimo': es_anonimo # El HTML usará esto para mostrar 'Tutor 1' en vez del nombre
    })


@login_required
@transaction.atomic
def enviar_correccion(req, comentario_id):
    comentario = get_object_or_404(Comentario, id=comentario_id)
    revision = comentario.revision
    version = revision.version
    proyecto = version.proyecto

    if req.user not in proyecto.estudiantes.all():
        return redirect('home')

    # --- FIX 1: Si el comentario ya se resolvió (por acción del profe o clic previo), 
    # en lugar de lanzar alerta y mandarlo al dashboard, lo llevamos al siguiente pendiente.
    siguiente_pendiente = revision.comentarios.exclude(estado='Resuelto').order_by('id').first()
    
    if comentario.estado == 'Resuelto':
        if siguiente_pendiente:
            # Redirección silenciosa (sin mensaje de info/warning)
            return redirect('enviar_correccion', comentario_id=siguiente_pendiente.id)
        return redirect('subir_version', proyecto_id=proyecto.id)

    # --- FIX 2: Si el usuario entró a uno que no es el "siguiente", lo redirigimos al correcto 
    # pero SIN el messages.warning que dispara el modal molesto.
    if siguiente_pendiente and siguiente_pendiente.id != comentario.id:
        return redirect('enviar_correccion', comentario_id=siguiente_pendiente.id)

    if req.method == 'POST':
        descripcion = req.POST.get('descripcion')
        
        # --- ENFORCE: las observaciones deben cumplirse en orden ---
        comentarios_ordenados = list(revision.comentarios.order_by('id'))
        for c in comentarios_ordenados:
            if c.id == comentario.id:
                break
            if c.estado != 'Resuelto':

                return redirect('dashboard_estudiante')

        # Registrar la corrección (Tu lógica original intacta)
        Correccion.objects.update_or_create(
            comentario=comentario,
            defaults={
                'descripcion': descripcion,
                'estado': 'Corregido',
                'fecha_respuesta': timezone.now()
            }
        )
        comentario.estado = 'Resuelto'
        comentario.save()

        logger.info(f"Corrección registrada para comentario {comentario_id}.")

        # --- ACTUALIZAR PROGRESO (Tu lógica original intacta) ---
        try:
            total = revision.comentarios.count()
            if total > 0:
                resueltos = revision.comentarios.filter(estado='Resuelto').count()
                proyecto.porcentaje_avance = round((resueltos / total) * 100, 2)
            else:
                proyecto.porcentaje_avance = 100.0
            proyecto.save()
        except Exception:
            logger.exception('Error al recalcular porcentaje de avance tras corrección.')

        siguiente_pendiente = revision.comentarios.exclude(estado='Resuelto').order_by('id').first()
        if siguiente_pendiente:
            return render(req, 'enviar_correccion.html', {
                'comentario': comentario,
                'mostrar_exito': True,
                'mensaje_exito': 'Observación guardada. En breve volverás al listado de correcciones pendientes.'
            })

        # Forzar subida de nueva versión si todo está resuelto
        try:
            total = revision.comentarios.count()
            resueltos = revision.comentarios.filter(estado='Resuelto').count()
            if total > 0 and resueltos >= total:
                Notificacion.objects.create(usuario=req.user, mensaje=f"Completaste todas las observaciones en '{proyecto.titulo}'. Sube una nueva versión.", estado='No leido')
                return redirect('subir_version', proyecto_id=proyecto.id)
        except Exception:
            logger.exception('Error comprobando si todas las observaciones fueron resueltas.')

        # Notificación con Branding y Envío de Mail (Tu lógica original intacta)
        nombre_estudiante = req.user.get_full_name() or req.user.username
        mensaje_notif = f"{nombre_estudiante} resolvió una corrección en el proyecto: [{proyecto.titulo}]."

        for prof in proyecto.profesores.all():
            Notificacion.objects.create(usuario=prof, mensaje=mensaje_notif, estado='No leido')
            if prof.email:
                try:
                    send_mail(f"[InvestiControl] Tarea Resuelta: {proyecto.titulo}", mensaje_notif, settings.DEFAULT_FROM_EMAIL, [prof.email])
                except Exception: pass

        return redirect('dashboard_estudiante')
    
    return render(req, 'enviar_correccion.html', {'comentario': comentario})


@login_required
def dashboard_coordinador(req):
    if req.user.rol != 'coordinador' and not req.user.is_staff:
        return redirect('home')
        
    # 1. Estadísticas por Carrera (Punto 3: Asegurar estados para el gráfico)
    estadisticas = (
        Carrera.objects
        .annotate(
            total_proyectos=Count('proyecto'),
            aprobados=Count('proyecto', filter=models.Q(proyecto__estado__in=['Aprobado', 'PROYECTO_APROBADO'])),
            promedio_avance=models.Avg('proyecto__porcentaje_avance')
        )
        .order_by('nombre')
        .values('nombre', 'total_proyectos', 'promedio_avance', 'aprobados')
    )
    
    nombres_y_totales = [
        {**item, 'promedio_avance': round((item['promedio_avance'] or 0), 1)}
        for item in estadisticas
    ]
    
    # 2. Contadores Globales para las tarjetas (Punto 21: Incluye Cancelados)
    proyectos_todos = Proyecto.objects.all()
    total_global = proyectos_todos.count()
    en_progreso = proyectos_todos.filter(estado='En Progreso').count()
    aprobadas = proyectos_todos.filter(estado__in=['Aprobado', 'PROYECTO_APROBADO']).count()
    observadas = proyectos_todos.filter(estado='Observado').count()
    cancelados = proyectos_todos.filter(estado='Cancelado').count() # Nuevo contador solicitado
    
    proyectos_lista = proyectos_todos.order_by('-fecha_inicio')
    
    return render(req, 'dashboard_coordinador.html', {
        'nombres': nombres_y_totales,
        'total_global': total_global,
        'en_progreso': en_progreso,
        'aprobadas': aprobadas,
        'observadas': observadas,
        'cancelados': cancelados, # Pasamos los cancelados al front
        'proyectos_lista': proyectos_lista,
        'chart_data': None,
    })
@login_required
def cancelar_proyecto(req, proyecto_id):
    if req.user.rol != 'coordinador':
        return redirect('home')
    
    proyecto = get_object_or_404(Proyecto, id=proyecto_id)
    
    if req.method == 'POST':
        motivo_seleccionado = req.POST.get('motivo')
        otro_motivo = req.POST.get('otro_motivo')
        
        # Punto 21: Almacenar motivo real (si es 'Otro', usamos el texto libre)
        motivo_final = otro_motivo if motivo_seleccionado == 'Otro' else motivo_seleccionado
        
        # Trazabilidad Senior
        proyecto.estado = 'Cancelado'
        proyecto.motivo_cancelacion = motivo_final
        proyecto.cancelado_por = req.user
        # Registramos en la descripción para historial textual
        proyecto.descripcion += f"\n\n[CANCELACIÓN OFICIAL - {timezone.now()}]\nResponsable: {req.user.get_full_name()}\nMotivo: {motivo_final}"
        proyecto.save()

        # Notificar al equipo (Punto 21: Congelamiento de datos)
        mensaje = f"ATENCIÓN: El proyecto '{proyecto.titulo}' ha sido CANCELADO por la Coordinación Académica."
        usuarios_equipo = list(proyecto.estudiantes.all()) + list(proyecto.profesores.all())
        
        for u in usuarios_equipo:
            Notificacion.objects.create(usuario=u, mensaje=mensaje, estado='No leido')
            
        messages.warning(req, f"El proyecto '{proyecto.titulo}' ha sido cancelado y sus funciones inhabilitadas.")
        return redirect('dashboard_coordinador')
    
    return render(req, 'cancelar_proyecto_confirm.html', {'p': proyecto})   

@login_required
def ver_analisis_ia(req, version_id):
    version = get_object_or_404(VersionDocumento, id=version_id)
    analisis = getattr(version, 'analisis_ia', None)
    return render(req, 'ver_analisis_ia.html', {'version': version, 'analisis': analisis})

@login_required
def ver_perfil(req):
    if req.method == 'POST':
        action = req.POST.get('action')

        if req.FILES.get('avatar'):
            req.user.avatar = req.FILES.get('avatar')
            messages.success(req, "Foto de perfil actualizada.")

        if req.FILES.get('firma'):
            req.user.firma = req.FILES.get('firma')
            messages.success(req, "Firma digital actualizada.")

        if action == 'delete_avatar':
            if req.user.avatar:
                req.user.avatar.delete(save=False)
            req.user.avatar = None
            req.user.save(update_fields=['avatar'])
            return redirect(f"{req.path}?avatar=deleted")

        if action == 'delete_firma':
            if req.user.firma:
                req.user.firma.delete(save=False)
            req.user.firma = None
            req.user.save(update_fields=['firma'])
            return redirect(f"{req.path}?firma=deleted")
        req.user.save()
        return redirect('ver_perfil')
    context = {
        'proyectos_count': Proyecto.objects.filter(estudiantes=req.user).count() or Proyecto.objects.filter(profesores=req.user).count(),
        'revisiones_count': Revision.objects.filter(profesor=req.user).count(),
        'entregas_count': VersionDocumento.objects.filter(proyecto__estudiantes=req.user).count(),
        'notificaciones_count': req.user.notificaciones.count(),
    }

    return render(req, 'perfil.html', context)

@login_required
def generar_certificado(req, proyecto_id):
    proyecto = get_object_or_404(Proyecto, id=proyecto_id)
    if proyecto.estado != 'Aprobado':
        return redirect('dashboard_estudiante')
    ReporteProgreso.objects.get_or_create(
        proyecto=proyecto,
        porcentaje_avance=100,
        defaults={'observacion': 'Proyecto aprobado satisfactoriamente por el jurado.'}
    )
    return render(req, 'certificado_aprobacion.html', {'proyecto': proyecto})

@login_required
def gestionar_usuarios(req):
    if req.user.rol != 'coordinador':
        return redirect('home')
    usuarios = Usuario.objects.all()
    return render(req, 'gestionar_usuarios.html', {'usuarios': usuarios})

@login_required
def crear_usuario(req):
    if req.user.rol != 'coordinador':
        return redirect('home')
    if req.method == 'POST':
        first_name = req.POST.get('first_name') or req.POST.get('nombre') or ''
        last_name = req.POST.get('last_name') or req.POST.get('apellido') or ''
        if not first_name.strip():
            messages.error(req, 'El nombre es obligatorio.')
            return render(req, 'crear_usuario.html')
        Usuario.objects.create_user(
            username=req.POST.get('username'),
            password=req.POST.get('password'),
            rol=req.POST.get('rol'),
            first_name=first_name,
            last_name=last_name,
            email=req.POST.get('email') or ''
        )
        if req.POST.get('notificar'):
            email = (req.POST.get('email') or '').strip().lower()
            raw_password = req.POST.get('password')
            username = req.POST.get('username')
            if email:
                asunto = 'Credenciales de acceso - InvestiControl'
                mensaje = f"""
                        Hola {first_name} {last_name},

                        Tu cuenta en InvestiControl  ha sido creada.

                        Usuario: {username}
                        Contraseña: {raw_password}

                        Para ingresar: {settings.BASE_URL}/login

                        Saludos,
                        Dirección de Soporte Tecnológico | InvestiControl 
                    """
                try:
                    send_mail(
                        asunto,
                        mensaje,
                        settings.DEFAULT_FROM_EMAIL,
                        [email],
                        fail_silently=False,
                    )
                except Exception:
                    messages.warning(req, 'Usuario creado, pero no se pudo enviar el correo con las credenciales.')
        return redirect('gestionar_usuarios')
    return render(req, 'crear_usuario.html')


@login_required
def usuario_lista(req):
    if req.user.rol != 'coordinador': return redirect('home')
    usuarios = Usuario.objects.all().order_by('rol', 'username')
    return render(req, 'admin_entidades/usuario_lista.html', {'usuarios': usuarios})

@login_required
def usuario_editar(req, user_id):
    if req.user.rol != 'coordinador':
        return redirect('home')
    u = get_object_or_404(Usuario, id=user_id)
    if req.method == 'POST':
        u.first_name = req.POST.get('nombre')
        u.last_name = req.POST.get('apellido')
        u.email = req.POST.get('email')
        u.rol = req.POST.get('rol')
        u.estado = req.POST.get('estado')
        u.save()
        return redirect('usuario_lista')
    return render(req, 'admin_entidades/usuario_form.html', {'u': u})

@login_required
def carrera_lista(req):
    if req.user.rol != 'coordinador':
        return redirect('home')
    carreras = Carrera.objects.all()
    return render(req, 'admin_entidades/carrera_lista.html', {'carreras': carreras})

@login_required
def carrera_crear(req):
    if req.user.rol != 'coordinador':
        return redirect('home')
    if req.method == 'POST':
        Carrera.objects.create(nombre=req.POST.get('nombre'), facultad=req.POST.get('facultad'))
        return redirect('carrera_lista')
    return render(req, 'admin_entidades/carrera_form.html')

@login_required
def carrera_editar(req, carrera_id):
    if req.user.rol != 'coordinador':
        return redirect('home')
    c = get_object_or_404(Carrera, id=carrera_id)
    if req.method == 'POST':
        c.nombre = req.POST.get('nombre') or c.nombre
        c.facultad = req.POST.get('facultad') or c.facultad
        c.save()
        return redirect('carrera_lista')
    return render(req, 'admin_entidades/carrera_form.html', {'c': c})

@login_required
def proyecto_editar(req, proyecto_id):
    p = get_object_or_404(Proyecto, id=proyecto_id)
    
    if req.method == 'POST':
        p.titulo = req.POST.get('titulo') or p.titulo
        p.tipo = req.POST.get('tipo') or p.tipo
        p.estado = req.POST.get('estado') or p.estado
        p.descripcion = req.POST.get('descripcion') or p.descripcion
        
        # Punto 2: Guardar preferencia de anonimato
        p.tutores_incognito = req.POST.get('tutores_incognito') == 'on'
        
        carrera_id = req.POST.get('carrera')
        if carrera_id:
            p.carrera_id = carrera_id
        p.save()
        
        profesores_ids = req.POST.getlist('profesores')
        if profesores_ids:
            p.profesores.set(profesores_ids)
        estudiantes_ids = req.POST.getlist('estudiantes')
        if estudiantes_ids:
            p.estudiantes.set(estudiantes_ids, through_defaults={'rol': 'Autor Principal'})
            
        return redirect('dashboard_coordinador')
        
    context = {
        'p': p,
        'carreras': Carrera.objects.all(),
        'estudiantes': Usuario.objects.filter(rol='estudiante'),
        'profesores': Usuario.objects.filter(rol='profesor')
    }
    return render(req, 'admin_entidades/proyecto_form.html', context)


@login_required
def usuario_eliminar(req, user_id):
    if req.user.rol != 'coordinador': return redirect('home')
    u = get_object_or_404(Usuario, id=user_id)
    if u.id != req.user.id:
        u.delete()
    return redirect('usuario_lista')

@login_required
def carrera_eliminar(req, carrera_id):
    if req.user.rol != 'coordinador': return redirect('home')
    c = get_object_or_404(Carrera, id=carrera_id)
    c.delete()
    return redirect('carrera_lista')

@login_required
def generar_reporte_avance(req, proyecto_id):
    proyecto = get_object_or_404(Proyecto, id=proyecto_id)
    plantillas_disponibles = PlantillaReporte.objects.all()
    plantilla_id = req.GET.get('plantilla')
    if plantilla_id:
        plantilla = get_object_or_404(PlantillaReporte, id=plantilla_id)
    else:
        plantilla = plantillas_disponibles.filter(es_predeterminada=True).first()
        if not plantilla:
            plantilla = PlantillaReporte(nombre="Básica", encabezado="Institución", pie_pagina="Sistema", margen_superior=50)
    return render(req, 'reporte_avance.html', {
        'proyecto': proyecto,
        'plantilla': plantilla,
        'plantillas_disponibles': plantillas_disponibles,
        'fecha': timezone.now()
    })


def solicitar_otp(req):
    email_inicial = req.user.email if req.user.is_authenticated else ""

    if req.method == 'POST':
        email = req.POST.get('email').strip().lower()
        user = Usuario.objects.filter(email=email).first()
        if user:
            otp = str(random.randint(100000, 999999))
            cache.set(f"otp_reset_{email}", otp, timeout=600)
            
            # Branding: InvestiControl
            asunto = '🔑 Código de Seguridad - InvestiControl'
            mensaje = f"""
                Estimado/a {user.first_name or user.username},
                Se ha solicitado un restablecimiento de contraseña para su cuenta en InvestiControl.
                
                CÓDIGO: {otp} (Válido por 10 min)
                
                Atentamente,
                Soporte Tecnológico | InvestiControl
            """
            try:
                send_mail(asunto, mensaje, settings.DEFAULT_FROM_EMAIL, [email])
                return render(req, 'registration/password_reset.html', {'email_enviado': email})
            except Exception:
                return render(req, 'registration/password_reset.html', {'error': 'Error de envío.'})
        else:
            return render(req, 'registration/password_reset.html', {'error': 'Correo no registrado.'})
    return render(req, 'registration/password_reset.html', {'email_inicial': email_inicial})


def verificar_otp(req, email):
    if req.method == 'POST':
        otp_ingresado = req.POST.get('otp')
        nueva_pass = req.POST.get('password')
        otp_real = cache.get(f"otp_reset_{email}")
        if otp_real and otp_ingresado == otp_real:
            user = Usuario.objects.get(email=email)
            user.set_password(nueva_pass)
            user.save()
            cache.delete(f"otp_reset_{email}")
            messages.success(req, "La contraseña ha sido actualizada satisfactoriamente. Ya puede acceder.")
            return redirect('login')
        else:
            return render(req, 'registration/password_reset.html', {
                'email_enviado': email, 
                'error': 'El código ingresado es incorrecto o ha expirado. Verifique su bandeja de entrada.'
            })
    return render(req, 'registration/password_reset.html', {'email_enviado': email})

@login_required
def gestion_tesis_completa(req):
    if req.user.rol != 'coordinador':
        return redirect('home')
    proyectos = Proyecto.objects.all().order_by('-fecha_inicio')
    return render(req, 'admin_entidades/tesis_maestro.html', {'proyectos': proyectos})

@login_required
def plantilla_lista(req):
    if req.user.rol != 'coordinador':
        return redirect('home')
    plantillas = PlantillaReporte.objects.all()
    return render(req, 'admin_entidades/plantilla_lista.html', {'plantillas': plantillas})

@login_required
def plantilla_configurar(req, plantilla_id=None):
    if req.user.rol != 'coordinador':
        return redirect('home')
    plantilla = get_object_or_404(PlantillaReporte, id=plantilla_id) if plantilla_id else None
    if req.method == 'POST':
        datos = {
            'nombre': req.POST.get('nombre'),
            'encabezado': req.POST.get('encabezado'),
            'pie_pagina': req.POST.get('pie_pagina'),
            'margen_superior': req.POST.get('m_sup'),
            'margen_inferior': req.POST.get('m_inf'),
            'margen_derecho': req.POST.get('m_der'),
            'margen_izquierdo': req.POST.get('m_izq'),
            'es_predeterminada': req.POST.get('default') == 'on'
        }
        if plantilla:
            PlantillaReporte.objects.filter(id=plantilla_id).update(**datos)
        else:
            PlantillaReporte.objects.create(**datos)
        return redirect('plantilla_lista')
    return render(req, 'admin_entidades/plantilla_form.html', {'p': plantilla})

@login_required
def manual_interactivo(request):
    """
    Manual interactivo que se adapta al rol del usuario.
    """
    context = {
        'total_steps': 5,
        'total_steps_range': range(5),
        'support_email': settings.SUPPORT_EMAIL,
        # Para coordinadores
        'total_proyectos': Proyecto.objects.count(),
        'en_progreso': Proyecto.objects.filter(estado='En Progreso').count(),
        'aprobadas': Proyecto.objects.filter(estado__in=['Aprobado', 'PROYECTO_APROBADO']).count(),
        'observadas': Proyecto.objects.filter(estado='Observado').count(),
    }
    
    # Ajustar pasos según rol
    if request.user.rol == 'estudiante':
        context['total_steps'] = 5
        context['total_steps_range'] = range(5)
    elif request.user.rol == 'profesor':
        context['total_steps'] = 4
        context['total_steps_range'] = range(4)
    elif request.user.rol == 'coordinador':
        context['total_steps'] = 4
        context['total_steps_range'] = range(4)
    
    return render(request, 'manual_interactivo.html', context)

# --- MÓDULO DE INTEGRACIÓN COPYLEAKS ---

@csrf_exempt
def webhook_copyleaks(request, id_tesis, status):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            
            # --- Imprimir para debug ---
            print(f"WEBHOOK RECIBIDO para {id_tesis} con status: {status}")

            # Limpiar el ID. Por ejemplo: 'proyecto_4_v15-a1b2c3d4' -> 'proyecto_4_v15'
            identificador_limpio = id_tesis.split('-')[0]

            # Extraer el ID de la versión
            version_id = identificador_limpio.split('_v')[-1] 
            version = VersionDocumento.objects.get(id=version_id)

            # 1. MANEJO DE ERRORES: Si Copyleaks envía un error
            if 'error' in data:
                error_msg = data.get('error', {}).get('message', 'Error desconocido')
                AnalisisIA.objects.update_or_create(
                    version=version,
                    defaults={
                        'porcentaje_similitud': 0.0,
                        'riesgo_texto_ia': 0.0,
                        'resultado': f"Error: {error_msg}" 
                    }
                )
                return JsonResponse({'status': 'error guardado'}, status=200)

            # 2. MANEJO DE ÉXITO: Extracción y cálculo matemático
            results = data.get('results', {})
            score = results.get('score', {})
            
            # Obtener cantidad de palabras idénticas
            identical_words = score.get('identicalWords', 0)
            
            # Obtener el total de palabras (por defecto 1 para evitar error de división por cero)
            total_words = data.get('scannedDocument', {}).get('totalWords', 1)
            if total_words == 0:
                total_words = 1
                
            # Calcular porcentaje real de similitud (0 a 100)
            porcentaje_calculado = (identical_words / total_words) * 100
            
            # Score agregado (Copyleaks lo suele enviar como un número manejable)
            riesgo = score.get('aggregatedScore', 0) 
            
            # Guardamos los resultados redondeados a 2 decimales para proteger la base de datos
            AnalisisIA.objects.update_or_create(
                version=version,
                defaults={
                    'porcentaje_similitud': round(porcentaje_calculado, 2),
                    'riesgo_texto_ia': round(riesgo, 2),
                    'resultado': "Análisis completado"
                }
            )
            return JsonResponse({'status': 'ok'}, status=200)
            
        except Exception as e:
            print(f"Error crítico en webhook: {e}")
            return JsonResponse({'error': str(e)}, status=400)
            
    return JsonResponse({'error': 'Solo POST'}, status=405)