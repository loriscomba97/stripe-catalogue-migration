"""
Shared helpers for every script in scripts/.

Most of this exists because of one fact: recent stripe-python backs
StripeObject with an internal _data store and exposes no dict methods.
`obj.get("x")` resolves as a lookup for a key literally named "get" and
raises AttributeError. Index, don't .get().
"""

from __future__ import annotations

import importlib
import os
import sys

import stripe


class Abort(Exception):
    """Fatal: stop the run rather than leave things half done."""


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

def load_config():
    """Import config.py from the repo root. Fail loudly if it's missing."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        return importlib.import_module("config")
    except ModuleNotFoundError:
        print("config.py not found. Copy config.example.py to config.py and fill it in.",
              file=sys.stderr)
        raise SystemExit(2)


def init_stripe() -> str:
    """Set the API key from the environment. Returns 'LIVE' or 'test'."""
    key = os.environ.get("STRIPE_API_KEY")
    if not key:
        print("STRIPE_API_KEY is not set. Never paste it into a script or a chat —"
              " export it from a file or a keychain lookup.", file=sys.stderr)
        raise SystemExit(2)
    stripe.api_key = key
    mode = "LIVE" if key.startswith("sk_live_") else "test"
    print(f"Stripe mode: {mode}")
    return mode


def confirm(prompt: str) -> bool:
    return input(f"{prompt} Type 'yes': ").strip() == "yes"


# --------------------------------------------------------------------------
# StripeObject access
# --------------------------------------------------------------------------

def field(obj, key, default=None):
    """Safe key lookup on a StripeObject or dict."""
    try:
        return obj[key]
    except (KeyError, TypeError):
        return default


def meta(obj) -> dict:
    """A StripeObject's metadata as a plain {str: str} dict."""
    if obj is None:
        return {}
    for name in ("to_dict_recursive", "to_dict"):
        fn = getattr(obj, name, None)
        if callable(fn):
            return {str(k): str(v) for k, v in dict(fn()).items()}
    return {str(k): str(v) for k, v in dict(obj).items()}


def period_end(sub, item):
    """current_period_end moved from the subscription to the subscription
    item in API version 2025-03-31.basil. Read the item, fall back to the
    subscription, so either version works."""
    return field(item, "current_period_end") or field(sub, "current_period_end")


def discount_id(sub):
    """First discount id, tolerating both the legacy `discount` field and
    the newer `discounts` list."""
    d = getattr(sub, "discount", None)
    if d:
        return d if isinstance(d, str) else field(d, "id")
    ds = getattr(sub, "discounts", None) or []
    if not ds:
        return None
    return ds[0] if isinstance(ds[0], str) else field(ds[0], "id")


# --------------------------------------------------------------------------
# Snapshot — the fields a migration must not change
# --------------------------------------------------------------------------

SNAPSHOT_COLS = [
    "sub_id", "customer", "status", "item_count", "item_id", "price_id",
    "product", "unit_amount", "currency", "interval", "interval_count",
    "tax_behavior", "quantity", "current_period_end", "billing_cycle_anchor",
    "cancel_at_period_end", "trial_end", "discount", "default_payment_method",
]

# Only these may differ between the before and after snapshot.
ALLOWED_DIFF = {"price_id", "product"}


def snapshot(sub) -> dict:
    """Everything about a subscription that a price change must leave alone.
    Expects the subscription retrieved with expand=['items.data.price']."""
    item = sub["items"]["data"][0]
    price = item["price"]
    return {
        "sub_id": sub.id,
        "customer": sub.customer,
        "status": sub.status,
        "item_count": len(sub["items"]["data"]),
        "item_id": item["id"],
        "price_id": price["id"],
        "product": price["product"],
        "unit_amount": price["unit_amount"],
        "currency": price["currency"],
        "interval": price["recurring"]["interval"] if price["recurring"] else None,
        "interval_count": price["recurring"]["interval_count"] if price["recurring"] else None,
        "tax_behavior": price["tax_behavior"],
        "quantity": field(item, "quantity", 1),
        "current_period_end": period_end(sub, item),
        "billing_cycle_anchor": field(sub, "billing_cycle_anchor"),
        "cancel_at_period_end": field(sub, "cancel_at_period_end"),
        "trial_end": field(sub, "trial_end"),
        "discount": discount_id(sub),
        "default_payment_method": sub.default_payment_method,
    }


def _norm(v) -> str:
    """Compare as text. A snapshot read back from CSV holds strings, a fresh
    one holds ints, bools and None; without this every field differs."""
    return "" if v is None else str(v)


def diff(before: dict, after: dict) -> set:
    """Fields that changed and were not allowed to."""
    changed = {k for k in before if _norm(before[k]) != _norm(after.get(k))}
    return changed - ALLOWED_DIFF


def show(label: str, s: dict) -> None:
    print(f"\n{label}")
    for k, v in s.items():
        print(f"  {k:<24} {v}")
