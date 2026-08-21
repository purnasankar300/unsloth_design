"""Day-one instrumentation.

Three numbers, from spec §10, read at ``/insights/``. They decide whether a future version should
automate the image editing via an API — a decision costing ₹6,500–34,000 a
month. Without real data that decision is guesswork, so these ship with V1
rather than after it:

1. Versions per design — how many revision rounds actually happen.
2. Uploads per day, per user and total.
3. Time from creation to approval.
"""

import statistics
from datetime import timedelta

from django.db.models import Avg, Count, F
from django.db.models.functions import TruncDate
from django.shortcuts import render
from django.utils import timezone

from .models import Design, Version

WINDOW_DAYS = 30


def _humanise(delta):
    if delta is None:
        return "—"
    days = delta.days
    hours = delta.seconds // 3600
    return f"{days}d {hours}h" if days else f"{hours}h"


def insights_view(request):
    since = timezone.now() - timedelta(days=WINDOW_DAYS)

    # 1. Versions per design.
    per_design = list(
        Design.objects.annotate(versions_total=Count("versions"))
        .values_list("versions_total", flat=True)
    )
    distribution = {}
    for count in per_design:
        bucket = "10+" if count >= 10 else str(count)
        distribution[bucket] = distribution.get(bucket, 0) + 1

    # 2. Uploads per day, total and per user.
    daily = (
        Version.objects.filter(created_at__gte=since)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(total=Count("id"))
        .order_by("-day")
    )
    by_user = (
        Version.objects.filter(created_at__gte=since)
        .values(username=F("created_by__username"))
        .annotate(total=Count("id"))
        .order_by("-total")
    )

    # 3. Creation to approval.
    approved = Design.objects.filter(approved_at__isnull=False)
    lead_times = [d.approved_at - d.created_at for d in approved]
    median_lead = None
    if lead_times:
        median_lead = timedelta(seconds=statistics.median(t.total_seconds() for t in lead_times))

    busiest_day = max((row["total"] for row in daily), default=0)

    return render(
        request,
        "designs/insights.html",
        {
            "title": "Insights",
            "window_days": WINDOW_DAYS,
            "design_count": len(per_design),
            "version_total": sum(per_design),
            "versions_mean": round(sum(per_design) / len(per_design), 1) if per_design else 0,
            "versions_max": max(per_design, default=0),
            "distribution": sorted(distribution.items(), key=lambda kv: (kv[0] == "10+", kv[0])),
            "daily": daily,
            "busiest_day": busiest_day,
            "by_user": by_user,
            "approved_count": approved.count(),
            "median_lead": _humanise(median_lead),
            "mean_lead": _humanise(
                timedelta(seconds=sum(t.total_seconds() for t in lead_times) / len(lead_times))
                if lead_times
                else None
            ),
            "unapproved_count": Design.objects.filter(approved_at__isnull=True).count(),
            "deep_versions": Version.objects.filter(parent__isnull=False).aggregate(n=Count("id"))["n"],
            "reference_branches": Version.objects.filter(parent__parent__isnull=True, parent__isnull=False).count(),
            "avg_size_mb": round((Version.objects.aggregate(a=Avg("file_size"))["a"] or 0) / 1024 / 1024, 1),
        },
    )
