from django.contrib import admin
from .models import Usuario, Carrera, Proyecto, VersionDocumento, Revision, Comentario, AnalisisIA, Notificacion

admin.site.register(Usuario)
admin.site.register(Carrera)
admin.site.register(Proyecto)
admin.site.register(VersionDocumento)
admin.site.register(Revision)
admin.site.register(Comentario)
admin.site.register(AnalisisIA)
admin.site.register(Notificacion)