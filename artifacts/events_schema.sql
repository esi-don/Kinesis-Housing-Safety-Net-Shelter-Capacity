CREATE TABLE IF NOT EXISTS events (
event_id TEXT PRIMARY KEY, source TEXT NOT NULL, anon_id TEXT NOT NULL,
site_id TEXT NOT NULL, zone TEXT, event_time TEXT NOT NULL, modality TEXT NOT NULL,
text_input TEXT, payload TEXT, consent_method TEXT NOT NULL,
consent_scope TEXT NOT NULL, privacy_token TEXT NOT NULL, geo_json TEXT,
ingested_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
CREATE INDEX IF NOT EXISTS events_source_time ON events(source,event_time);