# Running Unsloth Design locally

Everything below assumes the repository root as the working directory. Nothing
here touches a production service — Postgres and object storage both run in
Docker.

## What you need

| Thing | Why |
|---|---|
| Python 3.12+ | |
| [`uv`](https://docs.astral.sh/uv/) | Dependencies are managed with it, against the in-tree `.venv` |
| Docker with Compose | Postgres 16 + MinIO |
| `pg_dump` on `PATH` | Only for `manage.py dump_to_r2` |

> **WSL note.** If `docker` reports *"could not be found in this WSL 2 distro"*,
> open Docker Desktop → Settings → Resources → WSL integration and enable it for
> this distro, then reopen the shell.

---

## First run

### 1. Dependencies

```bash
uv sync
```

Creates `.venv/` in the repository. Every command below calls
`.venv/bin/python` directly, so **there is no need to activate anything** —
though `source .venv/bin/activate` works if you prefer it.

### 2. Configuration

```bash
cp .env.example .env
```

`.env` is gitignored and must never be committed. The defaults already point at
the Docker services, so the only value worth changing locally is
`DJANGO_SECRET_KEY`:

```bash
.venv/bin/python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

Development and production differ **only** by these variables. MinIO and R2 are
both S3-compatible, so moving to Neon + R2 changes no code.

### 3. Services

```bash
docker compose up -d
```

Starts three things:

| Service | Port | Notes |
|---|---|---|
| `db` — postgres:16 | **5433** | Not 5432, so it cannot collide with a local Postgres install |
| `storage` — MinIO | 9000 (API), 9001 (console) | Stands in for Cloudflare R2 |
| `storage-init` | — | One-shot. Creates the `selvedge-designs` bucket and sets it **private**. Exiting is success. |

Check they came up healthy:

```bash
docker compose ps
```

MinIO console: <http://127.0.0.1:9001> — user `selvedge`, password
`selvedge_dev_secret`.

### 4. Database

```bash
.venv/bin/python manage.py migrate
```

This also seeds the reference data: 6 placeholder statuses with their 16 legal
transitions, 15 specification fields with their option lists, and 2 guidance
cards. All of it is editable afterwards under **Settings** in the app — the gear
icon in the top bar. None of it is demo data; a fresh database starts with the
configuration and no designs, drops or categories.

### 5. The first user

There is no self-signup — the team is 5–6 named people — and there is no
django-admin, so this command is the **only** way into a fresh database. Every
account after this one is added in the app, under Settings → Team.

```bash
.venv/bin/python manage.py createsuperuser
```

(`createsuperuser` belongs to `django.contrib.auth`, not to admin, so it still
works. The superuser flags it sets are inert — there are no roles.)

### 6. Seasons and categories

The design code is `{season}-{category}-{NNN}` — a season is called a *drop* in
the UI — so **at least one active season
and one active category must exist before a design can be created.** The app
warns you on the create page if they are missing.

Add them in the app: the gear icon → **Drops** and **Categories**. Codes are
2–8 capitals or digits — e.g. drop `SS26` / *Spring/Summer 26*, category
`TSHIRT` / *T-Shirt*.

### 7. Run it

```bash
.venv/bin/python manage.py runserver
```

- Board: <http://127.0.0.1:8000/>
- Settings: <http://127.0.0.1:8000/settings/>
- Insights: <http://127.0.0.1:8000/insights/>

---

## Day-to-day commands

```bash
docker compose up -d                    # bring the services back
docker compose stop                     # stop them, keep the data
docker compose down -v                  # DESTROY the database and the bucket

.venv/bin/python manage.py runserver
.venv/bin/python manage.py migrate
.venv/bin/python manage.py makemigrations designs
.venv/bin/python manage.py createsuperuser    # only for the FIRST account
.venv/bin/python manage.py shell

.venv/bin/python manage.py test designs                      # 101 tests
.venv/bin/python manage.py test designs.tests.test_specs     # one module
.venv/bin/python manage.py test designs.tests.test_domain.StatusTests.test_self_approval_is_permitted_but_flagged

.venv/bin/python manage.py dump_to_r2   # weekly backup; needs pg_dump on PATH
```

Tests build and drop their own database — running them does not touch your
development data.

---

## Walking the app once it is up

1. **New design** → drop, category, a reference photo (a name is optional, and
   any specification value can be set here or later). The code is
   generated; the photo is locked as **REF**.
2. Click the card. The drawer slides in.
3. **Sparkle button** (top right) → pick an editing tool, follow its steps,
   edit the image outside the app.
4. Back in the drawer, **Upload new version** — choose what to branch from. Come
   back to the reference rather than the last version once edits start
   degrading.
5. Comment on the version on screen. Comments belong to that image, not to the
   design.
6. Fill in the **Design specification** dropdowns. **Gear button → Specification**
   adds an attribute or a value to any field.
7. Move the status from the dropdown in the drawer head. Only legal moves are
   offered — **Gear → Workflow** is where those moves are defined.
8. Approving marks one version final. Approving your own design is allowed and
   is flagged in the activity feed.
9. The drawer's right column is one **activity feed** — uploads, comments and
   status moves for the whole design, oldest first. The pencil by the title
   renames or re-files the design; the code never changes.

---

## Troubleshooting

**`connection to server at "127.0.0.1", port 5433 failed`**
Postgres is not up. `docker compose up -d`, then `docker compose ps`. If a local
Postgres already owns 5433, change the host port in `docker-compose.yml` and the
port in `DATABASE_URL`.

**`docker: command not found` on WSL**
Enable Docker Desktop's WSL integration for this distro (see the note at the
top).

**Images 403, or never load**
The bucket is private by design — images are only reachable through a signed URL
that expires after `S3_SIGNED_URL_EXPIRY` seconds (600 by default). If they
never load at all, `storage-init` probably did not run: `docker compose up -d
storage-init` and check its log.

**`DJANGO_SECRET_KEY` KeyError on startup**
`.env` is missing. Copy `.env.example`.

**"Add at least one drop and one category under Settings first"**
Exactly that. Gear icon → **Drops**, then **Categories**. A design code is
`{DROP}-{CATEGORY}-{NNN}`, so neither can be missing. This is the normal state of
a brand-new database.

**Nobody can sign in / the users table is empty**
`manage.py createsuperuser`. There is no django-admin, so this is the only way
back in; every account after the first is added under Gear → **Team**. The app
also refuses to deactivate the last active account, so you should never get here
by accident.

**`/admin/` returns 404**
Correct. `django.contrib.admin` was removed — everything it did is under
`/settings/`. See `REQUIREMENTS.md` §11b.

**`test_parallel_allocation_produces_unique_codes` errors**
That test needs Postgres row locking. It cannot pass on SQLite. Run the suite
against the Docker Postgres.

**A migration will not apply after pulling**
```bash
.venv/bin/python manage.py migrate designs
.venv/bin/python manage.py showmigrations designs
```
To start clean: `docker compose down -v && docker compose up -d` then migrate
again. That destroys all local data.

---

See also: [application.md](application.md) · [database.md](database.md) ·
[deploy.md](deploy.md) · [restore-test.md](restore-test.md)
