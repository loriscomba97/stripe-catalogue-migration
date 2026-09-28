#!/usr/bin/env python3
"""
Audit tax_behavior across the whole account before migrating.

tax_behavior is immutable after a price is created, and a mismatch between
a source price and its destination changes what the customer pays even when
the unit amount is identical — the single easiest way to break a
"nobody's price changes" promise. It is set per price, by whoever created
it, so it cannot be inferred from age, product or batch. Read every one.

    python audit_tax_behavior.py

Read-only.
"""

from __future__ import annotations

from collections import Counter, defaultdict

import stripe

from common import init_stripe, load_config

cfg = load_config()


def main() -> int:
    init_stripe()

    settings = stripe.tax.Settings.retrieve()
    default = settings["defaults"]["tax_behavior"]
    print(f"\nAccount default tax_behavior: {default}")
    if default == "inferred_by_currency":
        print("  'unspecified' resolves per currency: typically inclusive for EUR/GBP,"
              " exclusive for USD.")

    prices = {p.id: p for p in stripe.Price.list(limit=100).auto_paging_iter()}
    by_behavior = Counter(p.tax_behavior for p in prices.values())
    print("\nAll prices by tax_behavior:")
    for k, n in by_behavior.most_common():
        print(f"  {n:>5}  {k}")

    # Live subscriptions per price, so the report is weighted by people.
    live = Counter()
    for status in cfg.MIGRATE_STATUSES:
        for sub in stripe.Subscription.list(status=status, limit=100).auto_paging_iter():
            for item in sub["items"]["data"]:
                live[item["price"]["id"]] += 1

    dest_behaviors = Counter(
        p.tax_behavior for p in prices.values() if p.product in cfg.TARGET_PRODUCTS)
    print("\nNew-catalogue prices by tax_behavior:")
    for k, n in dest_behaviors.most_common():
        print(f"  {n:>5}  {k}")

    if len(dest_behaviors) == 1:
        (dest_tb,) = dest_behaviors
        print(f"\nSource prices with live subscriptions whose tax_behavior != {dest_tb}:")
        rows = defaultdict(int)
        for pid, n in live.items():
            p = prices.get(pid)
            if p and p.product not in cfg.TARGET_PRODUCTS and p.tax_behavior != dest_tb:
                rows[(p.tax_behavior, pid, p.unit_amount, p.currency)] += n
        if not rows:
            print("  none — every pair matches.")
        total = 0
        for (tb, pid, amt, cur), n in sorted(rows.items(), key=lambda kv: -kv[1]):
            total += n
            print(f"  {n:>4} subs  {pid}  {amt} {cur.upper()}  {tb}")
        if rows:
            print(f"\n  {total} subscriptions need a destination created with the matching"
                  f" tax_behavior, or a decision to accept the change.")
    else:
        print("\nThe new catalogue itself mixes tax behaviours — fix that first.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
