"""Weekly database dump to object storage.

Neon's managed backups are the primary safety net. This is the second,
independent copy — the one that survives losing access to the Neon account
itself. The bucket carries a lifecycle rule expiring the ``backups/`` prefix
after 30 days.
"""

import gzip
import io
import subprocess
from datetime import datetime

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

PREFIX = "backups"


class Command(BaseCommand):
    help = "pg_dump the database, gzip it, and upload it to object storage."

    def add_arguments(self, parser):
        parser.add_argument("--keep-local", help="Also write the dump to this local path.")

    def handle(self, *args, **options):
        db = settings.DATABASES["default"]
        stamp = timezone.localtime().strftime("%Y%m%dT%H%M%S")
        key = f"{PREFIX}/selvedge-{stamp}.sql.gz"

        command = [
            "pg_dump",
            "--no-owner",
            "--no-privileges",
            f"--host={db['HOST']}",
            f"--port={db['PORT'] or 5432}",
            f"--username={db['USER']}",
            db["NAME"],
        ]

        self.stdout.write(f"Dumping {db['NAME']} from {db['HOST']}…")
        try:
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                env={"PGPASSWORD": db["PASSWORD"], "PATH": "/usr/bin:/usr/local/bin:/bin"},
            )
        except FileNotFoundError:
            raise CommandError("pg_dump is not installed on this machine. Install postgresql-client.")
        except subprocess.CalledProcessError as error:
            raise CommandError(f"pg_dump failed: {error.stderr.decode()[:500]}")

        buffer = io.BytesIO()
        with gzip.GzipFile(fileobj=buffer, mode="wb") as gz:
            gz.write(result.stdout)
        payload = buffer.getvalue()

        default_storage.save(key, ContentFile(payload))

        if options["keep_local"]:
            with open(options["keep_local"], "wb") as handle:
                handle.write(payload)

        size_mb = len(payload) / 1024 / 1024
        self.stdout.write(self.style.SUCCESS(f"Uploaded {key} ({size_mb:.1f} MB)"))
        self.stdout.write(
            "A backup you have not restored is not a backup. See docs/restore-test.md."
        )
