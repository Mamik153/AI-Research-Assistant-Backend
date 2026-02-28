-- Supabase Setup for AI Research Assistant Backend
-- Run these statements in the Supabase SQL Editor (https://supabase.com/dashboard)
--
-- IMPORTANT: Run sections in order. Section 5 (Security) should be run
-- for any production/live deployment.

-- =========================================================================
-- 1. Enable the pgvector extension
-- =========================================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- =========================================================================
-- 2. Create the paper_chunks table for storing embeddings and metadata
-- =========================================================================

CREATE TABLE IF NOT EXISTS paper_chunks (
    id TEXT PRIMARY KEY,
    arxiv_id TEXT NOT NULL,
    content TEXT NOT NULL,
    embedding VECTOR(384),
    chunk_type TEXT NOT NULL,
    chunk_position TEXT,
    chunk_index INTEGER,
    total_chunks INTEGER,
    title TEXT,
    authors TEXT,
    published TEXT,
    pdf_url TEXT,
    topic_query TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_paper_chunks_embedding ON paper_chunks
    USING ivfflat (embedding vector_l2_ops) WITH (lists = 100);

CREATE INDEX IF NOT EXISTS idx_paper_chunks_arxiv_id ON paper_chunks (arxiv_id);

-- =========================================================================
-- 3. Create the RPC function for similarity search
-- =========================================================================

CREATE OR REPLACE FUNCTION match_chunks(
    query_embedding VECTOR(384),
    match_threshold FLOAT,
    match_count INT
)
RETURNS TABLE (
    id TEXT,
    content TEXT,
    chunk_type TEXT,
    chunk_position TEXT,
    chunk_index INTEGER,
    total_chunks INTEGER,
    title TEXT,
    authors TEXT,
    published TEXT,
    arxiv_id TEXT,
    pdf_url TEXT,
    topic_query TEXT,
    similarity FLOAT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    RETURN QUERY
    SELECT
        pc.id,
        pc.content,
        pc.chunk_type,
        pc.chunk_position,
        pc.chunk_index,
        pc.total_chunks,
        pc.title,
        pc.authors,
        pc.published,
        pc.arxiv_id,
        pc.pdf_url,
        pc.topic_query,
        (1 - (pc.embedding <-> query_embedding))::FLOAT AS similarity
    FROM paper_chunks pc
    WHERE 1 - (pc.embedding <-> query_embedding) > match_threshold
    ORDER BY pc.embedding <-> query_embedding
    LIMIT match_count;
END;
$$;

-- =========================================================================
-- 4. Create the storage bucket
-- =========================================================================

-- The bucket is public so frontends can load image URLs directly.
-- Write access is locked down via storage policies in section 5.
INSERT INTO storage.buckets (id, name, public)
VALUES ('research-assets', 'research-assets', true)
ON CONFLICT (id) DO NOTHING;

-- =========================================================================
-- 5. SECURITY — Row Level Security + Storage Policies
-- =========================================================================
-- The backend connects with the service_role key, which BYPASSES RLS.
-- These policies ensure that the anon key (public, extractable from
-- frontend JS) cannot read, write, or delete any data.

-- 5a. Enable RLS on paper_chunks (blocks all anon access by default)
ALTER TABLE paper_chunks ENABLE ROW LEVEL SECURITY;

-- No SELECT/INSERT/UPDATE/DELETE policies are created for the anon role,
-- so all anon access is denied. The service_role key is unaffected.

-- 5b. Storage policies — public READ, service-role-only WRITE/DELETE
--
-- Allow anyone to read files (images need to be loadable by frontends):
CREATE POLICY "Public read access"
    ON storage.objects FOR SELECT
    USING (bucket_id = 'research-assets');

-- Only the service_role (backend) can upload files:
CREATE POLICY "Service role upload"
    ON storage.objects FOR INSERT
    WITH CHECK (
        bucket_id = 'research-assets'
        AND auth.role() = 'service_role'
    );

-- Only the service_role (backend) can overwrite/update files:
CREATE POLICY "Service role update"
    ON storage.objects FOR UPDATE
    USING (
        bucket_id = 'research-assets'
        AND auth.role() = 'service_role'
    );

-- Only the service_role (backend) can delete files:
CREATE POLICY "Service role delete"
    ON storage.objects FOR DELETE
    USING (
        bucket_id = 'research-assets'
        AND auth.role() = 'service_role'
    );

-- 5c. Restrict the match_chunks RPC to authenticated callers only.
-- The anon key is technically "authenticated" in Supabase, so to fully
-- lock this down, revoke execute from anon and grant only to service_role:
REVOKE EXECUTE ON FUNCTION match_chunks FROM anon;
REVOKE EXECUTE ON FUNCTION match_chunks FROM authenticated;

-- =========================================================================
-- 6. OPTIONAL — Additional hardening for production
-- =========================================================================

-- 6a. Prevent anon from reading paper_chunks directly via PostgREST
--     (RLS already blocks this, but belt-and-suspenders):
REVOKE ALL ON paper_chunks FROM anon;

-- 6b. If you want to rate-limit or audit Supabase access, enable the
--     pgaudit extension (Supabase Pro plan required):
-- CREATE EXTENSION IF NOT EXISTS pgaudit;

-- 6c. In the Supabase Dashboard, also configure:
--     - Settings > API > "Enable Row Level Security" confirmation
--     - Settings > Database > Connection pooling (use PgBouncer for prod)
--     - Settings > Auth > Disable signup (no user accounts needed)
--     - Settings > API > Custom CORS origins (lock to your frontend domain)
