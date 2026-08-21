"""Seed the design specification fields and their option lists.

These are a STARTING POINT, taken from the approved wireframe. Which attributes
a garment is described by is a merchandising decision — add fields, rename them,
retire values, all in django-admin. Nothing in the application names a spec
field, so none of that needs a migration.

Retiring is `is_active = False`, never a delete: a value in use is PROTECTed by
the designs carrying it, and nothing here is ever destroyed.
"""

from django.db import migrations

SWATCH = {
    "Black": "#22252B",
    "White": "#F2F2EE",
    "Navy": "#26324F",
    "Olive": "#6B7050",
    "Maroon": "#5E2A34",
    "Beige": "#D6C7AE",
    "Sage": "#A8B49B",
    "Mustard": "#C79A2E",
    "Charcoal": "#43474E",
    "Sky Blue": "#9FC0D9",
    "Lavender": "#B6AECB",
    "Rust": "#A2543A",
}

# code, label, show_on_card, options
# Season and Category are deliberately absent — they are structural, they feed
# the design code, and they already have their own tables.
FIELDS = [
    ("segment", "Segment", False, ["Men", "Women", "Unisex", "Kids"]),
    ("fabric", "Fabric", True, [
        "Cotton 100%", "Cotton-Poly 60/40", "Poly-Cotton 65/35", "Cotton-Lycra 95/5",
        "Viscose", "Modal", "Bamboo", "Slub Cotton", "Linen Blend",
    ]),
    ("knit-type", "Knit type", False, [
        "Single Jersey", "Pique", "Interlock", "French Terry", "Fleece", "Rib 1x1", "Rib 2x2",
    ]),
    ("gsm", "GSM", True, ["140", "150", "160", "180", "190", "200", "220", "240", "280", "320"]),
    ("neck-type", "Neck type", True, [
        "Round Neck", "V-Neck", "Polo Collar", "Henley", "Mock Neck",
        "Boat Neck", "Scoop Neck", "Turtle Neck",
    ]),
    ("sleeve", "Sleeve", False, [
        "Short Sleeve", "Half Sleeve", "Three-Quarter", "Full Sleeve",
        "Sleeveless", "Raglan", "Drop Shoulder", "Cap Sleeve",
    ]),
    ("fit", "Fit", True, [
        "Regular Fit", "Slim Fit", "Oversized", "Relaxed", "Boxy", "Athletic", "Muscle Fit",
    ]),
    ("colours", "Colours", False, [
        "Black", "White", "Navy", "Olive", "Maroon", "Beige",
        "Sage", "Mustard", "Charcoal", "Sky Blue", "Lavender", "Rust",
    ]),
    ("colour-type", "Colour type", False, [
        "Solid", "Melange", "Yarn Dyed", "Pigment Dyed", "Acid Wash",
        "Tie & Dye", "Stripe", "Colour Block",
    ]),
    ("print-technique", "Print technique", False, [
        "Screen Print", "DTG", "DTF", "Sublimation", "Embroidery", "Puff Print",
        "HD Print", "Vinyl", "Rubber Print", "Foil", "None",
    ]),
    ("print-placement", "Print placement", False, [
        "Left Chest", "Right Chest", "Front Centre", "Full Front", "Back Yoke",
        "Full Back", "Left Sleeve", "Right Sleeve", "Nape", "Hem",
    ]),
    ("wash-finish", "Wash / finish", False, [
        "None", "Bio Wash", "Enzyme Wash", "Silicon Wash", "Garment Dyed", "Stone Wash",
    ]),
    ("hem", "Hem", False, ["Straight Hem", "Curved Hem", "High-Low", "Raw Edge", "Ribbed Hem"]),
    ("trims", "Trims", False, [
        "Woven Label", "Printed Label", "Heat Transfer Label", "Hangtag",
        "Care Label", "Twill Tape", "Drawcord", "Metal Eyelet",
    ]),
    ("size-range", "Size range", False, ["XS–XL", "S–XXL", "S–3XL", "M–XXL", "Free Size"]),
]


def seed(apps, schema_editor):
    SpecField = apps.get_model("designs", "SpecField")
    SpecOption = apps.get_model("designs", "SpecOption")

    for index, (code, label, on_card, options) in enumerate(FIELDS, start=1):
        field, _ = SpecField.objects.get_or_create(
            code=code,
            defaults={"label": label, "order": index * 10, "show_on_card": on_card},
        )
        for position, option in enumerate(options, start=1):
            SpecOption.objects.get_or_create(
                field=field,
                label=option,
                defaults={"order": position * 10, "swatch_hex": SWATCH.get(option, "")},
            )


def unseed(apps, schema_editor):
    codes = [code for code, _, _, _ in FIELDS]
    apps.get_model("designs", "SpecOption").objects.filter(field__code__in=codes).delete()
    apps.get_model("designs", "SpecField").objects.filter(code__in=codes).delete()


class Migration(migrations.Migration):
    dependencies = [("designs", "0003_spec_models")]
    operations = [migrations.RunPython(seed, unseed)]
