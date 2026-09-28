#!/usr/bin/env python3
"""
Migrate every live subscription onto the new catalogue.

Rebuilds the source -> destination map from the Stripe API on every run,
because counts drift between the day you plan and the day you run.

Anything that cannot be moved safely is SKIPPED with a named reason, never
guessed at. That is what separates the clean majority from the handful
that need a human decision first.

    python migrate_all.py                       # dry run  -> out/plan.csv
    python migrate_all.py --limit 10 --apply    # first ten, soonest renewals
    python migrate_all.py --apply               # the rest  -> out/results.csv
    python migrate_all.py --rollback out/results.csv --apply

BEFORE --apply: pause every automation that reacts to
customer.subscription.updated. This emits one event per subscription in a
few minutes, and "your plan has changed" emails during a migration that
promises zero change is the worst available outcome.
"""

from __future__ import annotations

import argparse
import csv
import os
import time
from datetime import datetime, timezone

import stripe

from common import (SNAPSHOT_COLS, Abort, confirm, diff, init_stripe,
                    load_config, meta, snapshot)

cfg = load_config()
OUT = "out"


# --------------------------------------------------------------------------
# Catalogue
# --------------------------------------------------------------------------

def load_destinations():
    """Index the new catalogue by the attributes that identify a mirror:
    tier, channel, rto, currency, interval, interval_count, amount.

    Amount alone is ambiguous — the same amount can exist under two tiers,
    or as both a plain and a rent-to-own mirror. The full key is what makes
    each destination unique.
    """
    index, prices = {}, {}
    for product_id in cfg.TARGET_PRODUCTS:
        for p in stripe.Price.list(product=product_id, limit=100).auto_paging_iter():
            prices[p.id] = p
            if not p.recurring or not p.active:
                continue
            m = meta(p.metadata)
            key = (m.get("tier"), m.get("channel", "web"), m.get("rto") == "true",
                   p.currency, p.recurring["interval"], p.recurring["interval_count"],
                   p.unit_amount)
            index.setdefault(key, []).append(p)
    if not index:
        raise Abort("No destination prices found. Wrong account, mode, or TARGET_PRODUCTS?")
    return index, prices


def load_source_products():
    out = {}
    for p in stripe.Product.list(limit=100).auto_paging_iter():
        m = meta(p.metadata)
        m["_name"] = p.name or ""
        out[p.id] = m
    return out


def destination_for(src_price, src_product_id, products, index, prices):
    """Resolve the mirror, or (None, reason)."""
    if src_price.id in cfg.OVERRIDES:
        d = prices.get(cfg.OVERRIDES[src_price.id])
        return (d, None) if d else (None, "override_price_missing")

    pm = products.get(src_product_id, {})
    name = pm.get("_name", "")
    tier = cfg.source_tier(pm, name)
    channel = "app" if src_product_id in cfg.IN_APP_PRODUCTS else "web"
    rto = cfg.is_rto(pm, meta(src_price.metadata))
    # interval_count is part of the contract: a quarterly price must never
    # land on a monthly mirror at the same amount.
    shape = (channel, rto, src_price.currency, src_price.recurring["interval"],
             src_price.recurring["interval_count"], src_price.unit_amount)

    def lookup(t):
        hits = index.get((t,) + shape, [])
        if not hits and channel == "app":          # amount is the contract; channel is a label
            hits = index.get((t, "web") + shape[1:], [])
        return hits

    if tier == cfg.UPGRADE_FROM_TIER and cfg.UPGRADE_ROUTING != "none" and lookup(cfg.UPGRADE_TO_TIER):
        if cfg.UPGRADE_ROUTING == "amount" or (cfg.UPGRADE_ROUTING == "bundle" and cfg.is_bundle(pm, name)):
            tier = cfg.UPGRADE_TO_TIER

    hits = lookup(tier)
    if not hits:
        return None, f"no_mirror[{tier}/{channel}/{'rto' if rto else 'std'}]"
    if len(hits) > 1:
        return None, "ambiguous_" + ",".join(h.id for h in hits)
    return hits[0], None


# --------------------------------------------------------------------------
# Assessment
# --------------------------------------------------------------------------

def assess(sub, products, index, prices) -> dict:
    items = sub["items"]["data"]
    if len(items) != 1:
        return {"sub_id": sub.id, "decision": "skip", "reason": f"multi_item[{len(items)}]"}

    s = snapshot(sub)
    if s["product"] in cfg.TARGET_PRODUCTS:
        return {**s, "decision": "skip", "reason": "already_migrated"}
    if s["product"] in cfg.EXCLUDE_PRODUCTS:
        return {**s, "decision": "skip", "reason": "excluded_product"}
    if not s["interval"]:
        return {**s, "decision": "skip", "reason": "not_recurring"}
    if getattr(sub, "schedule", None):
        return {**s, "decision": "skip", "reason": "has_schedule"}
    if getattr(sub, "application", None):
        return {**s, "decision": "skip", "reason": "connect_application"}

    dest, why = destination_for(items[0]["price"], s["product"], products, index, prices)
    if dest is None:
        return {**s, "decision": "skip", "reason": why}
    if dest.tax_behavior != s["tax_behavior"]:
        return {**s, "decision": "skip", "dest_price_id": dest.id,
                "reason": f"tax_mismatch[{s['tax_behavior']}->{dest.tax_behavior}]"}
    if not meta(dest.metadata).get("tier"):
        return {**s, "decision": "skip", "dest_price_id": dest.id, "reason": "dest_missing_tier"}
    if s["current_period_end"] and s["current_period_end"] - int(time.time()) < cfg.MIN_HOURS_TO_RENEWAL * 3600:
        return {**s, "decision": "skip", "dest_price_id": dest.id, "reason": "renews_imminently"}

    return {**s, "decision": "migrate", "reason": "",
            "dest_price_id": dest.id, "dest_nickname": dest.nickname or "",
            "dest_tier": cfg.TARGET_PRODUCTS[dest.product]}


def build_plan(products, index, prices):
    rows = []
    for status in cfg.MIGRATE_STATUSES:
        for sub in stripe.Subscription.list(status=status, limit=100,
                                            expand=["data.items.data.price"]).auto_paging_iter():
            rows.append(assess(sub, products, index, prices))
    rows.sort(key=lambda r: r.get("current_period_end") or 0)   # soonest renewal first
    return rows


def write_csv(path, rows, cols):
    os.makedirs(OUT, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {path}")


def report(rows):
    todo = [r for r in rows if r["decision"] == "migrate"]
    skip = [r for r in rows if r["decision"] != "migrate"]
    print(f"\n{len(rows)} subscriptions: {len(todo)} to migrate, {len(skip)} skipped")
    print(f"Upgrade routing: {cfg.UPGRADE_ROUTING}\n")
    if todo:
        by_tier = {}
        for r in todo:
            by_tier[r["dest_tier"]] = by_tier.get(r["dest_tier"], 0) + 1
        print("DESTINATION TIER")
        for t, n in sorted(by_tier.items(), key=lambda kv: -kv[1]):
            print(f"  {n:>4}  {t}")
        print()
    if skip:
        counts = {}
        for r in skip:
            counts[r["reason"]] = counts.get(r["reason"], 0) + 1
        print("SKIPPED")
        for reason, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"  {n:>4}  {reason}")
    return todo


# --------------------------------------------------------------------------
# Apply
# --------------------------------------------------------------------------

def modify(sub_id, item_id, price, quantity, key_suffix=""):
    for attempt in range(5):
        try:
            return stripe.Subscription.modify(
                sub_id,
                # Nothing else is passed. No billing_cycle_anchor (resets the
                # renewal and invoices now), no discounts (overwrites), no
                # trial_end (preserved). Quantity is echoed because a price
                # change otherwise resets it to 1.
                items=[{"id": item_id, "price": price, "quantity": quantity}],
                proration_behavior="none",
                idempotency_key=f"{cfg.IDEMPOTENCY_PREFIX}{key_suffix}-{sub_id}",
            )
        except stripe.error.RateLimitError:
            time.sleep(2 ** attempt)
        except stripe.error.StripeError as e:
            return e
    return Abort(f"{sub_id}: rate limited after 5 attempts")


def apply(todo, out_path, key_suffix=""):
    results, failures = [], 0
    for i, row in enumerate(todo, 1):
        before = {k: row[k] for k in SNAPSHOT_COLS}
        res = modify(row["sub_id"], row["item_id"], row["dest_price_id"], row["quantity"], key_suffix)
        if isinstance(res, Exception):
            failures += 1
            print(f"[{i}/{len(todo)}] {row['sub_id']}  ERROR {res}")
            results.append({**{f"before_{k}": v for k, v in before.items()}, "result": f"error: {res}"})
            continue

        after = snapshot(stripe.Subscription.retrieve(row["sub_id"], expand=["items.data.price"]))
        bad = diff(before, after)
        results.append({**{f"before_{k}": v for k, v in before.items()},
                        **{f"after_{k}": v for k, v in after.items()},
                        "result": "ok" if not bad else "FAILED:" + ",".join(sorted(bad))})
        if bad:
            failures += 1
            print(f"[{i}/{len(todo)}] {row['sub_id']}  FAILED  unexpected change: {sorted(bad)}")
            print("  Stopping. Something other than the price moved.")
            break
        print(f"[{i}/{len(todo)}] {row['sub_id']}  ok  -> {row['dest_price_id']}")
        time.sleep(cfg.WRITE_SLEEP)

    cols = [f"before_{k}" for k in SNAPSHOT_COLS] + [f"after_{k}" for k in SNAPSHOT_COLS] + ["result"]
    write_csv(out_path, results, cols)
    print(f"\n{len(results)} processed, {failures} failed.")
    return failures


def rollback(results_csv):
    """Reverse a previous run from its results file. A DIFFERENT idempotency
    namespace is essential: reusing the migration key returns Stripe's cached
    response and silently does nothing."""
    with open(results_csv, newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r["result"] == "ok"]
    print(f"Rolling back {len(rows)} subscriptions.")
    todo = [{**{k: r[f"before_{k}"] for k in SNAPSHOT_COLS},
             "dest_price_id": r["before_price_id"],
             "quantity": int(r["before_quantity"] or 1)} for r in rows]
    return apply(todo, os.path.join(OUT, "rollback_results.csv"), key_suffix="-rollback")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--rollback", metavar="RESULTS_CSV")
    args = ap.parse_args()

    mode = init_stripe()
    print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")
    try:
        if args.rollback:
            if not args.apply or not confirm(f"\nRoll back from {args.rollback} in {mode}?"):
                return 1
            return 1 if rollback(args.rollback) else 0

        print("\nRebuilding the map from the Stripe API...")
        index, prices = load_destinations()
        products = load_source_products()
        rows = build_plan(products, index, prices)
        write_csv(os.path.join(OUT, "plan.csv"), rows,
                  SNAPSHOT_COLS + ["decision", "reason", "dest_price_id", "dest_nickname", "dest_tier"])
        todo = report(rows)
        if args.limit:
            todo = todo[:args.limit]
            print(f"\n--limit: {len(todo)} in this batch (soonest renewals first).")
        if not args.apply:
            print("\nDry run. Nothing written. Review out/plan.csv, then --apply.")
            return 0
        if not todo:
            return 0
        print("\nPause every automation on customer.subscription.updated first.")
        if not confirm(f"Migrate {len(todo)} subscriptions in {mode} Stripe?"):
            return 1
        print()
        return 1 if apply(todo, os.path.join(OUT, "results.csv")) else 0
    except Abort as e:
        print(f"\nABORTED: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
