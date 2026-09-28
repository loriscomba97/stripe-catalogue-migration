#!/usr/bin/env python3
"""
Recover the origin of every migrated subscription from Stripe events.

After the migration your database only holds the NEW price; the original
product is gone. Stripe events keep it: every customer.subscription.updated
written by the migration carries the previous price in previous_attributes,
and is identifiable by its idempotency key.

    python export_migration_origins.py --since YYYY-MM-DD [--no-email]

    -> out/migration_origins.csv
    -> out/migration_origins.sql   (create table + inserts, for your DB)

Read-only. --no-email omits customer emails from both outputs.
"""

from __future__ import annotations

import argparse
import csv
import os
from datetime import datetime, timezone

import stripe

from common import field, init_stripe, load_config

cfg = load_config()
OUT = "out"

_prices, _customers = {}, {}


def price_info(pid):
    if pid not in _prices:
        p = stripe.Price.retrieve(pid, expand=["product"])
        prod = p.product
        _prices[pid] = {"product_id": prod if isinstance(prod, str) else prod.id,
                        "product_name": "" if isinstance(prod, str) else (prod.name or ""),
                        "nickname": p.nickname or ""}
    return _prices[pid]


def email_of(cid):
    if cid not in _customers:
        try:
            _customers[cid] = getattr(stripe.Customer.retrieve(cid), "email", None) or ""
        except stripe.error.StripeError:
            _customers[cid] = ""
    return _customers[cid]


def previous_price_id(prev):
    items = field(prev, "items")
    if items:
        data = field(items, "data") or []
        if data:
            pr = field(data[0], "price")
            if pr:
                return field(pr, "id")
    plan = field(prev, "plan")
    return field(plan, "id") if plan else None


def sql(v):
    if v is None or v == "":
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    return "'" + str(v).replace("'", "''") + "'"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True, metavar="YYYY-MM-DD",
                    help="first day of the migration (UTC)")
    ap.add_argument("--no-email", action="store_true")
    args = ap.parse_args()
    init_stripe()

    since = int(datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc).timestamp())
    prefix, rollback = cfg.IDEMPOTENCY_PREFIX, cfg.IDEMPOTENCY_PREFIX + "-rollback"
    rows, seen, no_prev = [], set(), 0

    for ev in stripe.Event.list(type="customer.subscription.updated",
                                created={"gte": since}, limit=100).auto_paging_iter():
        req = field(ev, "request")
        ikey = str(field(req, "idempotency_key") or "") if req else ""
        if not ikey.startswith(prefix) or ikey.startswith(rollback):
            continue
        sub = ev.data.object
        if sub.id in seen:
            continue
        old = previous_price_id(field(ev.data, "previous_attributes") or {})
        if not old:
            no_prev += 1
            continue
        new = sub["items"]["data"][0]["price"]["id"]
        o, n = price_info(old), price_info(new)
        rows.append({
            "subscription_id": sub.id,
            "customer_id": sub.customer,
            "email": "" if args.no_email else email_of(sub.customer),
            "old_price_id": old, "old_product_id": o["product_id"],
            "old_product_name": o["product_name"],
            "new_price_id": new, "new_nickname": n["nickname"],
            "flagged": cfg.flag_origin(o["product_name"]),
            "migrated_at": datetime.fromtimestamp(ev.created, timezone.utc).isoformat(),
        })
        seen.add(sub.id)

    os.makedirs(OUT, exist_ok=True)
    cols = list(rows[0].keys()) if rows else []
    with open(os.path.join(OUT, "migration_origins.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(OUT, "migration_origins.sql"), "w", encoding="utf-8") as fh:
        fh.write("create table if not exists billing.migration_origins (\n"
                 "  subscription_id text primary key, customer_id text, email text,\n"
                 "  old_price_id text, old_product_id text, old_product_name text,\n"
                 "  new_price_id text, new_nickname text, flagged boolean,\n"
                 "  migrated_at timestamptz\n);\n\n")
        if rows:
            fh.write(f"insert into billing.migration_origins ({', '.join(cols)}) values\n")
            fh.write(",\n".join("(" + ", ".join(sql(r[c]) for c in cols) + ")" for r in rows))
            fh.write("\non conflict (subscription_id) do nothing;\n")

    flagged = [r for r in rows if r["flagged"]]
    print(f"\n{len(rows)} migrated subscriptions found, {len(flagged)} flagged.")
    if no_prev:
        print(f"WARNING: {no_prev} migration events had no readable previous price.")
    print("Wrote out/migration_origins.csv and out/migration_origins.sql")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
