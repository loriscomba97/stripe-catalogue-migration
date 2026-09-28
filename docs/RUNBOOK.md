# Migration day

## Before, once

- [ ] Rotate the Stripe secret key if it has ever been in a terminal paste
      or a chat. Give production its own restricted key.
- [ ] `write_price_metadata.py` → `N prices, 0 to write`
- [ ] Confirm every new price reached your database: the resolver reads
      `tier` from there, and `subscriptions.price_id` has a foreign key to it
- [ ] `audit_tax_behavior.py`: decide what to do with every mismatch
- [ ] Deploy `sql/entitlements.sql`; the `'none'` query returns zero rows
- [ ] Repoint checkout, Payment Links and pricing tables at the new catalogue
- [ ] Save the expected outcome from `sql/migration_projection.sql`
- [ ] Save the baseline: `select entitlement, count(*) ... group by 1`

## Right before

- [ ] Pause every automation on `customer.subscription.updated`
- [ ] Disable any endpoint that can modify subscriptions (upgrade flows)
- [ ] Nobody edits subscriptions in the Stripe dashboard until you're done

## Run

```
python migrate_all.py                       # DESTINATION TIER and SKIPPED as expected?
python migrate_all.py --limit 10 --apply    # every row in out/results.csv is ok?
python migrate_all.py --apply
```

Spot-check one migrated subscription through the resolver between the ten
and the rest.

## Verify

- [ ] Entitlement counts match the projection, not the baseline, if any
      subscriptions moved tier
- [ ] `where entitlement = 'none'` → zero rows
- [ ] Migrated rows in your database show the new `price_id` (the webhook kept up)
- [ ] Re-enable automations

## After the diff passes, not before

- [ ] Archive legacy prices
- [ ] Handle the skipped subscriptions as their blockers clear
- [ ] `export_migration_origins.py --since <migration date>`: the lineage
      for reporting and comms

## If it goes wrong

```
python migrate_all.py --rollback out/results.csv --apply
```

Nothing is archived until the diff passes, so every source price is still
assignable. That's what makes rollback possible.
