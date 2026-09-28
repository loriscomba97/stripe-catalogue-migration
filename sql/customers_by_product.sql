-- ==========================================================================
-- Customers by product — subscriptions, lifetimes, perpetuals
-- ==========================================================================
--
-- One row per purchase, by the product the person ORIGINALLY bought. For
-- migrated subscriptions that comes from billing.migration_origins, built
-- by scripts/export_migration_origins.py, because after the migration the
-- subscriptions table only holds the new price.
--
-- Read-only. Export the result as CSV and filter on `product`.

with subs as (
  select coalesce(mo.old_product_name, pd.name) as product, 'subscription' as type,
         u.email, mo.email as billing_email, s.user_id, s.created::date as since,
         s.status, pr.unit_amount / 100.0 as list_price, upper(pr.currency) as currency,
         pr.interval as billing, null::text as coupon, null::date as updates_until,
         (mo.subscription_id is not null) as migrated, pd.name as now_on, s.id as stripe_id
  from billing.subscriptions s
  join billing.prices   pr on pr.id = s.price_id
  join billing.products pd on pd.id = pr.product_id
  join auth.users       u  on u.id  = s.user_id
  left join billing.migration_origins mo on mo.subscription_id = s.id
  where s.status in ('active', 'past_due', 'paused', 'trialing')
),
lifes as (
  select pd.name, 'lifetime', u.email, null, l.user_id, l.created::date, 'owned',
         pr.unit_amount / 100.0, upper(pr.currency), 'one-time', null, null::date, false, null, l.id
  from billing.lifetimes l
  join billing.prices   pr on pr.id = l.price_id
  join billing.products pd on pd.id = pr.product_id
  join auth.users       u  on u.id  = l.user_id
  where l.revoked_at is null
),
perps as (
  select coalesce(pd.name, '(no price on record)'), 'perpetual', u.email, null, p.user_id,
         p.created::date, 'owned', pr.unit_amount / 100.0, upper(pr.currency), 'one-time',
         null, p.end_date, false, null, p.id
  from billing.perpetuals p
  left join billing.prices   pr on pr.id = p.price_id
  left join billing.products pd on pd.id = pr.product_id
  join auth.users            u  on u.id  = p.user_id
  where p.revoked_at is null
),
everything as (select * from subs union all select * from lifes union all select * from perps)
select e.product, e.type, e.email, e.billing_email, e.since, e.status, e.list_price, e.currency,
       e.billing, e.coupon, e.updates_until, e.migrated, e.now_on,
       r.entitlement as entitlement_today, e.user_id, e.stripe_id
from everything e
cross join lateral billing.resolve_entitlement(e.user_id) r
order by e.product, e.type, e.since;

-- list_price is the price's listed amount, not necessarily what was paid.
-- billing_email is the Stripe customer email and can differ from the account
-- email when duplicate customers exist; for a mailing, use `email`.
