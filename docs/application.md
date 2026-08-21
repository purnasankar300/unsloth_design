# Application

Unsloth Design is a **versioned image collaboration tool with a status pipeline**. A
reference photo goes in, the team edits it by hand in external tools, uploads
each result as a new version, comments on specific versions, and eventually
approves one. It ends at "approved" — production tracking lives elsewhere.

There is no cleverness in the middle. The value is the record: what was tried,
who said what, which version won, and when.

**The application never calls a model API.** All editing happens outside it,
by hand, in tools described by guidance cards the team edits under Settings.

---

## Layers

```
templates/ + static/     the board, the drawer, the modals
        ↕
designs/views.py         request → response. Reads freely, NEVER writes.
        ↕
designs/services.py      every multi-table write. Attribution, transition
                         validation and the audit trail are enforced here.
        ↕
designs/models.py        the schema (see database.md)
```

> **The rule that matters:** views never write to the ORM directly. Adding a
> write path that bypasses `services.py` will silently drop the audit record.

Supporting modules:

| Module | Job |
|---|---|
| `codes.py` | Design code allocation. `SELECT … FOR UPDATE` on a counter row so concurrent creates cannot collide. |
| `imaging.py` | The single upload pipeline: validate it is an image, SHA-256 it, read dimensions, generate a thumbnail. |
| `storage.py` | Opaque key construction and short-lived signed URLs. |
| `insights.py` | The §10 instrumentation, rendered at `/insights/`. |
| `forms.py` | `NewDesignForm` (spec fields built from the table at runtime), `NewVersionForm`, `CommentForm`, `DesignHeaderForm`, `AssetsForm`, `MeasurementFormSet`. |


---

## Services

Everything that writes more than one table:

| Function | Does |
|---|---|
| `create_design(name, season, category, actor, reference_upload, requirement)` | Allocates the code, creates the Design in the initial status, stores the reference as version 1, writes the creation `StatusTransition`. A blank name falls back to the code. Atomic. |
| `add_version(design, parent, upload, requirement, actor)` | Locks the design, rejects a cross-design parent and a second reference, allocates `number = max + 1`, runs the upload pipeline. |
| `duplicate_of(version)` | The earliest other version in the same design with an identical `content_hash`. **Warning only** — the upload is still saved. |
| `add_comment(version, author, body)` | Rejects an empty body. |
| `allowed_targets(status)` | Statuses reachable from here, from `AllowedTransition` rows. |
| `change_status(design, to_status, actor, comment, version)` | Refuses a no-op and anything without an `AllowedTransition` row. If the target `is_approval`, requires a version of this design and marks exactly one final. Leaving approval clears the flag and nulls `approved_at`. Records `is_self_approval`. |
| `update_design(design, name, season, category, actor)` | Renames or re-files a design. **Never recalculates the code.** A blank name falls back to the code. |
| `set_requirement(version, text, actor)` | Corrects the description a version was uploaded with. The image is untouched; the old wording stays in `Version.history`. |
| `activity(design)` | Every recorded event — status moves, uploads and comments — oldest first, in three queries. What the drawer's right column renders. |
| `build_tree(design)` | The version tree, in one query. |
| `set_spec(design, field, option, actor)` | Rejects an option belonging to a different field, and any retired option. Attributes the change to `actor`. |
| `add_spec_option(field, label, actor)` | Case-insensitive duplicate check. |
| `retire_spec_option(option, actor)` | `is_active = False`. Never deletes. |
| `spec_choices(design=None)` | `[(field, options, current), …]` for the grid. A design holding a since-retired value keeps that value in its own list, so opening the drawer never silently drops what someone chose. |

### Configuration services

What django-admin used to do. Single-table writes, but they live here for the
same reason the rest does: one place knows the invariants.

| Function | Does |
|---|---|
| `add_drop` / `update_drop` / `retire_drop` / `restore_drop` | A selling season. The code is typed, uppercased and unique; renaming never touches it, because every design code carries it. |
| `add_category` / `update_category` / `retire_category` / `restore_category` | The same, for the second code segment. |
| `add_spec_field` / `update_spec_field` / `retire_spec_field` / `restore_spec_field` | The attributes a garment is described by. The code is slugified from the label at creation and then frozen. |
| `save_guidance_card(card=None, …)` / `retire_guidance_card` / `restore_guidance_card` | Create or rewrite one external-tool card. Steps are replaced wholesale from a textarea, one per line. Retiring hides it from the sparkle modal; the card itself stays. |
| `add_status` / `update_status` / `retire_status` / `restore_status` | Workflow stages — spec §9 answered as data. Setting `is_initial` clears it elsewhere, because `only_one_initial_status` is a database constraint. |
| `set_transitions(status, targets, actor)` | Rewrites the legal moves out of one stage. **The only service that really deletes rows** — an `AllowedTransition` has no active flag, so absence is the meaning. |
| `add_teammate` / `set_teammate_password` / `deactivate_teammate` / `reactivate_teammate` | The team. No roles. Deactivating the **last** active account is refused — with no admin, that would lock everyone out. |

---

## The UI

Ported from `index.html`, the current approved wireframe. `mockup.html` is the
earlier one it superseded. Neither is wired to the app; both stay as reference.

### The board (`/`)
A grid of hangtag cards. Each card shows the cover image (the approved version
if there is one, otherwise the newest), the design code, the name, up to a few
spec chips, the status stamp and a comment count.

- **Thumbnails only.** Lists never request a full-size image — both a
  performance and an egress-cost requirement.
- The filter rail is one GET form holding three controls — search, a Status
  dropdown and a Category dropdown — so they compose into one querystring. Both
  dropdowns are built from their tables; no status or category name is written
  in the template.
- Search covers the name, the code and **every specification value**, so
  "cotton" or "240" finds designs.

### The drawer
Clicking a card htmx-swaps `#drawer-body` and slides the drawer in.

`design_detail` returns:
- `partials/_drawer.html` to an htmx request,
- `detail.html` — the same partial wrapped in a page shell — to anything else.

So a deep link, a bookmark, a shared URL and a click with JavaScript off all
still work, and the two paths cannot drift apart.

Inside the drawer:

| Region | What it is |
|---|---|
| Head | Code, name, status dropdown (offering only legal moves), compare toggle, upload button, close. The pencil opens `?edit=head`: name, drop and category inline — the code never changes. |
| Stage | The selected image, full size, via a signed URL. The reference is marked **Locked**. |
| Compare | `?compare=1` renders the reference and the selected version side by side. Server-rendered, so it survives a deep link. |
| Contact sheet | Every version as a filmstrip frame, reference first and set apart. Clicking a frame swaps the drawer to that version. |
| Upload | Names its parent, so **re-branching from the reference is one dropdown** — which matters, because externally edited images degrade over successive rounds. |
| Specification | The spec grid. Changing a dropdown posts immediately and toasts. |
| Description | "What this version was for", with an Edit button opening `?edit=req`. |
| Activity | One chronological feed for the whole design: uploads, status moves (self-approval flagged) and comments from every version, interleaved. The composer still posts against the version on screen. |
| Authoritative assets | Logo, Pantone, measurements — what the garment is actually made from. |

### Modals
- **Sparkle** → the guidance cards: which external tool to use and the steps for it.
- **Gear** → settings: drops, categories, the specification grid, guidance cards, the
  workflow and the team. Six sections in one modal; this is what replaced django-admin.
- **Assets Edit** → the authoritative assets form, so editing them never leaves the drawer.

All three also render as ordinary pages (`/guidance/`, `/settings/<section>/`,
`/designs/<code>/assets/`) when JavaScript is off. The settings page template
*includes* the same modal partial, so the two cannot drift.

### Brand assets

`docs/logo.png` is the original artwork, 1536×1024 on white, and is **not**
served. Three derivatives live in `static/img/`, made by thresholding the white
paper to transparent and trimming to the ink:

| File | Where it is used |
|---|---|
| `unsloth-logo.png` (900×476) | The sign-in page lockup |
| `unsloth-mark.png` (160×88) | The topbar, `filter: invert(1)` because the artwork is black and the bar is ink |
| `unsloth-watermark.png` (638×351) | The page watermark — the sloth alone, since repeating the wordmark behind a page that already shows it reads as a mistake |

The watermark is a fixed `body::before` at 4% opacity: `pointer-events: none`, so
it never intercepts a click, and everything readable is lifted above it with
`z-index: 1`. On the sign-in page it drops to 5% and moves below the card rather
than behind it.

> **The app is called Unsloth Design; the infrastructure is still called
> `selvedge`.** The database, the R2/MinIO bucket, the Linux user, the systemd
> unit and the backup key prefix all keep that name. Renaming them means
> recreating a database and a bucket for a cosmetic gain, so it has not been
> done — do not "fix" one of them in isolation.

### JavaScript
`static/js/app.js`, about 70 lines, is the only hand-written JS. It opens the
overlays after an htmx swap, closes them on Escape / scrim / close button or an
`overlay-close` trigger from a view, and shows a toast when a view sends one via
the `HX-Trigger` header. **Nothing in it
is load-bearing** — every URL it touches renders as a page on its own.

---

## Routes

| Route | Name | Notes |
|---|---|---|
| `/` | `design-list` | The board. Accepts `?status=`, `?category=`, `?q=`, composed by one filter form in the rail |
| `/designs/new/` | `design-create` | |
| `/designs/<code>/` | `design-detail` | Drawer partial for htmx, page otherwise. `?v=<number>`, `?compare=1`, `?edit=head\|req` |
| `/designs/<code>/edit/` | `design-update` | POST name, drop, category. The code is never touched |
| `/designs/<code>/versions/` | `version-create` | POST |
| `/designs/<code>/versions/<number>/requirement/` | `requirement-set` | POST |
| `/designs/<code>/versions/<number>/comments/` | `comment-create` | POST |
| `/designs/<code>/status/` | `status-change` | POST |
| `/designs/<code>/assets/` | `assets-edit` | Authoritative assets + measurements. Modal for htmx, page otherwise |
| `/designs/<code>/specs/` | `spec-set` | POST one field + option |
| `/settings/` | `settings` | Redirects to the first section |
| `/settings/<section>/` | `settings-section` | `drops`, `categories`, `spec-fields`, `guidance`, `workflow`, `team`. Modal for htmx, page otherwise |
| `/settings/spec-fields/<field>/options/` | `spec-options-field` | One field's value list — the drawer links straight here |
| `/settings/<thing>/add/` | `drop-add`, `category-add`, `spec-field-add`, `status-add`, `teammate-add`, `guidance-save` | POST |
| `/settings/<thing>/<pk>/save/` | `drop-save`, `category-save`, `spec-field-save`, `status-save`, `guidance-card-save` | POST |
| `/settings/<thing>/<pk>/toggle/` | `drop-toggle`, `category-toggle`, `spec-field-toggle`, `guidance-toggle`, `status-toggle`, `teammate-toggle` | POST. Retires or restores, depending which way the row is |
| `/settings/spec-fields/<field>/options/add/` | `spec-option-add` | POST |
| `/settings/spec-options/<pk>/retire/` | `spec-option-retire` | POST |
| `/settings/workflow/<pk>/moves/` | `status-moves` | POST the legal moves out of one stage |
| `/settings/team/<pk>/password/` | `teammate-password` | POST |
| `/insights/` | `insights` | The §10 numbers. Was an admin page |
| `/guidance/` | `guidance-modal` | Modal or page |
| `/images/<uuid>/` | `version-image` | 302 → signed URL |
| `/images/<uuid>/thumb/` | `version-thumbnail` | 302 → signed URL |


Every POST answers the way it was asked: an htmx caller gets the re-rendered
drawer plus a toast, a plain form post gets a redirect with a Django message.

Authentication is global — `LoginRequiredMiddleware`, no per-view decorators, no
anonymous access. `/login/` and `/logout/` are the only routes outside
`designs/urls.py`; **there is no `/admin/`.** The signed-out pages extend
`templates/base_bare.html`, which is `base.html` without the topbar and the
overlay shells.

---

## Rules that are easy to break

- **Statuses are data, not code.** No status name appears in Python or in a
  template. Behaviour keys off `is_approval` / `is_terminal` / `is_initial`.
  Spec §9 is still open, so the seeded names are placeholders and the team may
  rename them at any time — a `code == "approved"` check would break silently.
  Guarded by `test_no_status_name_is_hardcoded`.
- **Spec fields are data too**, on exactly the same terms. Guarded by
  `test_no_spec_field_name_is_hardcoded`.
- **The reference image is version 1**, a `Version` with `parent = NULL`. There
  is no separate reference field on Design.
- **Versions form a tree, not a chain.** Re-branching from the reference is the
  answer to accumulated degradation; `depth_from_reference` is what surfaces it.
- **Comments attach to a Version, not a Design.**
- **Nothing is ever deleted.** `PROTECT` throughout, no delete views, and every
  "remove" in Settings is `is_active = False`.
- **No roles.** Any user may do anything, including approving their own design
  and changing any setting. Do not add a permission system — self-approval is
  stored on the transition and shown in the trail.
- **No django-admin.** Configuration lives under `/settings/`. A deliberate
  deviation from spec §5/§6/§10, recorded in `REQUIREMENTS.md` §11b;
  `manage.py createsuperuser` is the only bootstrap into an empty database.
- **A drop is a Season.** "Drop" is the UI label, set once by `verbose_name` on
  the `Design.season` FK. The model, the field and the code segment stay `season`.
- **`{# #}` is single-line only.** Django will not close one across a newline, so
  a multi-line comment renders as text on the page. Use `{% comment %}`.
- **No AI/LLM calls**, ever.
- **`Version.number` is 1-based; the UI is not.** Use `display_label` in
  templates, `number` in URLs.

---

## Known limitations (accepted — spec §11, do not relitigate)

Properties of AI image editing, not bugs. Tell users up front:

- The team's actual logo artwork **will not** be faithfully reproduced.
- Exact Pantone or shade matching is **not achievable**.
- Readable text or slogans on garments render poorly.
- Dark-to-light conversions (navy → white) are the hardest case. Expect several
  attempts.
- Details drift between successive generations. Hence the version tree.

This is why `Design.logo_file`, `colour_code` and `Measurement` exist: the
edited images are a visualisation aid, the authoritative fields are the record.

**Confidentiality:** designers upload unreleased designs to external consumer AI
tools, whose free tiers generally permit training on that data. Either use paid
accounts or accept the risk formally — do not leave it undecided.

---

## Instrumentation

`/insights/` reports, from day one:

1. Versions per design — how many revision rounds actually happen
2. Uploads per day, per user and total
3. Time from creation to approval

These numbers decide whether a future version should automate the image editing
via API — a ₹6,500–34,000/month decision that is guesswork without real data.

---

## Tests

```bash
.venv/bin/python manage.py test designs      # 101 tests
```

- `test_domain.py` — creation and codes, the upload pipeline, the version tree,
  status rules including self-approval, comments, and code-allocation
  concurrency (needs Postgres — SQLite has no `SELECT … FOR UPDATE`).
- `test_specs.py` — spec values, retired options, and the no-hardcoded-labels guard.
- `test_views.py` — access control, signed-URL delivery, thumbnails-only, the
  board and its three composing filters, the drawer-vs-page split, and the full
  core loop end to end.
- `test_editing.py` — the login page carrying no chrome, the optional name and
  the specs chosen at creation, the merged activity feed (including an
  `assertNumQueries` guard on it), the in-drawer editors, and a template-hygiene
  test that fails on any multi-line `{# #}` comment.
- `test_settings.py` — that `/admin/` is gone, every settings section rendering
  both ways, and the invariants admin used to give for free: one initial status,
  retiring instead of deleting, and refusing to deactivate the last account.

---

See also: [database.md](database.md) · [local-setup.md](local-setup.md) ·
[deploy.md](deploy.md) · [restore-test.md](restore-test.md)
