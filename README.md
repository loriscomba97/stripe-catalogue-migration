# Stripe Catalogue Migration

**Move live subscriptions to a new Stripe product catalogue without changing what customers pay.**

This repository contains Python scripts and SQL for a catalogue migration where every existing price gets an exact mirror under a new product. The subscription keeps its amount, currency, interval, quantity, renewal date, payment method and discounts. Entitlement moves from product names to price metadata.

The project was extracted from a completed migration with subscriptions, lifetime licenses, perpetual licenses with update windows and rent-to-own plans. Account IDs and customer data are replaced by configuration and placeholders.

[Get started](#get-started) · [Migration runbook](docs/RUNBOOK.md) · [Catalogue conventions](docs/CONVENTIONS.md) · [Lessons](docs/LESSONS.md)

[![CI](https://github.com/loriscomba97/stripe-catalogue-migration/actions/workflows/ci.yml/badge.svg)](https://github.com/loriscomba97/stripe-catalogue-migration/actions/workflows/ci.yml)
[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

## See the migration before it writes

The batch starts with a dry run. It rebuilds the source-to-destination map from the live API, writes `out/plan.csv`, lists every subscription it would migrate and groups every skipped subscription by reason.

```text
124 subscriptions: 117 to migrate, 7 skipped

SKIPPED
     3  no_mirror[studio/web/std]
     2  renews_imminently
     1  has_schedule
     1  multi_item[2]
```

The numbers above are illustrative. Review the plan from your own account before adding `--apply`.

## The migration model

Stripe prices keep their product association. Reorganizing a catalogue therefore uses four steps:

1. Create the new products.
2. Create one mirror for every legacy price with live subscribers. Match amount, currency, interval, interval count and `tax_behavior`.
3. Add structured metadata to the new prices, then swap each subscription item to its exact mirror with `proration_behavior: none`.
4. Resolve access from price metadata instead of product names.

The repository tags and verifies prices, plans and applies the subscription swaps, and provides the entitlement SQL. It does not create products or mirror prices.

## Get started

Requires Python 3.9 or later and `stripe-python` 10 or later. The SQL targets Supabase.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.py config.py
```

Fill `config.py` with your product IDs, tier rules and a unique idempotency prefix. Load `STRIPE_API_KEY` from a local secret store. Do not paste a live key into a script, commit it or include it in a report.

Run the read-only checks and dry runs first:

```bash
cd scripts
python audit_tax_behavior.py
python write_price_metadata.py
python migrate_one.py
python migrate_all.py
```

Deploy [`sql/entitlements.sql`](sql/entitlements.sql) before the batch. Confirm that legacy and new prices resolve correctly, then follow the [migration-day runbook](docs/RUNBOOK.md) for the rehearsal, first batch, full run and verification.

## Safety model

- **Dry run first.** Scripts that change Stripe require `--apply`. Live subscription changes also require typing `yes`.
- **Exact mirrors only.** Routing uses tier, channel, rent-to-own state, currency, interval, interval count and amount. Missing or ambiguous matches are skipped.
- **No silent contract changes.** The batch rejects tax mismatches, multiple items, schedules, connected applications and renewals inside the configured safety window.
- **Verify every write.** Each subscription is read before and after. Only `price_id` and `product` may differ. The batch stops at the first unexpected change.
- **Safe retries and rollback.** Every write uses an idempotency key. Rollback uses a separate namespace because Stripe returns the saved response when a key is reused.

Generated plans, results and exports contain subscription, customer and price IDs. `out/`, `*.csv` and `config.py` are ignored, but they still need restricted storage. `export_migration_origins.py` includes customer email unless you pass `--no-email`.

## What is included

| Area | Files | Purpose |
|---|---|---|
| Configuration | [`config.example.py`](config.example.py) | Account-specific IDs, routing and metadata rules |
| Migration | [`scripts/`](scripts) | Audit, metadata plan, rehearsal, batch, verification and rollback |
| Entitlement | [`sql/`](sql) | Reference schema, resolver, projections and communication queries |
| Operations | [`docs/RUNBOOK.md`](docs/RUNBOOK.md) | Ordered checklist for migration day |
| Conventions | [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md) | Price nickname grammar and metadata keys |
| Lessons | [`docs/LESSONS.md`](docs/LESSONS.md) | Failures and edge cases found during the original migration |

## Limits

This is a reference implementation, not a drop-in migration. It does not create the target catalogue, sync Stripe into a database, complete rent-to-own plans or support plain Postgres without adapting the Supabase roles and user lookup. Test the routing and SQL against your own schema and API version before using a live key.

## License

[MIT](LICENSE) © 2026 Loris Comba.
