from django.test import TestCase, Client
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core import mail
from django.core.cache import cache
from .models import *

class SistemaTesisMasterSuite(TestCase):

    def setUp(self):
        # 1. Datos Base
        self.carrera = Carrera.objects.create(nombre="Sistemas", facultad="Ingeniería")
        self.admin = Usuario.objects.create_superuser(username='admin', email='admin@t.com', password='123', rol='coordinador')
        self.profesor = Usuario.objects.create_user(username='profe', email='p@t.com', password='123', rol='profesor')
        self.estudiante = Usuario.objects.create_user(username='alumno', email='e@t.com', password='123', rol='estudiante')
        self.hacker = Usuario.objects.create_user(username='hacker', email='h@t.com', password='123', rol='estudiante')

        # 2. Proyecto con relación intermedia
        self.proyecto = Proyecto.objects.create(titulo="Tesis Pro", carrera=self.carrera)
        ProyectoEstudiante.objects.create(proyecto=self.proyecto, estudiante=self.estudiante, rol="Investigador")
        self.proyecto.profesores.add(self.profesor)

    # --- PRUEBAS DE SEGURIDAD Y ROLES ---
    def test_rabc_security(self):
        """Verifica que los roles no puedan acceder a rutas prohibidas"""
        self.client.login(username='alumno', password='123')
        # Estudiante intentando entrar a panel de coordinador
        response = self.client.get(reverse('dashboard_coordinador'))
        self.assertEqual(response.status_code, 302) # Redirigido (Bloqueado)

    def test_password_reset_redis_flow(self):
        """Prueba completa de recuperación con Redis y Correo virtual"""
        # Solicitud
        self.client.post(reverse('enviar_otp'), {'email': 'e@t.com'})
        self.assertEqual(len(mail.outbox), 1)
        
        otp = cache.get(f"otp_reset_e@t.com")
        self.assertIsNotNone(otp)

        # Verificación y cambio
        response = self.client.post(reverse('verificar_otp', args=['e@t.com']), {
            'otp': otp, 'password': 'new_password_pro'
        })
        self.estudiante.refresh_from_db()
        self.assertTrue(self.estudiante.check_password('new_password_pro'))

    # --- PRUEBAS DE FLUJO DE TESIS ---
    def test_upload_and_auto_analysis(self):
        """Prueba subida de PDF y creación automática de registro IA"""
        self.client.login(username='alumno', password='123')
        pdf = SimpleUploadedFile("tesis.pdf", b"content", content_type="application/pdf")
        
        self.client.post(reverse('subir_version', args=[self.proyecto.id]), {
            'archivo': pdf, 'resumen': 'Avance 1'
        })
        
        version = VersionDocumento.objects.first()
        self.assertIsNotNone(version)
        # Verificar que se creó el Análisis IA (Stub para Miri)
        self.assertTrue(AnalisisIA.objects.filter(version=version).exists())

    def test_approval_and_auto_report_generation(self):
        """Verifica que al aprobar se genere el Reporte de Progreso automáticamente"""
        # Subir versión primero
        v = VersionDocumento.objects.create(proyecto=self.proyecto, numero_version=1, resumen_cambios="v1")
        
        self.client.login(username='profe', password='123')
        self.client.post(reverse('revisar_version', args=[v.id]), {
            'estado': 'Aprobado', 'observaciones': 'Excelente'
        })

        self.proyecto.refresh_from_db()
        self.assertEqual(self.proyecto.estado, 'Aprobado')
        # Verificar creación de ReporteProgreso (Automatización Pág 5)
        self.assertTrue(ReporteProgreso.objects.filter(proyecto=self.proyecto).exists())
        # Verificar notificación
        self.assertTrue(Notificacion.objects.filter(usuario=self.estudiante).exists())

    # --- PRUEBAS DE ADMINISTRACIÓN ---
    def test_coordinator_crud(self):
        """Prueba que el coordinador puede crear usuarios y proyectos"""
        self.client.login(username='admin', password='123')
        
        # Crear Carrera
        response = self.client.post(reverse('carrera_crear'), {'nombre': 'Derecho', 'facultad': 'Leyes'})
        self.assertEqual(Carrera.objects.filter(nombre='Derecho').count(), 1)

        # Eliminar Usuario
        user_to_del = Usuario.objects.create_user(username='todel', password='1')
        self.client.post(reverse('usuario_eliminar', args=[user_to_del.id]))
        self.assertFalse(Usuario.objects.filter(username='todel').exists())

    def test_report_visual_rendering(self):
        """Verifica que el reporte dinámico cargue sin errores"""
        self.client.login(username='admin', password='123')
        # Crear plantilla predeterminada
        PlantillaReporte.objects.create(nombre="Default", es_predeterminada=True)
        
        response = self.client.get(reverse('reporte_avance', args=[self.proyecto.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.proyecto.titulo)