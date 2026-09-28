# Catalogue conventions

## Nickname grammar

Every price in the new catalogue carries a nickname the scripts can parse:

```
{TIER}-{PERIOD}-{CURRENCY}-{SURFACE}-{KIND}[-{AMOUNT}]

STU-Y-EUR-WEB-LEG-11880      Studio, yearly, EUR, web, legacy mirror, 118.80
STU-M-EUR-RTO-LEG-2490       Studio, monthly, EUR, rent-to-own mirror, 24.90
SUI-M-EUR-WEB-LIST           Suite, monthly, EUR, web, current list price
SUI-ONE-EUR-WEB-WIN-14900    Suite, one-time, EUR, web, upgrade with a launch window
```

| Slot | Values | Notes |
|---|---|---|
| TIER | codes from `config.TIER_CODES` | |
| PERIOD | `M` `Y` `ONE` | cross-checked against `recurring.interval`; `interval_count` must be 1 |
| CURRENCY | ISO, upper | cross-checked against `currency` |
| SURFACE | `WEB` `APP` `RTO` | RTO collapses channel and rent-to-own into one slot |
| KIND | `LIST` `LEG` `WIN` | current price / legacy mirror / windowed upgrade |
| AMOUNT | minor units | optional; cross-checked against `unit_amount` when present |

`write_price_metadata.py` refuses to run if the nickname and the Price
object disagree. A nickname is a display string; it can lie.

## Metadata keys

Written to every price by `write_price_metadata.py`:

| Key | Values | Read by |
|---|---|---|
| `tier` | `studio` `suite` `logic` | resolver, migration |
| `kind` | `list` `legacy` `upgrade` | resolver, price map |
| `visibility` | `public` `hidden` | price map; hidden prices must never reach checkout |
| `channel` | `web` `app` | migration |
| `rto` | `true` `false` | resolver, migration |
| `grants` | `studio_for_life` `suite_for_life` | resolver; explicit override for one-time prices |
| `window` | `first30` `standard` | upgrade pricing |

Product-level metadata is for reporting only. Put nothing there the app
needs: a product that mixes subscriptions and one-time prices can't carry
one truthful licence value.

## Rules that keep the catalogue clean

- Prices are created in code, never by hand.
- `lookup_key` only on list prices the code actually resolves. Hidden
  legacy prices get none, so nothing can ever look them up and sell them.
- Promotions are coupons, never new prices.
- Legacy mirrors are one price per currency, because each one has to match
  an existing price exactly.
- Type values, don't paste them. A trailing newline in a metadata value
  silently defeats every string comparison downstream.
