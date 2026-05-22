-- Run this in your Supabase SQL editor.
-- If you already ran the M0 schema, drop existing tables first:
--   DROP TABLE IF EXISTS publish_queue, generated_posts, raw_articles, rss_sources CASCADE;

-- Source discovery results (replaces rss_sources from M0)
CREATE TABLE sources (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    key          text UNIQUE NOT NULL,      -- "hn", "sdtimes", "openai"
    url          text NOT NULL,             -- original URL passed to --add
    method       text NOT NULL,             -- "rss" | "rsshub" | "html" | "playwright"
    feed_url     text,                      -- set when method is rss or rsshub
    rsshub_slug  text,                      -- set when method is rsshub
    pw_wait_for  text,                      -- set when method is playwright
    added_at     timestamptz DEFAULT now(),
    last_fetched timestamptz
);

-- Every article fetched (deduplicated by url)
CREATE TABLE raw_articles (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id    uuid REFERENCES sources(id),
    title        text NOT NULL,
    url          text UNIQUE NOT NULL,
    summary      text,
    published_at timestamptz,
    fetched_at   timestamptz DEFAULT now(),
    filtered     boolean DEFAULT false,
    viral_score  integer,
    content_hash text UNIQUE
);

-- Generated posts awaiting review or publishing
CREATE TABLE generated_posts (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    article_id   uuid REFERENCES raw_articles(id),
    summary_text text,
    caption      text,
    tone         text,
    image_url    text,
    image_type   text,
    status       text DEFAULT 'pending_review',
    created_at   timestamptz DEFAULT now(),
    reviewed_at  timestamptz,
    published_at timestamptz
);

-- Scheduled publish times
CREATE TABLE publish_queue (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    post_id      uuid REFERENCES generated_posts(id),
    scheduled_at timestamptz NOT NULL,
    status       text DEFAULT 'pending'
);
