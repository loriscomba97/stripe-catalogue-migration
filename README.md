# stripe-catalogue-migration

Scripts and SQL for moving live Stripe subscriptions onto a new product
catalogue **without changing what anyone pays**, and for resolving
entitlement from price metadata instead of product names.

Extracted from a real migration: a live subscription base, three licence
types (subscription, lifetime, perpetual with an update window), a
rent-to-own plan Stripe doesn't model, one product line that had to be left
alone, and a legacy catalogue that had grown one price at a time. Every
guard in here exists because something went wrong without it.

## The idea

Stripe prices are immutable. You can't edit an amount, a currency, an
interval or a tax behaviour, and you can't move a price to another product.
So a catalogue reorganisation means:

1. Build the new products.
2. For every legacy price with live subscribers, create a **mirror** under
   the right new product: identical amount, currency, interval, interval
   count and `tax_behavior`, tagged with metadata.
3. Point each subscription's item at its mirror with
   `proration_behavior: none`. Nothing is billed, nothing resets.
4. Resolve entitlement from the price's metadata, so the app never has to
   know which product a price belongs to.

The scripts do steps 2 to 4 and refuse to do anything ambiguous.

## Safety model

Every script is **dry-run by default** and writes only with `--apply`.
Every write has an idempotency key, so a re-run after a crash is safe. The
batch migration snapshots every subscription before and after, allows only
`price_id` and `product` to differ, and **stops on the first unexpected
change**. Rollback uses a separate idempotency namespace, because reusing
the migration key would return Stripe's cached response and silently do
nothing.

Subscriptions that can't be moved safely are skipped with a named reason:
tax-behaviour mismatch, no mirror, more than one item, attached to a
schedule, renewing in the next two hours. Nothing is guessed.

## Requirements

Python 3.9+ and `stripe-python` 10 or later. The SQL targets Supabase
(see below).

## Quickstart

```
pip install -r requirements.txt
cp config.example.py config.py      # fill in your product ids
export STRIPE_API_KEY=$(cat ~/.stripe_key)   # never paste a key into a terminal

cd scripts
python audit_tax_behavior.py        # read-only: find the prices that will break
python write_price_metadata.py      # dry run, then --apply
python migrate_one.py               # rehearse on one subscription
python migrate_all.py               # dry run, read the skip breakdown
python migrate_all.py --limit 10 --apply
python migrate_all.py --apply
```

Deploy `sql/entitlements.sql` **before** the batch. It reads legacy and new
prices alike, so real users exercise it for as long as you like before
migration day, and the migration becomes a `price_id` swap on rows that
already resolve.

## Layout

```
config.example.py            every account-specific value; copy to config.py
scripts/
  common.py                  stripe-python compatibility, snapshot, diff
  audit_tax_behavior.py      the check that most often breaks "zero change"
  write_price_metadata.py    metadata from the nickname convention
  migrate_one.py             single-subscription rehearsal
  migrate_all.py             the batch, with pre-flight, ordering, rollback
  export_migration_origins.py  recover each sub's original product afterwards
sql/
  schema.sql                 what the SQL expects
  entitlements.sql           the resolver, trials, permissions
  migration_projection.sql   post-migration tier distribution, before you run
  customers_by_product.sql   who bought what, for communications
docs/
  CONVENTIONS.md             nickname grammar and metadata keys
  RUNBOOK.md                 the day, in order
  LESSONS.md                 what bit us
```

## What it does not do

- Create the new products or the mirror prices. That's a catalogue design
  decision; do it with your own script, then run `write_price_metadata.py`
  to tag them.
- Sync Stripe to your database. It assumes something already mirrors
  products and prices (with metadata) into tables the SQL can read.
- Handle rent-to-own completion. RTO is an ordinary subscription in Stripe;
  counting instalments and cancelling at term is yours to build.
- Work outside Supabase unmodified. The SQL uses `auth.uid()` and the
  `authenticated` / `service_role` roles; on plain Postgres, replace those
  with your own session-user function and roles.

## Licence

MIT. See `LICENSE`.
