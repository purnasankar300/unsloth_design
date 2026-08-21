# Deploying Selvedge

Target: a single Ubuntu 24.04 VPS, PostgreSQL on Neon, images on Cloudflare R2.
Estimated running cost is around ₹1,100/month.

All service accounts — Neon, Cloudflare, the VPS — must be on the **company card
and a shared company account**, not a personal account belonging to whoever set
them up. An expired personal card is a common way internal tools die.

## 1. Neon

- **Launch plan, not the free plan.** The free plan suspends the project when
  compute, storage or transfer quotas run out, gives a 6-hour restore window,
  and has no automated backup schedule. None of that is acceptable for the
  only record of approved designs.
- **Disable scale-to-zero**, so the first request each morning is not a cold
  start.
- Region: the closest one to the team (`ap-southeast-1` for India).
- Copy the pooled connection string into `DATABASE_URL`, keeping
  `?sslmode=require`.

## 2. Cloudflare R2

- Create a bucket, e.g. `selvedge-designs`. **Keep it private** — never attach a
  public development URL or a public bucket policy. Images are served only
  through the application, which hands out signed URLs valid for ten minutes,
  so access is revocable and an unreleased design is not visible to anyone who
  happens to hold a link.
- Create an API token scoped to that one bucket (Object Read & Write).
- `S3_ENDPOINT_URL` is `https://<account-id>.r2.cloudflarestorage.com`.
- Add a **lifecycle rule on the `backups/` prefix: expire after 30 days.** Leave
  every other prefix alone — designs and versions are never deleted.

## 3. The VPS

```bash
adduser selvedge && usermod -aG sudo selvedge
apt update && apt install -y python3.12-venv postgresql-client nginx git
```

Harden before anything else is exposed:

```bash
ufw default deny incoming && ufw allow OpenSSH && ufw allow 'Nginx Full' && ufw enable
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl restart ssh
```

Deploy the code:

```bash
git clone <repo> /srv/selvedge && cd /srv/selvedge
python3 -m venv .venv && .venv/bin/pip install -e . gunicorn whitenoise
install -m 600 -o selvedge -g selvedge /dev/null .env   # then fill it in
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py createsuperuser
```

`.env` must be mode `600` and owned by the service user. `DJANGO_DEBUG=false`
turns on HSTS, secure cookies and the SSL redirect.

### systemd

`/etc/systemd/system/selvedge.service`:

```ini
[Unit]
Description=Selvedge
After=network.target

[Service]
User=selvedge
WorkingDirectory=/srv/selvedge
EnvironmentFile=/srv/selvedge/.env
ExecStart=/srv/selvedge/.venv/bin/gunicorn config.wsgi:application \
  --bind 127.0.0.1:8000 --workers 3 --timeout 120
Restart=always

[Install]
WantedBy=multi-user.target
```

`--timeout 120` because a request can be uploading a 20 MB photograph and
generating a thumbnail.

### nginx

Proxy to `127.0.0.1:8000`, serve `/static/` from `staticfiles/`, and set
`client_max_body_size 25m` to match `MAX_UPLOAD_BYTES`. Terminate TLS with
certbot.

## 4. Users

Five or six accounts. The first comes from `manage.py createsuperuser`; the rest are added
in the app under Settings → Team. There are **no roles** — any user
can do anything, including approving their own design. That is deliberate for a
team this size, and self-approval is recorded and displayed as such.

## 5. Backups

Neon's managed backups are primary. Add the independent weekly copy:

```cron
15 2 * * 0 cd /srv/selvedge && .venv/bin/python manage.py dump_to_r2 >> /var/log/selvedge-backup.log 2>&1
```

Then do the restore drill in `restore-test.md` **before go-live**.

## 6. Monthly check — one named owner, about 15 minutes

- The weekly backup actually ran (check the log and the `backups/` prefix).
- The card on the Neon and Cloudflare accounts is still valid.
- Disk on the VPS is not filling up.
- Skim `/insights/` — the numbers there are what a decision
  about automated image editing will eventually rest on.

## 7. One decision that is not technical

Designers will upload unreleased designs to external consumer AI tools. Free
consumer tiers generally permit the provider to train on that data. Either put
the team on paid accounts, or accept the risk formally and in writing. Do not
leave it undecided by default.
