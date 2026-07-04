from django.core.management.base import BaseCommand
from gestion.models import Usuario, Carrera, Proyecto, ProyectoEstudiante, PlantillaReporte
from django.db import transaction

class Command(BaseCommand):
    help = 'Siembre datos iniciales para pruebas del sistema InvestiControl'

    def handle(self, *args, **kwargs):
        self.stdout.write(self.style.HTTP_INFO('Iniciando siembra de datos...'))
        
        with transaction.atomic():
            # 1. Crear Plantilla de Reporte (Para que los reportes funcionen de entrada)
            plantilla, _ = PlantillaReporte.objects.get_or_create(
                nombre="Formato Oficial Predefinido",
                defaults={
                    'encabezado': "REPÚBLICA BOLIVARIANA DE VENEZUELA",
                    'pie_pagina': "Sistema de Control de Revisiones - Validación Digital",
                    'margen_superior': 50,
                    'margen_inferior': 50,
                    'es_predeterminada': True
                }
            )

            # 2. Carrera de ejemplo
            carrera, _ = Carrera.objects.get_or_create(
                nombre="Ingeniería de Sistemas",
                facultad="Facultad de Ingeniería"
            )

            # 3. Super Administrador (Admin de TI)
            if not Usuario.objects.filter(username='admin').exists():
                Usuario.objects.create_superuser(
                    username='admin', email='admin@tesis.com', password='admin123',
                    rol='coordinador', first_name='Admin', last_name='General'
                )
                self.stdout.write(self.style.SUCCESS('✅ Superuser: admin / admin123'))

            # 4. Coordinador Académico
            coord, created = Usuario.objects.get_or_create(
                username='coord_mendoza',
                defaults={'email':'c.mendoza@univ.edu', 'rol':'coordinador', 'first_name':'Carlos', 'last_name':'Mendoza', 'is_staff':True}
            )
            if created: coord.set_password('coord123'); coord.save()

            # 5. Profesor / Tutor
            prof, created = Usuario.objects.get_or_create(
                username='profe_ana',
                defaults={'email':'ana.m@univ.edu', 'rol':'profesor', 'first_name':'Ana', 'last_name':'Martinez', 'is_staff':True}
            )
            if created: prof.set_password('prof123'); prof.save()

            # 6. Estudiante
            est, created = Usuario.objects.get_or_create(
                username='est_pedro',
                defaults={'email':'pedro.r@estu.edu', 'rol':'estudiante', 'first_name':'Pedro', 'last_name':'Rojas'}
            )
            if created: est.set_password('est123'); est.save()

            # 7. Proyecto vinculado con ROL (Requerimiento de la DB)
            if not Proyecto.objects.filter(titulo__icontains="Optimización").exists():
                proyecto = Proyecto.objects.create(
                    titulo="Optimización de Recursos mediante IA",
                    tipo="Tesis",
                    descripcion="Investigación sobre algoritmos aplicados a la gestión hídrica.",
                    carrera=carrera,
                    estado="En Progreso"
                )
                # Usamos el modelo intermedio para asignar el ROL
                ProyectoEstudiante.objects.create(
                    proyecto=proyecto,
                    estudiante=est,
                    rol="Autor Principal"
                )
                proyecto.profesores.add(prof)
                self.stdout.write(self.style.SUCCESS('✅ Proyecto y relación de roles creados'))

        self.stdout.write(self.style.SUCCESS('🚀 ¡Base de datos lista para demostración profesional!'))