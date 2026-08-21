"""Design code allocation.

The code is the design's identity, so it must be unique, stable and never
derived from anything a user can rename.
"""

from django.db import transaction

from .models import DesignCodeSequence

CODE_FORMAT = "{season}-{category}-{number:03d}"


@transaction.atomic
def allocate_code(season, category):
    """Return the next design code for this season and category.

    The counter row is locked for the duration of the transaction, so two
    people creating a design at the same moment get different codes rather than
    colliding on the unique constraint.
    """
    sequence = (
        DesignCodeSequence.objects.select_for_update()
        .filter(season=season, category=category)
        .first()
    )
    if sequence is None:
        # get_or_create then re-select, so the row is locked either way.
        DesignCodeSequence.objects.get_or_create(season=season, category=category)
        sequence = (
            DesignCodeSequence.objects.select_for_update()
            .get(season=season, category=category)
        )

    number = sequence.next_number
    sequence.next_number = number + 1
    sequence.save(update_fields=["next_number"])

    return CODE_FORMAT.format(season=season.code, category=category.code, number=number)
