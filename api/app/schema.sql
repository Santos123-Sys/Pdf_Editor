CREATE TABLE IF NOT EXISTS documents (
  id uuid PRIMARY KEY, name text NOT NULL, original_path text NOT NULL,
  original_hash text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz, deleted_at timestamptz
);
CREATE TABLE IF NOT EXISTS versions (
  document_id uuid NOT NULL REFERENCES documents(id), number integer NOT NULL,
  path text NOT NULL, digest text NOT NULL, operation text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(document_id, number)
);
CREATE TABLE IF NOT EXISTS events (
  id uuid PRIMARY KEY, document_id uuid NOT NULL REFERENCES documents(id),
  kind text NOT NULL, digest text NOT NULL, details jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS invitations (
  token uuid PRIMARY KEY, document_id uuid NOT NULL REFERENCES documents(id),
  version integer NOT NULL, signer_name text NOT NULL, signer_email text,
  page integer NOT NULL, x real NOT NULL, y real NOT NULL,
  consent_at timestamptz, sealed_hash text, created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE invitations ADD COLUMN IF NOT EXISTS expires_at timestamptz NOT NULL DEFAULT (now() + interval '7 days');
ALTER TABLE invitations ADD COLUMN IF NOT EXISTS revoked_at timestamptz;
ALTER TABLE invitations ADD COLUMN IF NOT EXISTS sealed_version integer;
ALTER TABLE invitations ADD COLUMN IF NOT EXISTS width real NOT NULL DEFAULT 240;
ALTER TABLE invitations ADD COLUMN IF NOT EXISTS height real NOT NULL DEFAULT 50;
CREATE INDEX IF NOT EXISTS events_document_time ON events(document_id, created_at, id);
CREATE INDEX IF NOT EXISTS documents_expiry ON documents(expires_at) WHERE deleted_at IS NULL;
CREATE OR REPLACE FUNCTION reject_event_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Audit events are append-only';
END;
$$;
DROP TRIGGER IF EXISTS events_append_only ON events;
CREATE TRIGGER events_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON events
FOR EACH STATEMENT EXECUTE FUNCTION reject_event_mutation();

ALTER TABLE events ALTER COLUMN created_at SET DEFAULT clock_timestamp();
