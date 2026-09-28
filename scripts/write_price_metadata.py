#!/usr/bin/env python3
"""
Write price metadata for the new catalogue, derived from each price's
nickname.

    {TIER}-{M|Y|ONE}-{CUR}-{WEB|APP|RTO}-{LIST|LEG|WIN}[-{amount}]

Every derived value is cross-checked against the real Price object before
anything is written — currency, interval and unit amount must agree with
the nickname. A single mismatch aborts the whole run: a price left without
metadata resolves to no entitlement, and that failure is silent.

    python write_price_metadata.py                 # dry run
    python write_price_metadata.py --csv plan.csv
    python write_price_metadata.py --only price_a,price_b --apply
    python write_price_metadata.py --apply         # everything

Dry run is the default.
"""

from __future__ import annotations

import argparse
import csv
import re
import time
from dataclasses import dataclass, field as dc_field

import stripe

from common import Abort, confirm, init_stripe, load_config, meta

cfg = load_config()

NICKNAME_RE = re.compile(
    r"^(" + "|".join(map(re.escape, cfg.TIER_CODES)) + r")"
    r"-(M|Y|ONE)-([A-Z]{3})-(WEB|APP|RTO)-(LIST|LEG|WIN)(?:-(\d+))?$"
)
KIND = {"LIST": "list", "LEG": "legacy", "WIN": "upgrade"}
INTERVAL = {"M": "month", "Y": "year"}


@dataclass
class Plan:
    price_id: str
    product: str
    nickname: str | None
    amount: int
    currency: str
    current: dict
    target: dict
    notes: list = dc_field(default_factory=list)

    @property
    def unchanged(self):
        return self.current == self.target

    @property
    def stale_keys(self):
        return sorted(k for k in self.current if k not in self.target)


def derive(price) -> dict:
    override = cfg.METADATA_OVERRIDES.get(price.id, {})
    nickname = (price.nickname or "").strip()

    if not nickname:
        if not override:
            raise Abort(f"{price.id}: no nickname and no override.")
        out = dict(override)
    else:
        m = NICKNAME_RE.match(nickname)
        if not m:
            raise Abort(f"{price.id}: nickname {nickname!r} does not match the convention.")
        tier_t, period_t, cur_t, surface_t, kind_t, amount_t = m.groups()

        # A nickname is a display string; it can lie. The Price object can't.
        if cur_t.lower() != price.currency:
            raise Abort(f"{price.id}: nickname says {cur_t}, price is {price.currency.upper()}.")
        if period_t == "ONE":
            if price.recurring is not None:
                raise Abort(f"{price.id}: nickname says one-time, price is recurring.")
        else:
            if price.recurring is None:
                raise Abort(f"{price.id}: nickname says recurring, price is one-time.")
            if (price.recurring["interval"] != INTERVAL[period_t]
                    or price.recurring["interval_count"] != 1):
                raise Abort(f"{price.id}: nickname says {INTERVAL[period_t]}, price is "
                            f"{price.recurring['interval_count']}x {price.recurring['interval']}.")
        if amount_t is not None and int(amount_t) != price.unit_amount:
            raise Abort(f"{price.id}: nickname says {amount_t}, price is {price.unit_amount}.")

        tier = cfg.TIER_CODES[tier_t]
        out = {
            "tier": tier,
            "kind": KIND[kind_t],
            "channel": cfg.RTO_CHANNEL if surface_t == "RTO" else surface_t.lower(),
            "rto": "true" if surface_t == "RTO" else "false",
        }
        out.update(cfg.EXTRA_METADATA_BY_TIER.get(tier, {}))
        out.update(override)

    if "visibility" not in override:
        out["visibility"] = "hidden" if out.get("kind") == "legacy" else "public"
    return sanitise(price.id, out)


def sanitise(price_id: str, m: dict) -> dict:
    """Reject whitespace and empties. A trailing newline in a metadata value
    silently defeats every string comparison downstream."""
    clean = {}
    for k, v in m.items():
        if not isinstance(v, str):
            raise Abort(f"{price_id}: {k!r} is {type(v).__name__}, not str.")
        if not k.strip() or not v.strip() or k != k.strip() or v != v.strip():
            raise Abort(f"{price_id}: empty or whitespace-padded metadata ({k!r}: {v!r}).")
        clean[k] = v
    return clean


def build_plan(only: set | None) -> list:
    plans, seen = [], set()
    for product_id in cfg.TARGET_PRODUCTS:
        for price in stripe.Price.list(product=product_id, limit=100).auto_paging_iter():
            seen.add(price.id)
            if only is not None and price.id not in only:
                continue
            p = Plan(price.id, cfg.TARGET_PRODUCTS[product_id], price.nickname,
                     price.unit_amount, price.currency.upper(),
                     meta(price.metadata), derive(price))
            if p.current and not p.unchanged:
                p.notes.append("existing metadata will be replaced")
            if p.stale_keys:
                p.notes.append("clearing " + ", ".join(p.stale_keys))
            plans.append(p)
    if only is not None and (only - seen):
        raise Abort("--only names prices not in the target products: " + ", ".join(sorted(only - seen)))
    if not plans:
        raise Abort("No prices found. Check TARGET_PRODUCTS and the API key's mode.")
    return plans


def report(plans):
    fmt = "{:<32} {:<10} {:<30} {}"
    print(fmt.format("PRICE", "TIER", "NICKNAME", "METADATA"))
    print("-" * 130)
    for p in plans:
        rendered = " ".join(f"{k}={v}" for k, v in sorted(p.target.items()))
        print(fmt.format(p.price_id, p.product, (p.nickname or "(none)")[:30],
                         rendered + ("  [ok]" if p.unchanged else "")))
        for n in p.notes:
            print(f"{'':<32} {'':<10} {'':<30} ! {n}")
    todo = [p for p in plans if not p.unchanged]
    print("-" * 130)
    print(f"{len(plans)} prices, {len(todo)} to write, {len(plans) - len(todo)} already correct.")
    return todo


def write_csv(plans, path):
    keys = sorted({k for p in plans for k in p.target})
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["price_id", "tier", "nickname", "amount", "currency", *keys])
        for p in plans:
            w.writerow([p.price_id, p.product, p.nickname or "", p.amount, p.currency,
                        *[p.target.get(k, "") for k in keys]])
    print(f"plan written to {path}")


def apply(todo):
    for i, p in enumerate(todo, 1):
        payload = dict(p.target)
        for k in p.stale_keys:
            payload[k] = ""          # Stripe merges metadata; "" deletes a key
        for attempt in range(5):
            try:
                stripe.Price.modify(p.price_id, metadata=payload,
                                    idempotency_key=f"price-meta-v1-{p.price_id}")
                print(f"[{i}/{len(todo)}] {p.price_id} written")
                break
            except stripe.error.RateLimitError:
                time.sleep(2 ** attempt)
        else:
            raise Abort(f"{p.price_id}: rate limited after 5 attempts.")
        time.sleep(0.05)


def verify(plans, partial: bool) -> bool:
    expected = {p.price_id: p.target for p in plans}
    ok = True
    for product_id in cfg.TARGET_PRODUCTS:
        for price in stripe.Price.list(product=product_id, limit=100).auto_paging_iter():
            want = expected.get(price.id)
            if want is None:
                if not partial:
                    print(f"VERIFY: {price.id} appeared since the plan was built")
                    ok = False
            elif meta(price.metadata) != want:
                print(f"VERIFY: {price.id} mismatch\n  want {want}\n  got  {meta(price.metadata)}")
                ok = False
    print("Verification passed." if ok else "VERIFICATION FAILED.")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--csv", metavar="PATH")
    ap.add_argument("--only", metavar="IDS", help="comma-separated price ids")
    args = ap.parse_args()
    only = {s.strip() for s in args.only.split(",") if s.strip()} if args.only else None

    mode = init_stripe()
    try:
        plans = build_plan(only)
        todo = report(plans)
        if args.csv:
            write_csv(plans, args.csv)
        if not args.apply:
            print("\nDry run. Nothing written.")
            return 0
        if not todo:
            return 0
        if mode == "LIVE" and not confirm(f"\nWrite {len(todo)} prices in LIVE Stripe?"):
            return 1
        print()
        apply(todo)
        print()
        return 0 if verify(plans, partial=bool(only)) else 1
    except Abort as e:
        print(f"\nABORTED: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
