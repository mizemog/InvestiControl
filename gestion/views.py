import random
import re
import pypdf

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

logger = logging.getLogger(__name__)


# --- BLOQUE 1: CORREGIDO ---
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
    
    if req.method == 'POST':
        archivo = req.FILES.get('archivo')
        resumen = req.POST.get('resumen')
        
        ids_resueltos = req.POST.getlist('tareas_completadas')
        if ids_resueltos:
            Comentario.objects.filter(id__in=ids_resueltos).update(estado='Resuelto')
        ultima_version = proyecto.versiones.count() + 1
        nueva_v = VersionDocumento.objects.create(
            proyecto=proyecto,
            numero_version=ultima_version,
            archivo=archivo,
            resumen_cambios=resumen
        )
        proyecto.porcentaje_avance = 0.00
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
        ejecutar_analisis_ia(texto_extraido, nueva_v.id)
        nombre_estudiante = req.user.get_full_name() or req.user.username
        mensaje_notif = f"{nombre_estudiante} subió el Documento de Entrega v{nueva_v.numero_version}."

        for prof in proyecto.profesores.all():
            Notificacion.objects.create(usuario=prof, mensaje=mensaje_notif, estado='No leido')
            try:
                if prof.email:
                    asunto = f"[InvestiControl] Nueva entrega en '{proyecto.titulo}' (v{nueva_v.numero_version})"
                    send_mail(asunto, mensaje_notif, settings.DEFAULT_FROM_EMAIL, [prof.email], fail_silently=False)
                    logger.info("Email enviado: evento=subir_version usuario=%s proyecto=%s", prof.username, proyecto.id)
            except Exception as e:
                logger.exception("Error enviando email: %s", e)

        return redirect('dashboard_estudiante')

    tareas_pendientes = []
    ultima_v = proyecto.versiones.last()
    if ultima_v and ultima_v.revisiones.exists():
        tareas_pendientes = ultima_v.revisiones.last().comentarios.filter(verificado_docente=False)

    return render(req, 'subir_version.html', {'proyecto': proyecto, 'tareas_pendientes': tareas_pendientes})

def ejecutar_analisis_ia(texto, version_id):
    version = VersionDocumento.objects.get(id=version_id)
    
    conteo_palabras = len(texto.split())
    
    AnalisisIA.objects.create(
        version=version, 
        resultado=texto,
        porcentaje_similitud=0.0,
        riesgo_texto_ia=0.0
    )
    
    print(f"✅ Análisis preparado: {conteo_palabras} palabras guardadas para la Fase 2.")
    
@login_required
def dashboard_profesor(req):
    if req.user.rol != 'profesor' and not req.user.is_staff:
        return redirect('dashboard_estudiante')
        
    proyectos = Proyecto.objects.filter(profesores=req.user)
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

        for texto_obs in comentarios_lista:
            if texto_obs.strip():
                Comentario.objects.create(revision=nueva_revision, seccion="Especifica", texto=texto_obs.strip(), prioridad="Alta")

        # PUNTO 20.3: FÓRMULA DE PROGRESO MATEMÁTICO DE MIRI
        # El progreso de esta versión se basa en lo verificado HOY sobre lo pedido AYER
        if comentarios_anteriores:
            total_anterior = len(comentarios_anteriores)
            validados_hoy = len(ids_verificados)
            proyecto.porcentaje_avance = (validados_hoy / total_anterior) * 100
        else:
            # Si es la primera versión, el progreso es 100% si se aprueba o 0% si se observa
            proyecto.porcentaje_avance = 100.0 if estado == 'Aprobado' else 0.0

        proyecto.estado = estado 
        # Punto 21: Aprobación definitiva
        if estado == 'Aprobado' or req.POST.get('finalizar_investigacion') == 'on':
            proyecto.estado = 'PROYECTO_APROBADO'
            proyecto.porcentaje_avance = 100
            ReporteProgreso.objects.create(proyecto=proyecto, porcentaje_avance=100, observacion=f"Aprobación definitiva: Prof. {req.user.get_full_name()}.")
        
        proyecto.save()

        nombre_profe = req.user.get_full_name() or req.user.username
        mensaje_notif = f"Tu Documento de Entrega v{version.numero_version} fue revisado. Resultado: {estado}."

        for estudiante in proyecto.estudiantes.all():
            Notificacion.objects.create(usuario=estudiante, mensaje=mensaje_notif, estado='No leido')
            if estudiante.email:
                try:
                    send_mail(f"[InvestiControl] Revisión v{version.numero_version}", mensaje_notif, settings.DEFAULT_FROM_EMAIL, [estudiante.email])
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

    if req.method == 'POST':
        descripcion = req.POST.get('descripcion')
        # Registrar la corrección
        Correccion.objects.update_or_create(
            comentario=comentario,
            defaults={
                'descripcion': descripcion,
                'estado': 'Corregido',
                'fecha_respuesta': timezone.now() # Punto 8: Trazabilidad horaria
            }
        )
        comentario.estado = 'Resuelto'
        comentario.save()

        # --- LÓGICA DE PROGRESO MATEMÁTICO (Punto 5 y 20.3) ---
        # El progreso se basa en las tareas del CICLO ACTIVO (esta revisión)
        total_tareas_ciclo = revision.comentarios.count()
        tareas_resueltas = revision.comentarios.filter(estado='Resuelto').count()
        
        if total_tareas_ciclo > 0:
            proyecto.porcentaje_avance = (tareas_resueltas / total_tareas_ciclo) * 100
            proyecto.save()
            
            # Punto 13: Alimentar Reporte_Progreso
            ReporteProgreso.objects.create(
                proyecto=proyecto,
                porcentaje_avance=proyecto.porcentaje_avance,
                observacion=f"Avance de Observaciones: {tareas_resueltas}/{total_tareas_ciclo} resueltas."
            )

        # Notificación con Branding InvestiControl
        nombre_estudiante = req.user.get_full_name() or req.user.username
        mensaje_notif = f"{nombre_estudiante} resolvió una corrección en '{proyecto.titulo}'."

        for prof in proyecto.profesores.all():
            Notificacion.objects.create(usuario=prof, mensaje=mensaje_notif, estado='No leido')
            if prof.email:
                try:
                    send_mail(f"[InvestiControl] Tarea Resuelta: {proyecto.titulo}", mensaje_notif, settings.DEFAULT_FROM_EMAIL, [prof.email])
                except Exception: pass

        return redirect('ver_historial', proyecto_id=proyecto.id)
    
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

# ... Copyleaks ...

from .copyleaks_service import enviar_documento_a_escanear

@login_required
def disparar_analisis_ia(request, proyecto_id):
    """
    Vista que conecta el botón de la web con el motor de Copyleaks.
    """
    proyecto = get_object_or_404(Proyecto, id=proyecto_id)
    
    # 1. Obtenemos la última versión de la tesis
    version_actual = proyecto.versiones.last()
    if not version_actual or not version_actual.archivo_pdf:
        messages.error(request, "No hay un archivo PDF válido para analizar.")
        return redirect('detalle_proyecto', proyecto_id=proyecto.id)

    # 2. Llamamos a nuestra función de IA (pasándole la ruta real del archivo)
    resultado = enviar_documento_a_escanear(
        id_tesis=f"proyecto_{proyecto.id}", 
        ruta_archivo_pdf=version_actual.archivo_pdf.path
    )

    # 3. Informamos al usuario
    if resultado.get("exito"):
        messages.success(request, "¡La tesis ha sido enviada a Copyleaks correctamente!")
    else:
        messages.error(request, f"Error al enviar a IA: {resultado.get('error')}")

    return redirect('detalle_proyecto', proyecto_id=proyecto.id)