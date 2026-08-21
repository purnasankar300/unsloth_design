# Selvedge documentation

| Doc | What it covers |
|---|---|
| [local-setup.md](local-setup.md) | Getting the app running on your machine, and what to do when it will not |
| [application.md](application.md) | What the app is, how it is layered, the routes, the UI, and the rules that are easy to break |
| [database.md](database.md) | Every table, every column, and why the schema is shaped this way |
| [deploy.md](deploy.md) | VPS + Neon + Cloudflare R2 |
| [restore-test.md](restore-test.md) | The pre-go-live backup restore drill |

Start with **local-setup.md** if you want it running; **application.md** if you
want to change it; **database.md** if you want to understand the data.

`REQUIREMENTS.md` at the repository root is the frozen V1 spec and remains the
source of truth; its **§11b** records the deviations decided after the freeze,
the largest being that django-admin was removed and everything it did now lives
in the app under `/settings/`.

`CLAUDE.md` is the short brief for anyone — human or agent — picking the codebase
up cold. `index.html` is the current approved wireframe; `mockup.html` is the
earlier one it superseded. Neither is wired to the app.
