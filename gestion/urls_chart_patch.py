from django.urls import path
from .views_chart import dashboard_coordinador_chart

urlpatterns = [
    path('dashboard-coordinador/chart/', dashboard_coordinador_chart, name='dashboard_coordinador_chart'),
]

