-- ==========================================================================
-- Reference schema
-- ==========================================================================
--
-- What the SQL in this folder expects. Rename `billing` to your schema
-- throughout (search and replace). Column names follow the common
-- Supabase Stripe-sync template; adapt if yours differ.
--
-- Everything the resolver needs is in price and product METADATA, synced
-- from Stripe. Nothing here stores entitlement — it is derived at read time.

create schema if not exists billing;

-- Synced from Stripe ------------------------------------------------------

create table if not exists billing.products (
  id        text primary key,
  active    boolean not null default true,
  name      text not null,
  metadata  jsonb
);

create table if not exists billing.prices (
  id              text primary key,
  product_id      text not null references billing.products(id) on delete cascade,
  active          boolean not null default true,
  unit_amount     bigint not null,
  currency        text not null,
  type            text,                 -- 'recurring' | 'one_time'
  interval        text,                 -- 'month' | 'year' | null
  interval_count  integer,
  metadata        jsonb                 -- tier / kind / channel / rto / visibility / grants
);

create table if not exists billing.subscriptions (
  id                   text primary key,      -- a Stripe subscription belongs to ONE
  user_id              uuid not null,         -- customer; do not composite-key with user
  status               text not null,         -- Stripe status, incl. 'paused'
  price_id             text references billing.prices(id),
  quantity             integer not null default 1,
  metadata             jsonb,
  created              timestamptz not null,  -- timestamptz, never date: migration
  current_period_start timestamptz,           -- order and the post-run diff need
  current_period_end   timestamptz,           -- time-of-day resolution
  cancel_at_period_end boolean not null default false,
  canceled_at          timestamptz,
  trial_end            timestamptz
);
create index if not exists subscriptions_user_id_idx on billing.subscriptions(user_id);

-- One-time purchases -------------------------------------------------------

create table if not exists billing.lifetimes (
  id          text primary key,               -- Stripe PaymentIntent (pi_...)
  user_id     uuid not null,
  price_id    text references billing.prices(id),
  created     timestamptz not null,
  metadata    jsonb,
  revoked_at  timestamptz                     -- refunds: revoke, don't delete
);
create index if not exists lifetimes_user_id_idx on billing.lifetimes(user_id);

create table if not exists billing.perpetuals (   -- owns it, updates for a window
  id          text primary key,
  user_id     uuid not null,
  price_id    text references billing.prices(id),
  created     timestamptz not null,
  end_date    date,                           -- updates until; access is permanent
  revoked_at  timestamptz
);
create index if not exists perpetuals_user_id_idx on billing.perpetuals(user_id);

-- Trials tracked locally, not in Stripe -----------------------------------

create table if not exists billing.trials (
  id          text primary key,
  user_id     uuid not null,
  created_at  timestamptz not null default now(),
  ends_at     timestamptz not null,           -- timestamptz: a date column makes the
  revoked_at  timestamptz                     -- trial length vary by signup hour
);
create unique index if not exists trials_one_active_per_user
  on billing.trials(user_id) where revoked_at is null;
