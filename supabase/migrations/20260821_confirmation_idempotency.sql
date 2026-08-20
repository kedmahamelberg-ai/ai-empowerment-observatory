-- Preserve the hash of the most recently consumed confirmation token.
-- This lets a repeated click show "already confirmed" without keeping the
-- active confirmation token in the pending-token column.

alter table public.newsletter_subscribers
    add column if not exists last_confirmation_token_hash text;

alter table public.newsletter_subscribers
    add column if not exists last_confirmation_used_at timestamptz;

create index if not exists newsletter_subscribers_used_confirmation_idx
    on public.newsletter_subscribers(last_confirmation_token_hash)
    where last_confirmation_token_hash is not null;

comment on column public.newsletter_subscribers.last_confirmation_token_hash is
    'SHA-256 hash of the most recently consumed double-opt-in token; retained only for idempotent repeated-click handling.';

comment on column public.newsletter_subscribers.last_confirmation_used_at is
    'Timestamp at which the most recently consumed double-opt-in token was accepted.';
