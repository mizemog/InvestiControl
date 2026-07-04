from django.db import models
from django.contrib.auth.models import AbstractUser

class Usuario(AbstractUser):
    ROLES = (
        ('estudiante', 'Estudiante'),
        ('profesor', 'Profesor'),
        ('coordinador', 'Coordinador Academico'),
    )
    rol = models.CharField(max_length=20, choices=ROLES, default='estudiante')
    estado = models.CharField(max_length=20, default='Activo')
    firma = models.ImageField(upload_to='firmas/', null=True, blank=True)
    avatar = models.ImageField(upload_to='avatars/', null=True, blank=True)

    def contar_notificaciones_sin_leer(self):
        return self.notificaciones.filter(estado='No leido').count()

class Carrera(models.Model):
    FACULTADES = (
        ('Salud', 'Ciencias de la Salud'),
        ('Ingenieria', 'Ingeniería'),
        ('Juridicas', 'Ciencias Jurídicas y Políticas'),
        ('Economicas', 'Ciencias Económicas y Sociales'),
        ('Educacion', 'Ciencias de la Educación'),
        ('Odontologia', 'Odontología'),
        ('Tecnologia', 'Ciencias y Tecnología'),
        ('Otra', 'Otra'),
    )
    nombre = models.CharField(max_length=100)
    facultad = models.CharField(max_length=50, choices=FACULTADES, default='Otra')

    def __str__(self):
        return f"{self.nombre} ({self.facultad})"

class ProyectoEstudiante(models.Model):
    proyecto = models.ForeignKey('Proyecto', on_delete=models.CASCADE)
    estudiante = models.ForeignKey(Usuario, on_delete=models.CASCADE)
    rol = models.CharField(max_length=50, default="Autor Principal")

    class Meta:
        db_table = 'gestion_proyecto_estudiantes'

class Proyecto(models.Model):
    TIPOS = (
        ('Tesis', 'Tesis de Grado'),
        ('Articulo', 'Artículo Científico'),
        ('Proyecto', 'Proyecto de Investigación'),
        ('Monografia', 'Monografía'),
    )

    ESTADOS = (
        ('En Progreso', 'En Progreso'),
        ('Observado', 'Observado'),
        ('Aprobado', 'Aprobado'),
        ('PROYECTO_APROBADO', 'Finalizado Satisfactoriamente'),
        ('Cancelado', 'Cancelado'),
    )

    titulo = models.CharField(max_length=255)
    tipo = models.CharField(max_length=50, choices=TIPOS, default='Tesis')
    descripcion = models.TextField()
    estado = models.CharField(max_length=50, choices=ESTADOS, default='En Progreso')
    fecha_inicio = models.DateField(auto_now_add=True)
    porcentaje_avance = models.DecimalField(max_digits=5, decimal_places=2, default=0.0)
    carrera = models.ForeignKey(Carrera, on_delete=models.CASCADE)
    
    tutores_incognito = models.BooleanField(default=False, help_text="Ocultar nombres de tutores en la revisión")
    motivo_cancelacion = models.TextField(null=True, blank=True)
    cancelado_por = models.ForeignKey(Usuario, on_delete=models.SET_NULL, null=True, blank=True, related_name='proyectos_cancelados')

    estudiantes = models.ManyToManyField(Usuario, through=ProyectoEstudiante, related_name='proyectos_estudiante')
    profesores = models.ManyToManyField(Usuario, related_name='proyectos_profesores')

    def __str__(self):
        return self.titulo

class VersionDocumento(models.Model):
    proyecto = models.ForeignKey(Proyecto, on_delete=models.CASCADE, related_name='versiones')
    numero_version = models.IntegerField()
    archivo = models.FileField(upload_to='documentos/versiones/')
    resumen_cambios = models.TextField()
    fecha_subida = models.DateTimeField(auto_now_add=True)

class AnalisisIA(models.Model):
    version = models.OneToOneField(VersionDocumento, on_delete=models.CASCADE, related_name='analisis_ia')
    porcentaje_similitud = models.DecimalField(max_digits=5, decimal_places=2, default=0.0)
    riesgo_texto_ia = models.DecimalField(max_digits=5, decimal_places=2, default=0.0)
    resultado = models.TextField(blank=True)
    fecha_analisis = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Analisis v{self.version.numero_version}"

class Revision(models.Model):
    version = models.ForeignKey(VersionDocumento, on_delete=models.CASCADE, related_name='revisiones')
    profesor = models.ForeignKey(Usuario, on_delete=models.CASCADE, limit_choices_to={'rol': 'profesor'})
    estado = models.CharField(max_length=50)
    observaciones_generales = models.TextField()
    fecha_revision = models.DateTimeField(auto_now_add=True) # Punto 8: Trazabilidad con fecha y hora

class Comentario(models.Model):
    revision = models.ForeignKey(Revision, on_delete=models.CASCADE, related_name='comentarios')
    seccion = models.CharField(max_length=100)
    texto = models.TextField()
    prioridad = models.CharField(max_length=20, default='Media') 
    estado = models.CharField(max_length=20, default='Pendiente')
    verificado_docente = models.BooleanField(default=False)
    
class Correccion(models.Model):
    comentario = models.OneToOneField(Comentario, on_delete=models.CASCADE, related_name='correccion')
    descripcion = models.TextField()
    estado = models.CharField(max_length=20, default='Enviada')
    fecha_respuesta = models.DateTimeField(auto_now_add=True) # Punto 8: Fecha y hora exacta

class Notificacion(models.Model):
    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name='notificaciones')
    mensaje = models.TextField()
    estado = models.CharField(max_length=20, default='No leido')
    fecha_envio = models.DateTimeField(auto_now_add=True)

class ReporteProgreso(models.Model):
    proyecto = models.ForeignKey(Proyecto, on_delete=models.CASCADE, related_name='reportes')
    porcentaje_avance = models.DecimalField(max_digits=5, decimal_places=2)
    observacion = models.TextField()
    fecha_reporte = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Reporte {self.proyecto.titulo} - {self.porcentaje_avance}%"

class PlantillaReporte(models.Model):
    nombre = models.CharField(max_length=100)
    encabezado = models.TextField(help_text="Texto o HTML del membrete superior")
    pie_pagina = models.TextField(help_text="Texto o HTML del pie de página")
    margen_superior = models.IntegerField(default=50)
    margen_inferior = models.IntegerField(default=50)
    margen_derecho = models.IntegerField(default=50)
    margen_izquierdo = models.IntegerField(default=50)
    es_predeterminada = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        if self.es_predeterminada:
            PlantillaReporte.objects.filter(es_predeterminada=True).update(es_predeterminada=False)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.nombre