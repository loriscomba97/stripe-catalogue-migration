-- ==========================================================================
-- Projection: where every live subscription will land, before you run it
-- ==========================================================================
--
-- Read-only. Mirrors the routing in scripts/migrate_all.py so you can see
-- the post-migration tier distribution — and compare routing rules — from
-- the database alone. Rename `billing` and the product ids.

create or replace view billing.v_migration_projection as
with new_products as (
  select id from billing.products where id in ('prod_REPLACE_ME_1', 'prod_REPLACE_ME_2', 'prod_REPLACE_ME_3')
),
upgrade_mirrors as (          -- mirrors on the higher tier, keyed by shape
  select p.currency, p.interval, p.interval_count, p.unit_amount,
         coalesce(p.metadata->>'channel', 'web') as channel,
         coalesce(p.metadata->>'rto', 'false') = 'true' as is_rto
  from billing.prices p
  where p.metadata->>'tier' = 'suite' and p.metadata->>'kind' = 'legacy'
),
subs as (
  select s.id as subscription_id, s.user_id, pd.name as product,
         pr.unit_amount, pr.currency, pr.interval, pr.interval_count,
         coalesce(pr.metadata->>'tier',
                  case when pd.metadata->>'daw' = 'logicpro' then 'logic' else 'studio' end) as tier_now,
         case when pd.metadata->>'daw' = 'logicpro' then 'logic' else 'studio' end          as base_tier,
         'web'::text                                    as channel,      -- set 'app' for in-app products
         coalesce(pd.metadata->>'type', '') = 'rto'     as is_rto,
         pd.name ilike '%bundle%'                       as is_bundle,
         pr.product_id in (select id from new_products) as already_migrated
  from billing.subscriptions s
  join billing.prices   pr on pr.id = s.price_id
  join billing.products pd on pd.id = pr.product_id
  where s.status in ('active', 'past_due', 'paused', 'trialing')
),
scored as (
  select sb.*, exists (
    select 1 from upgrade_mirrors m
    where m.currency = sb.currency and m.interval = sb.interval
      and m.interval_count = sb.interval_count and m.unit_amount = sb.unit_amount
      and m.channel = sb.channel and m.is_rto = sb.is_rto
  ) as upgrade_mirror_exists
  from subs sb
)
select subscription_id, user_id, product, unit_amount / 100.0 as amount, currency, interval,
       tier_now, already_migrated, is_bundle, upgrade_mirror_exists,
       case when already_migrated then tier_now
            when base_tier = 'studio' and upgrade_mirror_exists then 'suite' else base_tier end as tier_after_amount,
       case when already_migrated then tier_now
            when base_tier = 'studio' and upgrade_mirror_exists and is_bundle then 'suite' else base_tier end as tier_after_bundle
from scored;

-- Subscriptions per tier, now vs after, under each rule:
--   select 'now' as scenario, tier_now, count(*) from billing.v_migration_projection group by 1,2
--   union all select 'amount', tier_after_amount, count(*) from billing.v_migration_projection group by 1,2
--   union all select 'bundle', tier_after_bundle, count(*) from billing.v_migration_projection group by 1,2
--   order by 1, 3 desc;

-- USERS per tier after migration (one person once, highest tier wins) —
-- this is what the post-run entitlement count must match:
--   with ranked as (
--     select user_id, tier_after_amount as tier,
--            case tier_after_amount when 'suite' then 1 when 'studio' then 2 else 3 end as r
--       from billing.v_migration_projection)
--   select tier, count(distinct user_id) from ranked r1
--    where r = (select min(r) from ranked r2 where r2.user_id = r1.user_id)
--    group by 1 order by 2 desc;
