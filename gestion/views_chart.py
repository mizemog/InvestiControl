import calendar
from collections import defaultdict

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from .models import Proyecto, VersionDocumento


def _month_key(dt):
    return (dt.year, dt.month)


def _quarter_key(dt):
    q = ((dt.month - 1) // 3) + 1
    return (dt.year, q)


def _labels_for_months(marks):
    # marks: list[(year, month)] oldest->newest
    out = []
    for y, m in marks:
        out.append(calendar.month_abbr[m])
    return out


def _labels_for_quarters(marks):
    # marks: list[(year, quarter)] oldest->newest
    out = []
    for y, q in marks:
        out.append(f"Q{q}")
    return out


def _get_month_marks(count_months=6):
    now = timezone.now()
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    marks = []
    # generar oldest->newest
    d = first
    # ir hacia atrás count_months-1 meses
    for _ in range(count_months - 1):
        prev_month = d.month - 1
        prev_year = d.year
        if prev_month == 0:
            prev_month = 12
            prev_year -= 1
        d = d.replace(year=prev_year, month=prev_month, day=1)

    # ahora d es el primer mes
    for _ in range(count_months):
        marks.append((d.year, d.month))
        # sumar 1 mes
        next_month = d.month + 1
        next_year = d.year
        if next_month == 13:
            next_month = 1
            next_year += 1
        d = d.replace(year=next_year, month=next_month, day=1)

    return marks


def _get_quarter_marks(count_quarters=4):
    now = timezone.now()
    year = now.year
    current_q = ((now.month - 1) // 3) + 1

    marks = []

    # convertir (year, q) a índice lineal para restar fácilmente
    idx_now = year * 4 + (current_q - 1)
    start_idx = idx_now - (count_quarters - 1)

    for i in range(count_quarters):
        idx = start_idx + i
        y = idx // 4
        q0 = idx % 4  # 0..3
        q = q0 + 1
        marks.append((y, q))

    return marks


def _aggregate_for_period(period: str):
    if period == "mes":
        marks = _get_month_marks(6)
        labels = _labels_for_months(marks)
        versions_qs = VersionDocumento.objects.all()
        # construir mapa de conteos por (year,month)
        version_counts = defaultdict(int)
        for vd in versions_qs.only("fecha_subida").iterator():
            key = _month_key(vd.fecha_subida)
            if key in set(marks):
                version_counts[key] += 1

        # aprobadas/observadas por fecha_inicio del proyecto (proxy temporal)
        aprov_counts = defaultdict(int)
        obs_counts = defaultdict(int)
        proyectos_qs = Proyecto.objects.all().only("estado", "fecha_inicio")
        marks_set = set(marks)
        for p in proyectos_qs.iterator():
            key = _month_key(p.fecha_inicio)
            if key in marks_set:
                if p.estado == "Aprobado":
                    aprov_counts[key] += 1
                elif p.estado == "Observado":
                    obs_counts[key] += 1

        data_versiones = [version_counts[k] for k in marks]
        data_aprobadas = [aprov_counts[k] for k in marks]
        data_observadas = [obs_counts[k] for k in marks]

        return {
            "labels": labels,
            "datasets": {
                "versiones": data_versiones,
                "aprobadas": data_aprobadas,
                "observadas": data_observadas,
            },
        }

    if period == "trimestre":
        marks = _get_quarter_marks(4)
        labels = _labels_for_quarters(marks)
        versions_qs = VersionDocumento.objects.all()
        version_counts = defaultdict(int)
        marks_set = set(marks)
        for vd in versions_qs.only("fecha_subida").iterator():
            key = _quarter_key(vd.fecha_subida)
            if key in marks_set:
                version_counts[key] += 1

        aprov_counts = defaultdict(int)
        obs_counts = defaultdict(int)
        proyectos_qs = Proyecto.objects.all().only("estado", "fecha_inicio")
        for p in proyectos_qs.iterator():
            key = _quarter_key(p.fecha_inicio)
            if key in marks_set:
                if p.estado == "Aprobado":
                    aprov_counts[key] += 1
                elif p.estado == "Observado":
                    obs_counts[key] += 1

        data_versiones = [version_counts[k] for k in marks]
        data_aprobadas = [aprov_counts[k] for k in marks]
        data_observadas = [obs_counts[k] for k in marks]

        return {
            "labels": labels,
            "datasets": {
                "versiones": data_versiones,
                "aprobadas": data_aprobadas,
                "observadas": data_observadas,
            },
        }

    return {"labels": [], "datasets": {"versiones": [], "aprobadas": [], "observadas": []}}


@require_GET
def dashboard_coordinador_chart(req):
    if not req.user.is_authenticated:
        return JsonResponse({"error": "unauthorized"}, status=401)

    period = req.GET.get("period", "mes")
    if period not in {"mes", "trimestre"}:
        period = "mes"

    payload = _aggregate_for_period(period)
    payload["period"] = period
    return JsonResponse(payload)

