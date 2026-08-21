# Garment Design Tracker — Requirements (V1)

**Status:** Frozen for build, with one open gap (see Section 9).
**Date:** 14 August 2026
**Audience:** Implementation (Claude Code)

---

## 1. Purpose and Scope

An internal web application for a 5–6 person garment design team, based in India.

**In scope:** Take a garment idea from a reference photo, through collaborative
visual iteration and review rounds, to a final approved, versioned design record.

**Out of scope (explicitly):**
- Purchase orders, vendor allocation, size/colour breakdown, production milestones,
  tech packs, sampling, QC, dispatch. All post-approval tracking lives in a
  different application. This app ends at "approved."
- **Any AI/LLM integration.** The app makes no API calls to any model. Image editing
  happens entirely outside the app, in external tools, by hand.

**Users:** 5–6 named internal users. No external/client access. No multi-tenancy.

---

## 2. What This Application Actually Is

A versioned image collaboration tool with a status pipeline. Roughly:

> Upload a reference image → get guided to an external editing tool → upload the
> edited result → others comment → someone uploads a revision → repeat → approve.

There is no cleverness in the middle. The value is in the record: what was tried,
who said what, which version won, and when it was approved.

---

## 3. Permissions Model

- **No roles.** Any user may perform any action, including approving a design they
  created. This is deliberate for a team of this size. Do not build a role system.
- **Attribution is mandatory.** Every create, upload, comment, status change, and
  approval records the acting user and a timestamp.
- Self-approval is permitted but must be plainly visible in the audit trail.

---

## 4. Architecture

| Layer | Choice |
|---|---|
| Framework | Django (latest LTS) + HTMX |
| Database | PostgreSQL 16+ on Neon (Launch plan) |
| Object storage | Cloudflare R2, via `django-storages` |
| App hosting | VPS (Ubuntu 24.04) |
| External editing | Manual, outside the app |

**Estimated running cost:** ~₹1,100/month.

### Why Django
Batteries-included: ORM, migrations, auth, and `django-admin` out of the box.
The app is essentially all CRUD-with-files. Do not use Gradio or Streamlit — they
are ML demo toolkits and cannot support this data model.

### Required packages
- `django-storages[s3]` (R2 is S3-compatible)
- `django-simple-history` (audit trail — a requirement, not optional)
- `psycopg[binary]`
- `Pillow` (thumbnail generation)

No HTTP client for external APIs is needed. There are none.

---

## 5. The Core Loop

1. User creates a Design and uploads a **reference image**.
2. App displays **guidance cards** for external editing tools — clickable, each
   opening the tool and showing step-by-step instructions for using it.
3. User edits the image externally, by hand, in that tool.
4. User returns and uploads the result as a **new version** of the design.
5. Other users view the version and leave **comments**.
6. Any user may upload a **further revision** in response to comments.
7. Steps 5–6 repeat as needed.
8. A user marks the design **approved**. Done.

### Guidance cards (Section 5, step 2)
- Static content, **editable via django-admin** — do not hardcode. Tools, URLs and
  instructions will change; a non-developer must be able to update them.
- Each card: tool name, link that opens the tool, and ordered step-by-step text.
- Initial set: Google Gemini chat, Google Flow. Add others as needed.
- This is presentational only. The app tracks nothing about what happens in the tool.

---

## 6. Data Model Requirements

Exact schema is left to implementation; these constraints are binding.

### Design
- Stable **auto-generated code** as the real identity (e.g. season/category/sequence).
  A user-supplied name is a **label on top**, not the identifier. Names get renamed
  and duplicated; codes must not.
- Belongs to a Category (categories managed via django-admin).
- Has a current status (see Section 9).
- Holds the original reference image.

### Version
- Belongs to a Design.
- **Versions form a tree, not a line.** Each version records its parent. Users must
  be able to branch back to the original reference rather than only chaining forward.
  This matters: AI-edited images degrade after several successive edit rounds, so a
  design deep in revision must be able to re-branch from the original.
- Stores: image reference, the requirement/description the user typed, creating user,
  timestamp, parent version.
- Exactly one version per design may be marked as the **approved/final** version.

### Comment
- Belongs to a **Version**, not to the Design. Feedback is about a specific image.
- Stores: author, timestamp, body.
- Comments are never deleted. Editing, if allowed, must retain history.

### Status transition
- A first-class record, not just a field on Design.
- Stores: from-status, to-status, acting user, timestamp, optional comment.

### Image handling
- Store the **file path/key** in Postgres. Never store image blobs in the database.
- Keys must be **immutable and opaque** — UUID or `design_id/version_id.ext`.
  Never use design names or user-supplied filenames as keys.
- On upload, compute and store: dimensions, file size, and a content hash.
  The hash catches duplicate uploads, which will happen with a manual flow.
- **Generate and store a thumbnail on upload.** Gallery views serve thumbnails,
  never full-size images — both a performance and an egress-cost requirement.
- **Nothing is ever deleted.** Original references and every version are retained
  permanently. Storage is cheap; a lost approved design is not.

### Authoritative asset fields
Externally edited images are a **visualisation aid, not a manufacturing spec.**
The design record must include fields for authoritative real-world assets: the actual
logo file, the actual colour code (Pantone or equivalent), and any real measurements.
Without these, a "final approved design" is a picture nobody can manufacture from.

---

## 7. Storage Configuration (Cloudflare R2)

- Access via `django-storages` with the S3 backend.
- **Bucket must be private.** Serve images through Django using short-lived signed
  URLs. Never expose public bucket URLs — unreleased designs must not be viewable by
  anyone holding a link, and access must be revocable.
- Lifecycle rule: expire database backup objects after 30 days.

---

## 8. Database Configuration

- PostgreSQL on Neon, **Launch plan** (~₹500/month). **Not the free plan** — the free
  plan suspends the project when compute, storage, or transfer quotas are exhausted,
  offers only a 6-hour restore window, and has no automated backup schedules.
- Scale-to-zero **disabled** to avoid cold starts on first request each morning.
- All credentials from environment variables. Never commit them.
- Set timezone to `Asia/Kolkata`.
- Neon's managed backups are the primary safety net. **Additionally** run a weekly
  `pg_dump` to R2 as a second, independent copy.
- **Test a restore before go-live.** An untested backup is not a backup.

---

## 9. OPEN GAP — Workflow Statuses

The review loop is defined (Section 5). The **named stages** are not.

Undecided:
- What are the actual status names between "created" and "approved"?
- Which transitions are legal from each status?
- What does rejection do — end the design, or return it for another revision round?
- Can a design be parked, put on hold, or abandoned? Status or separate flag?
- Is there a distinction between internal review and final approval?

**Instruction to implementer:** Build Sections 1–8 first. Because status transitions
are a first-class entity (Section 6), the pipeline can be defined later without a
schema rewrite. Do **not** hardcode a guessed set of statuses into business logic —
keep them configurable/data-driven.

A reasonable placeholder to start with, to be confirmed:
`Draft → In Review → Revision Requested → Approved`, plus `On Hold` and `Dropped`.

---

## 10. Instrumentation

Log from day one, visible in django-admin:

1. **Versions per design** (how many revision rounds actually happen)
2. **Uploads per day**, per user and total
3. **Time from creation to approval**

These numbers determine whether a future version should automate the image editing
via API — a decision costing ₹6,500–34,000/month depending on engine. Without real
data that decision is guesswork.

### Deferred, not rejected
Automated image editing via API. Candidate engines, to be settled by a bake-off with
real garment photos: Qwen-Image-Edit (~$0.025/edit), FLUX Kontext Pro (~$0.04),
Gemini 3 Pro Image (~$0.13). Keep version creation decoupled from image *origin* so
that adding an API-generated source later is an additive change, not a rewrite.

---

## 11. Known Limitations (Accepted, Do Not Relitigate)

Properties of AI image editing, not bugs. Users should be told these up front:

- The team's actual logo artwork will **not** be faithfully reproduced. Models redraw it.
- Exact Pantone or shade matching is **not achievable.**
- Readable text or slogans on garments render poorly.
- Dark-to-light colour conversions (navy shirt → white) are the **hardest** case,
  as fold shadows and fabric texture must be reconstructed. Expect several attempts.
- Garment details drift between successive generations. Hence the version tree.

### Confidentiality note
Designers will upload unreleased designs to external consumer AI tools. Free consumer
tiers generally permit the provider to use that data for model training. This is a
**decision the team must make explicitly** — either use paid accounts, or accept the
risk formally. Do not leave it undecided by default.

---

## 12. Operational Requirements

- All service accounts (Neon, R2, VPS) on a **company card and shared company
  account** — not a personal account belonging to whoever set it up. Expired personal
  cards are a common way internal tools die.
- One named person owns monthly checks: backups ran, card valid, disk not full.
  ~15 minutes/month.
- VPS: firewall configured, root SSH disabled.
