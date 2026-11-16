CREATE TABLE IF NOT EXISTS feed_cache (
  cache_key TEXT PRIMARY KEY,
  fetched_at_ms INTEGER NOT NULL,
  payload TEXT NOT NULL,
  lease_until_ms INTEGER NOT NULL DEFAULT 0
);
