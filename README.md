# 🚀 TesisControl v2.0 - Gestión Académica Inteligente

[![Django](https://img.shields.io/badge/Django-5.2-092e20?style=for-the-badge&logo=django)](https://www.djangoproject.com/)
[![Docker](https://img.shields.io/badge/Docker-SaaS-2496ed?style=for-the-badge&logo=docker)](https://www.docker.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-336791?style=for-the-badge&logo=postgresql)](https://www.postgresql.org/)
[![Redis](https://img.shields.io/badge/Redis-Cache-dc382d?style=for-the-badge&logo=redis)](https://redis.io/)

**TesisControl** es una plataforma SaaS integral diseñada para automatizar el ciclo de vida de los trabajos de investigación académicos. Desde la carga inicial hasta la aprobación final, el sistema garantiza trazabilidad, seguridad y eficiencia en la comunicación entre estudiantes, profesores y coordinadores.

---

## ✨ Características Principales

*   **🛠️ Control de Versiones:** Historial completo de entregas con gestión de estados (Pendiente, Observado, Aprobado).
*   **👁️ Visor de PDF Integrado:** Evaluación en tiempo real con pantalla dividida (Side-by-side).
*   **🤖 Motor de Análisis IA:** Extracción automatizada de texto para análisis de originalidad y detección de plagio.
*   **📊 Estadísticas de Impacto:** Dashboard administrativo con métricas visuales desglosadas por carrera universitaria.
*   **📄 Reportes Dinámicos:** Generación de informes oficiales con membretes institucionales personalizables y firmas digitales.
*   **🔐 Seguridad Grado Industrial:** Autenticación por roles (RBAC) y recuperación de cuentas vía OTP (Gmail + Redis).

---

## 🛠️ Stack Tecnológico

*   **Backend:** Python 3.11 / Django 5.2 (Arquitectura Monolítica limpia).
*   **Base de Datos:** PostgreSQL 15 (Persistencia relacional).
*   **Caché/Seguridad:** Redis (Gestión de tokens temporales).
*   **Frontend:** HTML5, CSS3 (Variables dinámicas), Bootstrap 5, FontAwesome 6 y Chart.js.
*   **Infraestructura:** Docker & Docker Compose (Contenerización completa).

---

## 🚀 Instalación 

El sistema está completamente dockerizado. Siga estos pasos para levantar el entorno en menos de 2 minutos:

### 1. Clonar el repositorio
```bash
git clone 
cd SISTEMA_TESIS
2. Configurar variables de entorno
Cree un archivo .env en la raíz basado en el archivo .env.example:
code
Bash
cp .env.example .env
# Edite el archivo .env con sus credenciales de Gmail para el soporte
3. Levantar el sistema
code
Bash
docker-compose up --build -d
El sistema ejecutará automáticamente las migraciones, la recolección de estáticos y la siembra de datos iniciales (Seeder).
👥 Credenciales de Acceso (Entorno de Pruebas)
Gracias al sistema de auto-seeding, puede probar el flujo completo con las siguientes cuentas predefinidas:
Rol	Usuario	Contraseña
Administrador / Coordinador	admin	admin123
Profesor / Evaluador	profe_ana	prof123
Estudiante	est_pedro	est123
URL de acceso local: http://localhost:8000
📂 Estructura del Proyecto
code
Text
├── core/               # Configuración global del proyecto
├── gestion/            # Lógica de negocio (App principal)
├── templates/          # Capa de presentación (UI/UX)
├── static/             # Activos estáticos (JS, CSS, Img)
├── docs/               # Manuales técnicos y de usuario
├── media/              # Almacenamiento de tesis y firmas
├── docker-compose.yml  # Orquestador de servicios
└── entrypoint.sh       # Script de arranque automático
📜 Documentación
Para una guía detallada sobre el uso de la plataforma, consulte el Manual de Usuario ubicado en la carpeta /docs/TesisControl.pdf.
