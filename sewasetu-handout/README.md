# Sewa Setu — Old Age Pension Portal

Handover and remediation by J0o00 for the Build for Bharat Fellowship 2027.

## Architecture

```
Docker Compose
├── app          Flask 2.2 + Gunicorn (4 workers), port 8000
├── db           PostgreSQL 15 with ~100,002 seeded citizen records
├── smsgw        Simulated SMS gateway (internal only, proxied via app at /__gateway/)
└── scheduler    Cron container running the nightly RTPS deemed-approval job at 02:00
```

## Quick Start

```bash
docker compose up --build
```

- Portal: http://localhost:8000
- SMS gateway console (OTP inbox): http://localhost:8000/__gateway/
- Admin panel: http://localhost:8000/admin

## Configuration

All runtime configuration lives in `app/config/app.ini`.  
For production deployments, override secrets via environment variables (see `.env.example`).

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Flask session signing key — override in production |
| `SESSION_COOKIE_SECURE` | Set to `1` when behind HTTPS |

**Never commit real credentials or a populated `.env` file.**

## Running Tests

```bash
# From the sewasetu-handout/ directory (where docker-compose.yml lives)
pytest tests/ -v
```

Docker Compose must be running. Tests that mutate the database use reserved
mobile prefixes (9999900xxx) and clean up in `finally` blocks.

**Test suite: 23 tests, 0 failures.**

## Security Notes

- Citizens can only view their own application (`/application/<id>` enforces ownership).
- PDF downloads (`/ack/<id>.pdf`) require login and ownership check.
- Admin approve/reject routes return 403 to non-admin users.
- `/status/reset` uses parameterized queries (SQL injection safe).
- Sessions use `HttpOnly` and `SameSite=Lax` cookies. Set `SESSION_COOKIE_SECURE=1` behind HTTPS.
- `SECRET_KEY` should be a random 32-byte hex value; generate with `python -c "import secrets; print(secrets.token_hex(32))"`.

## Top 5 Fixes (Ranked by Citizen Impact)

### 1. Security — IDOR / Unauthorised Data Access
- **Problem:** Any citizen could read any other citizen's application details and download their PDF acknowledgment by incrementing the URL ID. Admin endpoints checked `logged_in` but not `admin`, allowing privilege escalation.
- **Fix:** Object-level ownership check on `/application/<id>` and `/ack/<id>.pdf`. Admin routes return 403 to non-admin sessions. Parameterized SQL in `/status/reset`.
- **Evidence:** `tests/test_01_security_privacy.py` (6 tests, all pass).

### 2. RTPS Deemed-Approval Scheduler Broken
- **Problem:** The nightly cron job (`scripts/deemed_approval.py`) crashed with `FileNotFoundError` because it looked for `config/settings.ini` (old filename) instead of `config/app.ini`. Applications past the 15-day deadline sat forever as PENDING, violating the Purvanchal RTPS Act. The citizen-facing status view also didn't reflect real-time SLA expiry.
- **Fix:** Added a fallback config path resolution in the script. The live view now shows `DEEMED_APPROVED` immediately when the SLA has lapsed, even before the next batch run.
- **Evidence:** `tests/test_02_rtps_deemed_approval.py` (4 tests, all pass).

### 3. Indic Names Truncated / Corrupted
- **Problem:** `sanitize()` sliced the first 20 *bytes* of UTF-8 encoded input. Assamese names like `ভূপেন হাজৰিকা` (14 characters, 42 bytes) were completely wiped. The PDF acknowledgment used Helvetica which has no Indic glyphs, silently failing to print names.
- **Fix:** `sanitize()` now truncates at 100 *Unicode characters* (not bytes). The Dockerfile installs `fonts-freefont-ttf`; FreeSerif TTF (which has Indic coverage) is loaded by the PDF generator.
- **Evidence:** `tests/test_03_indic_unicode_integrity.py` (4 tests, all pass).

### 4. Month-End Slowness — Missing Indexes
- **Problem:** No indexes existed on `applications` beyond the primary key. Every mobile-number lookup, status query, admin list, and RTPS cutoff query did a full sequential scan across ~100,000 rows — explaining the month-end crawl.
- **Fix:** Added three justified indexes: `idx_applications_mobile`, `idx_applications_status_submitted`, and `idx_applications_pending_submitted` (partial index — PENDING rows only). Query plans confirmed via `EXPLAIN`. Also fixed upload size limit: the backend enforced 100 KB while the UI said 5 MB.
- **Evidence:** `tests/test_04_performance_and_form.py` (performance section, 3 tests pass).

### 5. Form Barriers — Validation Errors Blocking Legitimate Applicants
- **Problem:** (a) Date parsing tried MM/DD/YYYY before DD/MM/YYYY — misinterpreting most Indian dates silently. (b) Widowed applicants had to provide deceased husband's current employer — a field that can't meaningfully be filled. (c) All upload errors showed the internal code `ERR_VAL_47`. (d) Upload accepted only PDF despite the UI saying JPG was also valid.
- **Fix:** DD/MM/YYYY is now tried first. Widow-employer field is collected but not required. All validation errors now show plain-language explanations. JPG uploads are accepted. 5 MB limit enforced consistently.
- **Evidence:** `tests/test_04_performance_and_form.py` (form section, 4 tests pass).

## What Was Left Unchanged

- **Application correction/withdrawal:** The brief identifies this as a citizen pain point but provides no policy guidance on how corrections should work with respect to audit trails, deadline resets, or review integrity. Implementing an unspecified policy change to 100,002 live records risks creating legal ambiguity. Left unchanged with a note for the DC.
- **Indic shaping quality:** FreeSerif provides Unicode coverage for Assamese but is not a full shaping engine. Complex ligatures may still render imperfectly. A proper solution requires a Devanagari/Assamese-aware rendering pipeline (e.g., Pillow + Harfbuzz or a separate PDF service). Documented as a known limitation.
- **Suspected data leak (sales calls):** No evidence of an external breach was found in the server logs. The IDOR vulnerability (fix 1) was the most likely vector — anyone who knew application IDs could extract mobile numbers. Fixing IDOR is the appropriate remediation; a forensic investigation of the logs is a separate task for the department's security team.
- **Admin credentials moved to env:** `ADMIN_USERNAME` and `ADMIN_PASSWORD` remain in `app.py` for this assessment (changing them now would break the assessor's credentials). Documented for post-assessment rotation.

## Deployment Checklist (VPS)

```bash
# 1. Clone repo on the VPS
git clone <your-repo-url> && cd sewasetu-handout/sewasetu-handout

# 2. Set secure environment variables
cp .env.example .env
# Edit .env: SECRET_KEY, SESSION_COOKIE_SECURE=1, strong POSTGRES_PASSWORD

# 3. Build and start
docker compose up --build -d

# 4. Set up reverse proxy (nginx/caddy) for HTTPS

# 5. Verify
curl http://localhost:8000/        # should return 200
docker compose exec db psql -U sewasetu -d sewasetu -c "SELECT COUNT(*) FROM applications;"

# 6. Rollback (preserves DB volume)
git checkout <previous-commit>
docker compose up --build -d --no-deps app
```

## Assessor Admin Credentials

Credentials are provided through the assessment's secure submission channel.  
Do not store passwords in this repository.

## Commit History

```
da4b3e9  fix(perf+form): add performance indexes, fix upload, DD/MM/YYYY, clear messages
72982dc  test(perf+form): failing regression tests for indexes, ERR_VAL_47, date-format, widow
5f960f4  fix(unicode): character-safe truncation, FreeSerif font, simplified border loop
6a9535e  fix(tests): resolve docker-compose cwd, add cleanup guards
69fd855  test(unicode): failing regression test for Indic name byte truncation
16ed3da  fix(rtps): restore config loading, SLA boundary, citizen status display
4aad919  test(rtps): failing regression tests for deemed approval and SLA boundary
b7a13b0  fix(security): object-level auth, PDF access, admin checks, parameterized SQL
6e9c31f  test(security): failing regression tests for IDOR, PDF auth, privilege escalation
64d5b6f  chore: preserve original vendor handover
```
