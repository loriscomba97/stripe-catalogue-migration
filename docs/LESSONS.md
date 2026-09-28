# What bit us

Everything here cost real time. In rough order of how expensive it was.

**`tax_behavior` is immutable and set per price, by hand.** It cannot be
inferred from age, product or batch. A source at `exclusive` moved to a
destination at `unspecified` changes what the customer pays at an identical
unit amount, and no amount-based check will catch it. Audit every pair.

**Stripe events are the only place the original product survives.** After
migration your database holds the new price. `results.csv` can be
overwritten by the next run. `previous_attributes` on the
`customer.subscription.updated` event has the old price, and the event's
`request.idempotency_key` tells you which writes were yours.

**Idempotency keys cache the response.** A rollback that reuses the
migration key returns the original result and writes nothing. Different
operation, different namespace.

**`current_period_end` moved to the subscription item** in API version
`2025-03-31.basil`. Reading it off the subscription on a recent version
returns nothing, writes null, and silently breaks both migration ordering
and the post-run diff. Read the item, fall back to the subscription.

**Recent stripe-python has no dict methods on StripeObject.** `.get()` and
`.items()` resolve as key lookups and raise. Index, and use `to_dict()`.

**Your sync can swallow foreign-key violations.** A new product that
doesn't sync means every price under it fails to insert: eight times, in
our case, with nothing logged. The resolver then can't see `tier` for any
of them.

**Three Stripe customers, one email.** Checkout with `customer_email`
instead of `customer` creates a new customer every time. Stripe has no
merge. The fix is a canonical mapping in your own database and a unique
constraint, not anything in Stripe.

**A user id stored in customer metadata was stale.** Verify a stored user id
still exists before trusting it; fall back to a normalised email.

**A `date` column made trial length depend on signup hour.** 00:01 got
nearly seven days, 23:00 got just over six. `timestamptz`, always.

**Coupon name prefixes enforce nothing.** `[productA] CODE` applies to
product B if someone enters it. Real scoping is `applies_to.products`.

**Hand-typed metadata: three errors in ten values.** One was a trailing
newline that defeated every string comparison for weeks. Generate metadata
from a convention and verify by re-reading.

**The website was still selling from a query with no ORDER BY.** Adding a
metadata key to new products pulled their prices into a live price map that
took "whatever Postgres returned last." A customer paid the wrong amount
for the wrong product before anyone noticed. Know what queries your
metadata keys.

**A 3am "who changed my subscription" hunt was the upgrade endpoint we had
just built, tested on the CTO's own account.** Read the request log before
theorising. And don't run migrations tired.
