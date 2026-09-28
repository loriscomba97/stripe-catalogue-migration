-- ==========================================================================
-- Entitlement resolver
-- ==========================================================================
--
-- One function answers "what does this user have?" — from PRICE and
-- PRODUCT METADATA, never from product names. Product names are cosmetic
-- and will change again.
--
-- Returned as TEXT, not an enum: an enum needs ALTER TYPE ... ADD VALUE per
-- new state, which cannot run in the same transaction that references the
-- new value, and the file would stop being re-runnable.
--
-- States, highest precedence first:
--   suite_for_life > suite > studio_for_life > studio > legacy > logic > trial > none
--
-- Precedence matters because overlaps are real: a perpetual holder who
-- also carries a live subscription resolves to studio, not legacy. A trial
-- sits below every paid state, so trial-then-buy needs no cleanup.
--
-- Deploy and verify BEFORE migrating anything. It must resolve correctly
-- against the old and the new catalogue at once, because a batch takes
-- time and half the base is on each while it runs.
--
-- Rename `billing` to your schema. Adapt the tier derivation to how your
-- legacy products encode tier (here: a `daw` metadata key).

begin;

drop function if exists billing.my_entitlement();
drop function if exists billing.resolve_entitlement_all();
drop function if exists billing.resolve_entitlement(uuid);
drop function if exists billing.start_trial();
drop function if exists billing.start_trial_for(uuid);
drop function if exists billing.revoke_trial_for(uuid);
drop function if exists billing.trial_length();

create function billing.trial_length()
returns interval language sql immutable
as $$ select interval '7 days' $$;

-- --------------------------------------------------------------------------

create function billing.resolve_entitlement(p_user_id uuid)
returns table (
  entitlement   text,
  tier          text,          -- studio | suite | logic | null
  is_permanent  boolean,
  build         text,          -- which application build to serve
  source_type   text,          -- subscription | lifetime | perpetual | trial
  source_id     text,
  price_id      text,
  expires_at    timestamptz,   -- null when permanent; for perpetuals: updates until
  in_rto_term   boolean
)
language sql stable security definer
set search_path = billing, public
as $$
with live_subs as (
  select
    s.id, s.price_id, s.current_period_end,
    coalesce(pr.metadata->>'tier',
             case when pd.metadata->>'daw' = 'logicpro' then 'logic' else 'studio' end) as tier,
    coalesce(pr.metadata->>'rto' = 'true', pd.metadata->>'type' = 'rto', false) as is_rto
  from billing.subscriptions s
  join billing.prices   pr on pr.id = s.price_id
  join billing.products pd on pd.id = pr.product_id
  where s.user_id = p_user_id
    and s.status in ('active', 'past_due', 'paused', 'trialing')
),
lifes as (
  select
    l.id, l.price_id,
    coalesce(pr.metadata->>'tier',
             case when pd.metadata->>'daw' = 'logicpro' then 'logic' else 'studio' end) as tier,
    pr.metadata->>'kind'   as price_kind,
    pr.metadata->>'grants' as grants,       -- explicit override, wins when present
    pd.metadata->>'type'   as product_type
  from billing.lifetimes l
  join billing.prices   pr on pr.id = l.price_id
  join billing.products pd on pd.id = pr.product_id
  where l.user_id = p_user_id and l.revoked_at is null
),
perps as (
  select p.id, p.price_id, p.end_date::timestamptz as updates_until
  from billing.perpetuals p
  where p.user_id = p_user_id and p.revoked_at is null
),
live_trials as (
  select t.id, t.ends_at
  from billing.trials t
  where t.user_id = p_user_id and t.revoked_at is null and t.ends_at > now()
),
candidates as (
  select 1 as rank, 'suite_for_life'::text as ent, 'suite'::text as tier, true as perm,
         'v2'::text as build, 'lifetime'::text as src, id::text as sid, price_id::text as pid,
         null::timestamptz as exp, false as rto
  from lifes where grants = 'suite_for_life'
  union all
  select 1, 'suite_for_life', 'suite', true, 'v2', 'lifetime', id, price_id, null, false
  from lifes where grants is null and price_kind = 'upgrade'
  union all
  select 2, 'suite', 'suite', false, 'v2', 'subscription', id, price_id, current_period_end, is_rto
  from live_subs where tier = 'suite'
  union all
  select 3, 'studio_for_life', 'studio', true, 'v2', 'lifetime', id, price_id, null, false
  from lifes where grants = 'studio_for_life'
  union all
  -- a row in lifetimes IS a one-time purchase; missing metadata means lifetime
  select 3, 'studio_for_life', 'studio', true, 'v2', 'lifetime', id, price_id, null, false
  from lifes where grants is null and coalesce(product_type, 'lifetime') = 'lifetime' and tier = 'studio'
  union all
  select 4, 'studio', 'studio', false, 'v2', 'subscription', id, price_id, current_period_end, is_rto
  from live_subs where tier = 'studio'
  union all
  select 5, 'legacy', null, true, 'legacy', 'lifetime', id, price_id, null, false
  from lifes where product_type = 'perpetual'
  union all
  select 5, 'legacy', null, true, 'legacy', 'perpetual', id, price_id, updates_until, false
  from perps
  union all
  select 6, 'logic', 'logic', false, 'logic', 'subscription', id, price_id, current_period_end, is_rto
  from live_subs where tier = 'logic'
  union all
  select 6, 'logic', 'logic', true, 'logic', 'lifetime', id, price_id, null, false
  from lifes where tier = 'logic' and grants is null
  union all
  select 7, 'trial', 'suite', false, 'v2', 'trial', id, null, ends_at, false
  from live_trials
  union all
  -- always exactly one row; the app never handles an empty result
  select 99, 'none', null, false, null, null, null, null, null, false
)
select ent, tier, perm, build, src, sid, pid, exp, rto
from candidates
order by rank, exp desc nulls first
limit 1;
$$;

-- What the app calls. SECURITY DEFINER so it can reach the resolver, safe
-- because it takes no arguments and pins to auth.uid().
create function billing.my_entitlement()
returns table (entitlement text, tier text, is_permanent boolean, build text,
               source_type text, source_id text, price_id text,
               expires_at timestamptz, in_rto_term boolean)
language sql stable security definer set search_path = billing, public
as $$ select * from billing.resolve_entitlement(auth.uid()) $$;

-- Every user who SHOULD hold an entitlement right now — the acceptance test.
create function billing.resolve_entitlement_all()
returns table (user_id uuid, entitlement text, tier text, is_permanent boolean, build text,
               source_type text, source_id text, price_id text,
               expires_at timestamptz, in_rto_term boolean)
language sql stable security definer set search_path = billing, public
as $$
  select u.user_id, r.*
  from (
    select user_id from billing.subscriptions where status in ('active','past_due','paused','trialing')
    union select user_id from billing.lifetimes  where revoked_at is null
    union select user_id from billing.perpetuals where revoked_at is null
    union select user_id from billing.trials     where revoked_at is null and ends_at > now()
  ) u
  cross join lateral billing.resolve_entitlement(u.user_id) r;
$$;

-- --------------------------------------------------------------------------
-- Trials
-- --------------------------------------------------------------------------

create function billing.start_trial_for(p_user_id uuid)
returns table (status text, trial_id text, ends_at timestamptz)
language plpgsql security definer set search_path = billing, public
as $$
declare v_id text; v_ends timestamptz; v_prior billing.trials%rowtype; v_ent text;
begin
  if p_user_id is null then
    return query select 'no_user'::text, null::text, null::timestamptz; return;
  end if;
  select * into v_prior from billing.trials where user_id = p_user_id and revoked_at is null limit 1;
  if found then
    return query select 'already_used'::text, v_prior.id, v_prior.ends_at; return;
  end if;
  select e.entitlement into v_ent from billing.resolve_entitlement(p_user_id) e;
  if v_ent <> 'none' then
    return query select 'already_entitled'::text, null::text, null::timestamptz; return;
  end if;
  v_id := gen_random_uuid()::text; v_ends := now() + billing.trial_length();
  begin
    insert into billing.trials (id, user_id, ends_at) values (v_id, p_user_id, v_ends);
  exception when unique_violation then
    select * into v_prior from billing.trials where user_id = p_user_id and revoked_at is null limit 1;
    return query select 'already_used'::text, v_prior.id, v_prior.ends_at; return;
  end;
  return query select 'started'::text, v_id, v_ends;
end $$;

create function billing.start_trial()
returns table (status text, trial_id text, ends_at timestamptz)
language sql security definer set search_path = billing, public
as $$ select * from billing.start_trial_for(auth.uid()) $$;

create function billing.revoke_trial_for(p_user_id uuid)
returns table (status text, trial_id text)
language plpgsql security definer set search_path = billing, public
as $$
declare v_id text;
begin
  update billing.trials set revoked_at = now()
   where user_id = p_user_id and revoked_at is null returning id into v_id;
  return query select case when v_id is null then 'no_active_trial' else 'revoked' end::text, v_id;
end $$;

-- --------------------------------------------------------------------------
-- Permissions: functions taking a user_id run as definer — service role only
-- --------------------------------------------------------------------------

revoke all on function billing.resolve_entitlement(uuid), billing.resolve_entitlement_all(),
              billing.start_trial_for(uuid), billing.revoke_trial_for(uuid)
  from public, anon, authenticated;
grant execute on function billing.resolve_entitlement(uuid), billing.resolve_entitlement_all(),
                          billing.start_trial_for(uuid), billing.revoke_trial_for(uuid)
  to service_role;
grant execute on function billing.my_entitlement(), billing.start_trial(), billing.trial_length()
  to authenticated, service_role;

commit;

-- --------------------------------------------------------------------------
-- Acceptance test
-- --------------------------------------------------------------------------
-- Zero rows. Each row is a real user who would be locked out.
--   select * from billing.resolve_entitlement_all() where entitlement = 'none';
--
-- Save this before migrating; compare after. Group on entitlement, not tier.
--   select entitlement, count(*) from billing.resolve_entitlement_all() group by 1 order by 2 desc;
