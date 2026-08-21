# Restore drill

An untested backup is not a backup. Run this **before go-live**, and again
whenever the database or the backup command changes.

Budget: about 20 minutes.

## 1. Take a dump

```bash
.venv/bin/python manage.py dump_to_r2 --keep-local /tmp/selvedge-test.sql.gz
```

Confirm the object appears under the `backups/` prefix and that the local copy
is non-trivial in size. A dump measured in kilobytes when the database holds
real designs means something failed quietly.

## 2. Restore into a scratch database

Never restore over production. Use a Neon branch or a local container:

```bash
docker run -d --name restore-test -e POSTGRES_PASSWORD=x -p 5440:5432 postgres:16
gunzip -c /tmp/selvedge-test.sql.gz | psql -h 127.0.0.1 -p 5440 -U postgres
```

## 3. Check what came back

```sql
SELECT count(*) FROM designs_design;
SELECT count(*) FROM designs_version;
SELECT count(*) FROM designs_comment;
SELECT count(*) FROM designs_statustransition;
SELECT code, name FROM designs_design ORDER BY created_at DESC LIMIT 5;
```

Every count must match production. Then confirm the parts that matter most:

- A design that was approved still has exactly one version with
  `is_approved = true`.
- The version tree is intact — `parent_id` values still resolve.
- `designs_statustransition` still shows who approved what, including any
  `is_self_approval` rows.

## 4. Check the images separately

The database stores keys, not images. Pick an `image_key` from the restored
`designs_version` table and confirm the object still exists in R2. A restored
database pointing at missing objects is a restored index of nothing.

## 5. Write down the result

Record the date, who ran it, the row counts, and anything that went wrong. If
the drill did not happen, the backup is unverified — say so plainly rather than
assuming it works.

## 6. Tear down

```bash
docker rm -f restore-test && rm /tmp/selvedge-test.sql.gz
```
