#!/usr/bin/env python3
"""
Migrate ONE subscription — the one in config.REHEARSAL_SUBSCRIPTION — as a
live rehearsal. Same call, same guards, same before/after diff as the batch.

    python migrate_one.py            # dry run
    python migrate_one.py --apply    # migrate, then verify
"""

from __future__ import annotations

import argparse

import stripe

from common import (Abort, confirm, diff, init_stripe, load_config, meta, show,
                    snapshot)
from migrate_all import destination_for, load_destinations, load_source_products

cfg = load_config()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    mode = init_stripe()
    sub_id = cfg.REHEARSAL_SUBSCRIPTION

    try:
        sub = stripe.Subscription.retrieve(sub_id, expand=["items.data.price"])
        before = snapshot(sub)
        show("BEFORE", before)

        if before["item_count"] != 1:
            raise Abort(f"{before['item_count']} items on this subscription.")

        index, prices = load_destinations()
        products = load_source_products()
        dest, why = destination_for(sub["items"]["data"][0]["price"], before["product"],
                                    products, index, prices)
        if dest is None:
            raise Abort(f"no destination: {why}")

        print(f"\nDESTINATION  {dest.id}  {dest.nickname}")
        print(f"  tier      {cfg.TARGET_PRODUCTS[dest.product]}")
        print(f"  metadata  {meta(dest.metadata) or '(empty)'}")

        if dest.tax_behavior != before["tax_behavior"]:
            raise Abort(f"tax_behavior {before['tax_behavior']} -> {dest.tax_behavior}.")
        if not meta(dest.metadata).get("tier"):
            raise Abort("destination has no tier metadata.")
        print("\n  tax_behavior matches, tier metadata present.")

        if not args.apply:
            print("\nDry run. Nothing written.")
            return 0
        if not confirm(f"\nMigrate {sub_id} in {mode} Stripe?"):
            return 1

        stripe.Subscription.modify(
            sub_id,
            items=[{"id": before["item_id"], "price": dest.id, "quantity": before["quantity"]}],
            proration_behavior="none",
            idempotency_key=f"{cfg.IDEMPOTENCY_PREFIX}-rehearsal-{sub_id}",
        )
        after = snapshot(stripe.Subscription.retrieve(sub_id, expand=["items.data.price"]))
        show("AFTER", after)

        bad = diff(before, after)
        print("\nDIFF")
        for k in before:
            if before[k] != after[k]:
                print(f"  {'FAILED  ' if k in bad else 'changed '} {k}: {before[k]} -> {after[k]}")
        print("\nFAILED — something other than the price moved." if bad
              else "\nPassed. Only price and product changed.")
        return 1 if bad else 0
    except Abort as e:
        print(f"\nABORT: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
