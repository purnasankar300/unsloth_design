# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Selvedge — an internal Django app for a 5–6 person garment design team in India. A **versioned image collaboration tool with a status pipeline**: reference photo → guided external editing → upload the result as a new version → comments → revisions → approval. It ends at "approved"; post-approval production tracking lives in a different application.

`REQUIREMENTS.md` is the frozen V1 spec and remains the source of truth. Section 11 is marked "Do Not Relitigate". `index.html` is the current approved wireframe and the visual reference the UI is ported from; `mockup.html` is the earlier one it superseded. Neither is wired to the app — keep both, port from `index.html`.

## Commands

```bash
docker compose up -d                              # postgres:16 + MinIO (stands in for R2)
.venv/bin/python manage.py migrate                # also seeds statuses, spec fields, guidance cards
.venv/bin/python manage.py runserver
.venv/bin/python manage.py test designs           # 76 tests
.venv/bin/python manage.py test designs.tests.test_domain.StatusTests.test_self_approval_is_permitted_but_flagged
.venv/bin/python manage.py dump_to_r2             # weekly backup, needs pg_dump on PATH
```

Postgres is on **port 5433** (not 5432) to avoid colliding with a local install. Admin insights live at `/admin/designs/design/insights/`. Deps are managed with `uv` against the in-tree `.venv`.

## Architecture

Single app, `designs/`. The layering matters: **views never write to the ORM directly** — every multi-table write goes through `designs/services.py`, which is where attribution, transition validation and the audit trail are enforced. Adding a write path that bypasses it will silently drop the audit record.

- `services.py` — `create_design`, `add_version`, `add_comment`, `change_status`,
  `update_design`, `set_requirement`, `activity`, `build_tree`, `duplicate_of`, `set_spec`,
  `add_spec_option`, `retire_spec_option`, `spec_choices`
- `codes.py` — design code allocation, `select_for_update` on a counter row
- `imaging.py` — the single upload pipeline: validate, sha256, dimensions, thumbnail
- `storage.py` — opaque key construction and signed URLs
- `insights.py` — the §10 instrumentation, rendered inside admin

The UI is a **board of cards with a slide-over drawer**. `design_detail` returns
`partials/_drawer.html` to an htmx request and `detail.html` — the same partial in a page
shell — to anything else, so a deep link, a bookmark and a no-JS click all still work.
Every drawer control posts back and swaps `#drawer-body`. `static/js/app.js` is the only
hand-written JavaScript: it opens the overlays after an htmx swap, closes them (on Escape,
the scrim, `[data-close-overlays]`, or an `overlay-close` trigger from a view), and shows
a toast when a view sends one via the `HX-Trigger` header. Nothing in it is load-bearing.

`templates/base_bare.html` is the shell with no chrome; `base.html` adds the topbar and the
overlays on top of it, so the signed-out pages carry neither. The drawer's inline editors —
the design header and a version's requirement — are held in the URL as `?edit=head` /
`?edit=req` rather than in separate views, so the dual render costs nothing extra.

The drawer's right column is **one chronological activity feed** for the whole design —
uploads, status moves and comments interleaved, built by `services.activity`. Comments are
still written against the version on screen; the feed only reads across all of them.

## Rules that are easy to break

- **Statuses are data, not code.** No status name appears anywhere in Python or templates. Behaviour keys off `Status.is_approval` / `is_terminal` / `is_initial`. Section 9 of the spec is still open, so the names in the seed migration are placeholders and the team may rename them in admin at any time — a `code == "approved"` check would break silently when they do. There is a test (`test_no_status_name_is_hardcoded`) guarding this.
- **The reference image is version 1**, a `Version` with `parent=None`. There is no separate reference field on `Design`. This is a deliberate reading of spec §6: one image table, one upload path, and "branch from the original" is an ordinary parent assignment.
- **Versions form a tree.** Users must be able to re-branch from the reference because externally edited images degrade over successive rounds. `Version.depth_from_reference` is what surfaces that degradation in the UI.
- **Spec fields are data too.** The design specification grid (`SpecField` / `SpecOption` /
  `DesignSpec`) works exactly like `Status`: no field label and no option label appears in
  Python or in a template, the grid iterates whatever is active, and the seed migration is
  the only place the starting labels live. There is a test
  (`test_no_spec_field_name_is_hardcoded`) guarding this. Season and Category are
  deliberately *not* spec fields — they make up the design code.
- **Retiring an option is `is_active = False`.** Never a delete. Designs already carrying a
  value keep it, `spec_choices` still offers it to them, and new designs stop seeing it.
- **`Version.number` is 1-based; the UI is not.** The reference renders as `REF` and later
  versions as `v1`, `v2` via `Version.display_label`. The database, the admin and the audit
  trail keep `v{number}`. Use `display_label` in templates and `number` in URLs.
- **`{# #}` is single-line only.** Django does not close a `{# #}` across a newline, so a
  multi-line one renders as literal text on the page. Multi-line notes use
  `{% comment %}`/`{% endcomment %}`. A test (`test_no_template_comment_spans_more_than_one_line`)
  guards this — it caught every template in the repo once.
- **Comments attach to a Version, not a Design.** The drawer shows them in one feed across
  every version, but the composer always posts against the version on screen.
- **A season is called a "Drop" in the UI.** The model, the field name `season` and the first
  segment of the design code stay `Season`; the label comes from `verbose_name="Drop"` on the
  `Design.season` FK, so forms and the Design admin follow it without any string being typed
  twice. The Season model itself is still "Season" in admin.
- **A design's name is optional and its code is not.** A blank name falls back to the code in
  `create_design` / `update_design`. Re-filing a design to another drop or category never
  recalculates the code.
- **Nothing is ever deleted.** `on_delete=PROTECT` throughout, no delete views, admin delete disabled on Design/Version/Comment.
- **No roles.** Any user may do anything, including approving their own design. Do not add a permission system — instead, self-approval is stored as `StatusTransition.is_self_approval` and displayed in the trail.
- **No AI/LLM calls.** The app never talks to a model API. External editing happens by hand, in tools described by admin-editable `GuidanceCard` rows.

## Storage

Postgres stores keys only. Keys are opaque and immutable — `designs/{design_uuid}/versions/{version_uuid}.ext` — never derived from a design name or an uploaded filename. The bucket is **private**: templates link to `version-image` / `version-thumbnail` views which redirect to a signed URL that expires (600s by default). Lists request thumbnails only, for both speed and egress cost. Verified: an unsigned or expired URL returns 403.

Every upload also records dimensions, file size and a sha256 content hash; a matching hash within the same design raises a non-blocking duplicate warning, since a manual round-trip through external tools makes re-uploads likely.

## Config

All settings come from `.env` (gitignored; see `.env.example`). Dev and production differ **only** by environment variables — MinIO and R2 are both S3-compatible, so moving to Neon + R2 changes no code. `TIME_ZONE` is `Asia/Kolkata`. Full documentation is in `docs/` — `local-setup.md` (getting it running), `application.md` (layering, routes, UI), `database.md` (every table), `deploy.md`, `restore-test.md`.
