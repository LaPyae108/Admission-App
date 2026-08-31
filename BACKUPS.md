# Database Backups
 
Nightly backups run automatically via `.github/workflows/backup-database.yml`.
This is separate from Neon's own point-in-time recovery - it protects
against Neon-side problems too, not just bad queries.
 
 
## One-time setup
 
1. **Get the direct (non-pooled) connection string** from the Neon
   dashboard. It looks like your existing `DATABASE_URL`, but the
   hostname does **not** contain `-pooler`.
 
2. **Add it as a repo secret**: GitHub repo -> Settings -> Secrets and
   variables -> Actions -> New repository secret.
   - Name: `DATABASE_URL_DIRECT`
   - Value: the direct connection string from step 1
 
3. That's it - the workflow will run on its own schedule from here.
   Backups show up under the Actions tab -> pick a run -> Artifacts.
 
4. **Optional, for off-GitHub storage too**: add `AWS_ACCESS_KEY_ID`,
   `AWS_SECRET_ACCESS_KEY`, and `BACKUP_S3_BUCKET` as repo secrets
   (works with AWS S3, Backblaze B2, Cloudflare R2, etc. - for
   non-AWS providers also add `BACKUP_S3_ENDPOINT_URL`).
 
 
## Testing it right now
 
Don't wait for the 3am schedule to find out if this works. Go to the
Actions tab -> "Database Backup" -> "Run workflow" -> confirm it
succeeds and an artifact appears.
 
 
## Restoring from a backup
 
**Always restore into a scratch/test database first** - never run a
restore command directly against production without a very good
reason.
 
1. Download the `.sql.gz` file from the workflow run's Artifacts
   section (or from your S3-compatible bucket, if configured).
 
2. Decompress it:
   ```
   gunzip backup-2026-08-31T20-30-00Z.sql.gz
   ```
   This produces `backup-2026-08-31T20-30-00Z.sql`.
 
3. Restore into a target database:
   ```
   psql "postgresql://user:password@host/dbname?sslmode=require" \
       < backup-2026-08-31T20-30-00Z.sql
   ```
 
   To create a quick scratch database on Neon for testing a restore,
   use a Neon branch instead of a whole new project - it's
   instant and free to create.
 
4. Point a local copy of the app at that scratch database
   (a temporary `DATABASE_URL` in `.env`) and confirm it starts up
   and the data looks right.
 
 
## A note on retention
 
- GitHub Actions artifacts here are kept for 30 days
  (`retention-days: 30` in the workflow), then auto-deleted.
- If you add S3-compatible storage, set a lifecycle rule in that
  bucket's own settings to auto-expire old backups after however
  long you want to keep them - that's configured on the bucket
  itself, not in this workflow.
