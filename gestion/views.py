import random
import re
import pypdf
import secrets
import string

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
from django.core.files.base import ContentFile
from django.views.decorators.csrf import csrf_exempt
import logging
import io

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
@transaction.atomic
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
        # --- BIFURCACIÓN DE LÓGICA: DETECCIÓN DE ESTADO ---
        # Recuperar la última versión para decidir el flujo
        ultima_version_post = proyecto.versiones.last()
        
        # Verificar si estamos en "Modo Edición" (hay versión pendiente sin revisión)
        modo_edicion = False
        if ultima_version_post and not ultima_version_post.revisiones.exists():
            modo_edicion = True
        
        archivo = req.FILES.get('archivo')
        resumen = req.POST.get('resumen')
        
        # Validaciones básicas
        if not archivo:
            messages.error(req, "Debes seleccionar un archivo PDF para continuar.")
            return redirect('subir_version', proyecto_id=proyecto.id)
        
        if not resumen or len(resumen.strip()) < 10:
            messages.error(req, "El resumen de cambios debe tener al menos 10 caracteres.")
            return redirect('subir_version', proyecto_id=proyecto.id)
        
        ids_resueltos = req.POST.getlist('tareas_completadas')
        
        # --- VALIDACIÓN DE OBSERVACIONES COMPLETAS (solo si no estamos en modo edición) ---
        tareas_pendientes_post = Comentario.objects.none()
        if not modo_edicion and ultima_version_post and ultima_version_post.revisiones.exists():
            tareas_pendientes_post = ultima_version_post.revisiones.last().comentarios.filter(verificado_docente=False)
        
        if tareas_pendientes_post.exists() and len(ids_resueltos) != tareas_pendientes_post.count():
            messages.warning(req, 'Debes marcar todas las observaciones como resueltas antes de subir la nueva versión.')
            return redirect('subir_version', proyecto_id=proyecto.id)

        # --- CORREGIDO: Leer el PDF ANTES de guardar la versión ---
        # Guardar el contenido del archivo en memoria para poder leerlo después
        archivo_content = archivo.read()
        archivo.seek(0)  # Volver al inicio del archivo para que Django pueda guardarlo
        
        # Extraer texto del PDF desde el contenido en memoria
        texto_extraido = ""
        try:
            pdf_file = io.BytesIO(archivo_content)
            reader = pypdf.PdfReader(pdf_file)
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    texto_extraido += page_text + "\n"
            print(f"--- TEXTO EXTRAIDO DEL PDF ({len(texto_extraido)} caracteres) ---")
        except Exception as e:
            print(f"Error al leer PDF: {e}")
            logger.warning(f"Error al extraer texto del PDF: {e}")
            # No detenemos el proceso, solo registramos el error
        
        # --- PUNTO 1: ACTUALIZACIÓN VS CREACIÓN ---
        if modo_edicion:
            # "Modo Edición": Actualizar la versión existente en lugar de crear una nueva
            version_a_actualizar = ultima_version_post
            
            # Actualizar los campos de la versión existente
            version_a_actualizar.archivo.save(archivo.name, archivo, save=False)
            version_a_actualizar.resumen_cambios = resumen
            version_a_actualizar.fecha_subida = timezone.now()
            version_a_actualizar.save()
            
            nueva_v = version_a_actualizar
            nueva_version_numero = nueva_v.numero_version
            
            # --- PRESERVACIÓN DEL PROGRESO ---
            # No modificamos proyecto.porcentaje_avance, se mantiene el valor actual
            
            # Actualizar estado del proyecto solo si es necesario
            if proyecto.estado not in ['En Progreso', 'Observado']:
                proyecto.estado = 'En Progreso'
                proyecto.save(update_fields=['estado'])
            
            logger.info(f"Versión v{nueva_version_numero} actualizada en modo edición por {req.user.username}")
            
        else:
            # "Modo Creación": Flujo normal de creación de nueva versión
            nueva_version_numero = proyecto.versiones.count() + 1

            # Crear la nueva versión (Aquí nace nueva_v)
            nueva_v = VersionDocumento.objects.create(
                proyecto=proyecto,
                numero_version=nueva_version_numero,
                archivo=archivo,
                resumen_cambios=resumen
            )

            # --- RECÁLCULO DEL PROGRESO ---
            # Si el estudiante marcó tareas como completadas, actualizamos su estado
            if ids_resueltos:
                Comentario.objects.filter(id__in=ids_resueltos).update(estado='Resuelto')
            
            # Calcular el nuevo progreso basado en la versión ANTERIOR
            version_anterior = proyecto.versiones.filter(numero_version__lt=nueva_version_numero).last()
            
            if version_anterior:
                ultima_revision_anterior = version_anterior.revisiones.last()
                if ultima_revision_anterior:
                    total_comentarios = ultima_revision_anterior.comentarios.count()
                    if total_comentarios > 0:
                        resueltos = ultima_revision_anterior.comentarios.filter(estado='Resuelto').count()
                        proyecto.porcentaje_avance = round((resueltos / total_comentarios) * 100, 2)
                    else:
                        proyecto.porcentaje_avance = 100.0
                else:
                    proyecto.porcentaje_avance = 0.0
            else:
                proyecto.porcentaje_avance = 0.0
            
            proyecto.estado = 'En Progreso'
            proyecto.save(update_fields=['porcentaje_avance', 'estado'])
            
            logger.info(f"Nueva versión v{nueva_version_numero} creada por {req.user.username}")

        # === ENVIAR DIRECTAMENTE A COPYLEAKS ===
        # Se ejecuta después de que nueva_v fue creada o actualizada con éxito
        if texto_extraido.strip():
            # Eliminar análisis anterior si existe (para versión actualizada)
            if modo_edicion:
                AnalisisIA.objects.filter(version=nueva_v).delete()
                logger.info(f"Análisis IA anterior eliminado para v{nueva_version_numero}")
            
            # Traemos la función directamente aquí para que Python la reconozca de inmediato
            from gestion.copyleaks_service import enviar_documento_a_escanear
            
            identificador_base = f"proyecto_{proyecto.id}_v{nueva_v.id}"
            enviar_documento_a_escanear(identificador_base, nueva_v.archivo.path)
        else:
            logger.warning(f"No se pudo extraer texto del PDF para v{nueva_version_numero}")
        
        # --- NOTIFICACIONES INTELIGENTES ---
        nombre_estudiante = req.user.get_full_name() or req.user.username
        
        if modo_edicion:
            mensaje_notif = f"{nombre_estudiante} actualizó el documento de la v{nueva_v.numero_version} del proyecto: [{proyecto.titulo}]."
        else:
            mensaje_notif = f"{nombre_estudiante} subió v{nueva_v.numero_version} del proyecto: [{proyecto.titulo}]."

        profesores = proyecto.profesores.all()
        for prof in profesores:
            Notificacion.objects.create(usuario=prof, mensaje=mensaje_notif, estado='No leido')
            try:
                if prof.email:
                    if modo_edicion:
                        asunto = f"[InvestiControl] Documento actualizado en '{proyecto.titulo}' (v{nueva_v.numero_version})"
                    else:
                        asunto = f"[InvestiControl] Nueva entrega en '{proyecto.titulo}' (v{nueva_v.numero_version})"
                    send_mail(asunto, mensaje_notif, settings.DEFAULT_FROM_EMAIL, [prof.email], fail_silently=False)
                    logger.info("Email enviado: evento=subir_version usuario=%s proyecto=%s modo=%s", 
                              prof.username, proyecto.id, "edicion" if modo_edicion else "creacion")
            except Exception as e:
                logger.exception("Error enviando email a %s: %s", prof.email, e)

        # --- MENSAJES DE FEEDBACK ---
        if modo_edicion:
            messages.success(req, f"¡Versión v{nueva_version_numero} actualizada correctamente! El profesor será notificado del cambio.", extra_tags='no_login')
        else:
            messages.success(req, f"¡Versión v{nueva_version_numero} subida correctamente! Tu profesor será notificado.", extra_tags='no_login')
        
        return redirect('dashboard_estudiante')

    # GET: Preparar datos para el template
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

def ejecutar_analisis_ia(texto, version_id):
    try:
        version = VersionDocumento.objects.get(id=version_id)
        
        conteo_palabras = len(texto.split())
        
        AnalisisIA.objects.create(
            version=version, 
            resultado=texto,
            porcentaje_similitud=0.0,
            riesgo_texto_ia=0.0
        )
        
        print(f"✅ Análisis preparado: {conteo_palabras} palabras guardadas para la Fase 2.")
        logger.info(f"Análisis IA creado para versión {version_id}: {conteo_palabras} palabras")
    except Exception as e:
        logger.exception(f"Error en ejecutar_analisis_ia para versión {version_id}: {e}")
    
@login_required
def dashboard_profesor(req):
    if req.user.rol != 'profesor' and not req.user.is_staff:
        return redirect('dashboard_estudiante')
        
    proyectos = Proyecto.objects.filter(profesores=req.user)
    
    # --- PUNTO 3: VISIBILIDAD CONDICIONAL DEL PROGRESO ---
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
        ids_verificados = req.POST.getlist('verificar_comentarios')
        for ca in comentarios_anteriores:
            ca.verificado_docente = str(ca.id) in ids_verificados
            ca.save()

        estado = req.POST.get('estado')
        obs_general = req.POST.get('observaciones')
        
        comentarios_lista = req.POST.getlist('comentarios_especificos_bulk')

        nueva_revision = Revision.objects.create(
            version=version, profesor=req.user, estado=estado, observaciones_generales=obs_general
        )

        nuevos_comentarios = 0
        for texto_obs in comentarios_lista:
            if texto_obs.strip():
                Comentario.objects.create(revision=nueva_revision, seccion="Especifica", texto=texto_obs.strip(), prioridad="Alta")
                nuevos_comentarios += 1

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
        if estado == 'Aprobado' or req.POST.get('finalizar_investigacion') == 'on':
            proyecto.estado = 'PROYECTO_APROBADO'
            proyecto.porcentaje_avance = 100
            ReporteProgreso.objects.create(proyecto=proyecto, porcentaje_avance=100, observacion=f"Aprobación definitiva: Prof. {req.user.get_full_name()}.")
        
        proyecto.save()

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
    
    es_anonimo = proyecto.tutores_incognito and req.user.rol == 'estudiante'
    
    return render(req, 'historial_proyecto.html', {
        'proyecto': proyecto, 
        'versiones': versiones,
        'es_anonimo': es_anonimo
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

    siguiente_pendiente = revision.comentarios.exclude(estado='Resuelto').order_by('id').first()
    
    if comentario.estado == 'Resuelto':
        if siguiente_pendiente:
            return redirect('enviar_correccion', comentario_id=siguiente_pendiente.id)
        return redirect('subir_version', proyecto_id=proyecto.id)

    if siguiente_pendiente and siguiente_pendiente.id != comentario.id:
        return redirect('enviar_correccion', comentario_id=siguiente_pendiente.id)

    if req.method == 'POST':
        descripcion = req.POST.get('descripcion')
        
        comentarios_ordenados = list(revision.comentarios.order_by('id'))
        for c in comentarios_ordenados:
            if c.id == comentario.id:
                break
            if c.estado != 'Resuelto':
                return redirect('dashboard_estudiante')

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

        try:
            total = revision.comentarios.count()
            resueltos = revision.comentarios.filter(estado='Resuelto').count()
            if total > 0 and resueltos >= total:
                Notificacion.objects.create(usuario=req.user, mensaje=f"Completaste todas las observaciones en '{proyecto.titulo}'. Sube una nueva versión.", estado='No leido')
                return redirect('subir_version', proyecto_id=proyecto.id)
        except Exception:
            logger.exception('Error comprobando si todas las observaciones fueron resueltas.')

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
    # Permitir acceso a coordinador, profesor y staff
    if req.user.rol not in ['coordinador', 'profesor'] and not req.user.is_staff:
        return redirect('home')
        
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
    
    proyectos_todos = Proyecto.objects.all()
    total_global = proyectos_todos.count()
    en_progreso = proyectos_todos.filter(estado='En Progreso').count()
    aprobadas = proyectos_todos.filter(estado__in=['Aprobado', 'PROYECTO_APROBADO']).count()
    observadas = proyectos_todos.filter(estado='Observado').count()
    cancelados = proyectos_todos.filter(estado='Cancelado').count()
    
    proyectos_lista = proyectos_todos.order_by('-fecha_inicio')
    
    return render(req, 'dashboard_coordinador.html', {
        'nombres': nombres_y_totales,
        'total_global': total_global,
        'en_progreso': en_progreso,
        'aprobadas': aprobadas,
        'observadas': observadas,
        'cancelados': cancelados,
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
        
        motivo_final = otro_motivo if motivo_seleccionado == 'Otro' else motivo_seleccionado
        
        proyecto.estado = 'Cancelado'
        proyecto.motivo_cancelacion = motivo_final
        proyecto.cancelado_por = req.user
        proyecto.descripcion += f"\n\n[CANCELACIÓN OFICIAL - {timezone.now()}]\nResponsable: {req.user.get_full_name()}\nMotivo: {motivo_final}"
        proyecto.save()

        mensaje = f"ATENCIÓN: El proyecto '{proyecto.titulo}' ha sido CANCELADO por la Coordinación Académica."
        usuarios_equipo = list(proyecto.estudiantes.all()) + list(proyecto.profesores.all())
        
        for u in usuarios_equipo:
            Notificacion.objects.create(usuario=u, mensaje=mensaje, estado='No leido')
            
        messages.warning(req, f"El proyecto '{proyecto.titulo}' ha sido cancelado y sus funciones inhabilitadas.", extra_tags='gestion')
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
            messages.success(req, "Foto de perfil actualizada.", extra_tags='perfil')

        if req.FILES.get('firma'):
            req.user.firma = req.FILES.get('firma')
            messages.success(req, "Firma digital actualizada.", extra_tags='perfil')

        if action == 'delete_avatar':
            if req.user.avatar:
                req.user.avatar.delete(save=False)
            req.user.avatar = None
            req.user.save(update_fields=['avatar'])
            messages.success(req, "Foto de perfil eliminada.", extra_tags='perfil')
            return redirect(f"{req.path}?avatar=deleted")

        if action == 'delete_firma':
            if req.user.firma:
                req.user.firma.delete(save=False)
            req.user.firma = None
            req.user.save(update_fields=['firma'])
            messages.success(req, "Firma digital eliminada.", extra_tags='perfil')
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
        nuevo_estado = req.POST.get('estado')
        if nuevo_estado == 'Cancelado' and p.estado != 'Cancelado':
            messages.error(req, "No se puede cancelar el proyecto desde esta pantalla. Utilice el botón 'Cancelar Proyecto' en el Panel de Control para registrar el motivo correctamente.")
            return redirect('proyecto_editar', proyecto_id=proyecto.id)
        
        p.titulo = req.POST.get('titulo') or p.titulo
        p.tipo = req.POST.get('tipo') or p.tipo
        p.estado = nuevo_estado or p.estado
        p.descripcion = req.POST.get('descripcion') or p.descripcion
        
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
            # CORRECCIÓN DE SEGURIDAD
            otp = ''.join(secrets.choice(string.digits) for _ in range(6))
            cache.set(f"otp_reset_{email}", otp, timeout=600)
            
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
            except Exception as e:
                print("ERROR CRÍTICO AL ENVIAR CORREO:", e)  # <--- Esto lo mostrará en los logs
                return render(req, 'registration/password_reset.html', {'error': f'Error de envío: {e}'})
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
    # Permitir acceso a coordinador y profesor
    if req.user.rol not in ['coordinador', 'profesor']:
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
        'total_proyectos': Proyecto.objects.count(),
        'en_progreso': Proyecto.objects.filter(estado='En Progreso').count(),
        'aprobadas': Proyecto.objects.filter(estado__in=['Aprobado', 'PROYECTO_APROBADO']).count(),
        'observadas': Proyecto.objects.filter(estado='Observado').count(),
    }
    
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

@csrf_exempt
def webhook_copyleaks(request, id_tesis, status):
    if request.method == 'POST':
        try:
            import json
            data = json.loads(request.body)
            print(f"WEBHOOK RECIBIDO para {id_tesis} con status: {status}")

            # 1. Limpieza del ID: 'proyecto_10_v24-ba4862b5' -> 'proyecto_10_v24'
            identificador_limpio = id_tesis.split('-')[0]
            # Extraemos el número final: '24'
            version_id = identificador_limpio.split('_v')[-1] 
            version = VersionDocumento.objects.get(id=version_id)

            # 2. Si Copyleaks reporta un fallo en el documento
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
                return JsonResponse({'status': 'error registrado'}, status=200)

            # 3. Procesamiento de Éxito: Extracción de métricas
            results = data.get('results', {})
            score = results.get('score', {})
            
            identical_words = score.get('identicalWords', 0)
            total_words = data.get('scannedDocument', {}).get('totalWords', 1)
            
            # Red de seguridad contra divisiones por cero
            if total_words == 0: 
                total_words = 1
                
            # Regla matemática: (Palabras Idénticas / Palabras Totales) * 100 = Porcentaje Real
            porcentaje_calculado = (identical_words / total_words) * 100
            riesgo_ia = score.get('aggregatedScore', 0) 
            
            # Guardado o actualización aplicando round() a 2 decimales
            AnalisisIA.objects.update_or_create(
                version=version,
                defaults={
                    'porcentaje_similitud': round(porcentaje_calculado, 2),
                    'riesgo_texto_ia': round(riesgo_ia, 2),
                    'resultado': "Análisis completado"
                }
            )
            return JsonResponse({'status': 'procesado ok'}, status=200)
            
        except Exception as e:
            print(f"Error crítico en el webhook de Copyleaks: {e}")
            return JsonResponse({'error': str(e)}, status=400)
            
    return JsonResponse({'error': 'Método no permitido'}, status=405)

@login_required
def disparar_analisis_ia(request, proyecto_id):
    proyecto = get_object_or_404(Proyecto, id=proyecto_id)
    version_actual = proyecto.versiones.last()

    if not version_actual or not version_actual.archivo:
        messages.error(request, "No hay archivo disponible para analizar.")
        return redirect('ver_historial', proyecto_id=proyecto.id)

    # Importación interna para evitar dependencias circulares
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
