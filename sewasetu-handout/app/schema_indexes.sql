-- Performance indexes for the Sewa Setu application database.
-- These are safe to apply to a running database. All are CREATE IF NOT EXISTS.
-- Applied automatically at app startup; this file is kept for manual rollouts.

-- Speeds up: duplicate-check (WHERE mobile=?), citizen login lookup, admin queries
CREATE INDEX IF NOT EXISTS idx_applications_mobile
    ON applications (mobile);

-- Speeds up: admin list view (WHERE status=? ORDER BY submitted_at) and dashboard counts
CREATE INDEX IF NOT EXISTS idx_applications_status_submitted
    ON applications (status, submitted_at);

-- Speeds up: RTPS scheduler cutoff query (WHERE status='PENDING' AND submitted_at < ?)
-- Partial index — only indexes PENDING rows, very small and fast
CREATE INDEX IF NOT EXISTS idx_applications_pending_submitted
    ON applications (submitted_at)
    WHERE status = 'PENDING';

-- Speeds up portal_users login lookups (already has PK on mobile, no extra needed)
