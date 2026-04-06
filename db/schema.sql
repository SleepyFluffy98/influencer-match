-- Run this once in your Supabase project's SQL editor
-- (Dashboard → SQL Editor → New query → paste → Run)

CREATE TABLE IF NOT EXISTS jobs (
    job_id      TEXT PRIMARY KEY,
    data        JSONB NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback_log (
    id          BIGSERIAL PRIMARY KEY,
    data        JSONB NOT NULL,
    logged_at   TEXT NOT NULL
);
