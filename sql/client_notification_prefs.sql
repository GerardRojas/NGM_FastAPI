-- Per-client notification preferences for the NGM Connect portal.
-- Default-on: the app treats a missing row as "all enabled", so this table only
-- needs rows for clients who opt OUT of something. Safe to run multiple times.

CREATE TABLE IF NOT EXISTS public.client_notification_prefs (
  client_id     uuid PRIMARY KEY REFERENCES public.clients(client_id) ON DELETE CASCADE,
  new_message   boolean NOT NULL DEFAULT true,
  new_invoice   boolean NOT NULL DEFAULT true,
  share_digest  boolean NOT NULL DEFAULT true,
  weekly_update boolean NOT NULL DEFAULT true,
  updated_at    timestamptz NOT NULL DEFAULT now()
);

-- Service-role only (the API mediates all reads/writes); no client-side RLS needed.
ALTER TABLE public.client_notification_prefs ENABLE ROW LEVEL SECURITY;
