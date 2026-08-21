"""Seed the pipeline and the external-tool guidance cards.

The status names below are PLACEHOLDERS. Section 9 of the requirements leaves
the real stages open, so these exist only to make the app usable on day one.
Rename them, add stages, or rewire the permitted moves in django-admin — none
of it requires a migration or a code change, because nothing branches on a
status name.
"""

from django.db import migrations

# code, label, order, tone, initial, approval, terminal
STATUSES = [
    ("draft", "Draft", 10, "neutral", True, False, False),
    ("in-review", "In review", 20, "progress", False, False, False),
    ("revision-requested", "Revision requested", 30, "attention", False, False, False),
    ("approved", "Approved", 40, "good", False, True, False),
    ("on-hold", "On hold", 50, "neutral", False, False, False),
    ("dropped", "Dropped", 60, "stopped", False, False, True),
]

# Deliberately permissive: the loop is upload → comment → revise → approve, and
# with a team of five there is no reason to make a legitimate move impossible.
TRANSITIONS = [
    ("draft", "in-review"),
    ("draft", "on-hold"),
    ("draft", "dropped"),
    ("in-review", "revision-requested"),
    ("in-review", "approved"),
    ("in-review", "on-hold"),
    ("in-review", "dropped"),
    ("revision-requested", "in-review"),
    ("revision-requested", "approved"),
    ("revision-requested", "on-hold"),
    ("revision-requested", "dropped"),
    ("approved", "revision-requested"),
    ("on-hold", "draft"),
    ("on-hold", "in-review"),
    ("on-hold", "dropped"),
    ("dropped", "draft"),
]

GUIDANCE = [
    {
        "name": "Google Gemini chat",
        "url": "https://gemini.google.com",
        "summary": "Best for single, well-described changes to one garment photo.",
        "order": 10,
        "steps": [
            "Download the version image you want to work from.",
            "Open a new chat and attach the image.",
            "Describe one change only — colour, or sleeve, not both.",
            "Download the result, come back here, and upload it as a new version.",
        ],
    },
    {
        "name": "Google Flow",
        "url": "https://labs.google/flow",
        "summary": "Better when you need to mask and edit one region precisely.",
        "order": 20,
        "steps": [
            "Start a project and upload the reference as the base frame.",
            "Use the edit brush to mask only the panel you are changing.",
            "Export at full resolution — not the preview size.",
            "Upload the export here as a new version.",
        ],
    },
]


def seed(apps, schema_editor):
    Status = apps.get_model("designs", "Status")
    AllowedTransition = apps.get_model("designs", "AllowedTransition")
    GuidanceCard = apps.get_model("designs", "GuidanceCard")
    GuidanceStep = apps.get_model("designs", "GuidanceStep")

    by_code = {}
    for code, label, order, tone, initial, approval, terminal in STATUSES:
        by_code[code], _ = Status.objects.get_or_create(
            code=code,
            defaults={
                "label": label,
                "order": order,
                "tone": tone,
                "is_initial": initial,
                "is_approval": approval,
                "is_terminal": terminal,
            },
        )

    for source, target in TRANSITIONS:
        AllowedTransition.objects.get_or_create(from_status=by_code[source], to_status=by_code[target])

    for card in GUIDANCE:
        steps = card.pop("steps")
        obj, created = GuidanceCard.objects.get_or_create(name=card["name"], defaults=card)
        if created:
            for index, text in enumerate(steps, start=1):
                GuidanceStep.objects.create(card=obj, order=index * 10, text=text)


def unseed(apps, schema_editor):
    apps.get_model("designs", "AllowedTransition").objects.all().delete()
    apps.get_model("designs", "Status").objects.filter(code__in=[s[0] for s in STATUSES]).delete()
    apps.get_model("designs", "GuidanceCard").objects.filter(
        name__in=[c["name"] for c in GUIDANCE]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("designs", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]
