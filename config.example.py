"""
Copy this file to config.py and fill it in. config.py is gitignored.

Every ID, product, tier name and routing rule lives here. The scripts never
assume anything about your account.
"""

# --------------------------------------------------------------------------
# The NEW catalogue: product id -> tier name
# --------------------------------------------------------------------------
TARGET_PRODUCTS = {
    "prod_REPLACE_ME_1": "studio",
    "prod_REPLACE_ME_2": "suite",
    "prod_REPLACE_ME_3": "logic",
}

# Product lines that are not part of this migration at all.
EXCLUDE_PRODUCTS: set[str] = set()

# Source products sold through an in-app store. Their prices prefer an
# "app"-channel mirror and fall back to "web" at the same amount.
IN_APP_PRODUCTS: set[str] = set()

# Explicit exceptions: source price id -> destination price id.
OVERRIDES: dict[str, str] = {}

# --------------------------------------------------------------------------
# Nickname convention  (see docs/CONVENTIONS.md)
#   {TIER}-{M|Y|ONE}-{CUR}-{WEB|APP|RTO}-{LIST|LEG|WIN}[-{amount}]
# --------------------------------------------------------------------------
TIER_CODES = {"STU": "studio", "SUI": "suite", "LOG": "logic"}

# Extra metadata some tiers carry on every price.
EXTRA_METADATA_BY_TIER = {"logic": {"daw": "logicpro"}}

# Nicknames collapse channel and rent-to-own into one slot, so an RTO price
# has no channel of its own. This is what gets written.
RTO_CHANNEL = "web"

# Prices the nickname cannot describe. Merged on top of the derived values.
METADATA_OVERRIDES: dict[str, dict[str, str]] = {
    # "price_REPLACE": {"kind": "upgrade", "audience": "lifetime", "window": "standard"},
}


# --------------------------------------------------------------------------
# Which destination tier a legacy subscription belongs to
# --------------------------------------------------------------------------
def source_tier(product_metadata: dict, product_name: str) -> str:
    """Derive the destination tier from the SOURCE product. Your legacy
    products presumably encode this somewhere — metadata, a name prefix.
    This example reads a `daw` metadata key."""
    return "logic" if product_metadata.get("daw") == "logicpro" else "studio"


def is_rto(product_metadata: dict, price_metadata: dict) -> bool:
    return price_metadata.get("rto") == "true" or product_metadata.get("type") == "rto"


# --------------------------------------------------------------------------
# Optional: route some subscriptions to a HIGHER tier when a mirror exists
# there at the same amount.
# --------------------------------------------------------------------------
UPGRADE_ROUTING = "none"      # "none" | "amount" | "bundle"
UPGRADE_FROM_TIER = "studio"
UPGRADE_TO_TIER = "suite"


def is_bundle(product_metadata: dict, product_name: str) -> bool:
    return "bundle" in product_name.lower()


# --------------------------------------------------------------------------
# Run parameters
# --------------------------------------------------------------------------
IDEMPOTENCY_PREFIX = "catalogue-migration-REPLACE_ME"   # unique per migration
MIGRATE_STATUSES = ("active", "past_due", "paused")
MIN_HOURS_TO_RENEWAL = 2       # don't race a renewal
WRITE_SLEEP = 0.07             # ~14 writes/sec; Stripe live ceiling is 25

# migrate_one.py — the single subscription to rehearse on
REHEARSAL_SUBSCRIPTION = "sub_REPLACE_ME"


# export_migration_origins.py — mark source products that need a follow-up
# communication (e.g. a product that bundled something the new tier does not)
def flag_origin(product_name: str) -> bool:
    return product_name.startswith("[A]")
