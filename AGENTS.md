# Project instructions

- Read docs/实现手册.md section 12 as the current MVP source of truth. IMPLEMENTATION_MANUAL.md is an older reference.
- Use Supabase PostgreSQL and private Cloudflare R2 object storage for MVP. Do not start local PostgreSQL or MinIO.
- Local disk holds code, dependencies and bounded temporary processing only. Do not commit media, credentials, signed URLs or .env.
- Keep backend credentials out of VITE_* variables. Frontend talks to FastAPI, not directly to business tables.
- M1 skeleton is implemented; M2 manual SQL setup lives in supabase/. Do not invent working search/import/cloud connectivity. Reconcile manual SQL baseline before adding Alembic migrations.
- M3 R2 CLI operations are implemented in app.storage_cli; see docs/M3_STORAGE.md. Uploads stay unpublished, and current browser CORS configuration still needs user confirmation. Never expose operator CLI actions via anonymous HTTP endpoints.
- Store time as integer milliseconds; preserve video-version and subtitle-revision boundaries.
- M4 cloud import and local /review page are implemented; see docs/M4_CORPUS.md and docs/M4_NEW_SOURCE.md. The current dataset was reviewed; the user explicitly authorized preview/search and sharing for the current video version only. Download remains disabled. Do not set the automated `sync_verified` flag without evidence or expand rights/publish scope without explicit user authorization.
- Identify user-operated account/console steps explicitly. Explain unfamiliar terms in Chinese.
- Backend check: from services/backend run ../../.venv/Scripts/python.exe -m unittest discover -s tests -v.
- Frontend check: from apps/web run npm run build. Use scripts/start-web.ps1 to locate project portable Node when needed.
- Do not create paid resources, install system software, or delete source media without explicit authorization.
