# Assumptions & what is real vs simulated

**Real:** Olist orders, items, customers, sellers, products, payments, reviews, geolocation
(Brazilian e-commerce, 2016 to 2018, from Kaggle; not redistributed in this repo).

**Simulated** (seeded, reproducible, flagged `is_simulated = TRUE` in `core`, living in the `sim` schema):
warehouses, purchase orders, inventory snapshots, supplier lead-time behaviour.
Olist has no inventory or replenishment data, so these exist to demonstrate the analytics
(turnover, days of supply, stockout risk, supplier scorecards). **Inventory findings describe the
simulation's mechanics, not real Olist sellers' stock.**

## Simulation design (`src/sca/simulate.py`)

| Item | Assumption |
|---|---|
| Seed | `numpy` default_rng(42); the same input always gives the same output |
| Scope | Top 500 products by order-line volume (non-canceled) |
| Warehouses | 4 regional DCs (Southeast, South, Northeast, Center-North); each customer state maps to its nearest DC (`core.map_state_warehouse`) |
| Demand | **Real** weekly Olist units per product × DC (week = Monday start) |
| Primary supplier | The seller with most lines for the product |
| Policy | Weekly-review (s, S). s = ⌈μ·L⌉ + safety, S = s + 4 weeks of mean demand, where L = quoted lead time in weeks + 1 review week |
| Safety stock | z·σ·√L with z = 1.28 (~90% service level), σ = std dev of weekly demand |
| Planning vs reality | The policy plans with the supplier's *quoted* lead time; actual lead times are lognormal around the supplier's *actual* median |
| Supplier behaviour | Quoted lead 5 to 16 days. 85% of suppliers "reliable" (actual median 75 to 95% of quoted, σ 0.12 to 0.28). 15% "unreliable" (actual median 140 to 200% of quoted, σ 0.45 to 0.70). Profiles in `sim.supplier_profile` |
| Shortage handling | Lost sales (no backorders): `units_sold = min(on_hand, demand)` |
| Opening stock | Start of data = S |
| Order timing | PO placed at end of week (day 7); lands `lead_days` later |
| Unit cost | 60% of the product's mean selling price (a proxy; Olist has no COGS) |
| In-transit POs | POs whose receipt is after the last simulated day have `is_received = FALSE` and are excluded from lead-time statistics |

Stockouts, overstock and turnover are *emergent*: they result from demand variability meeting
unreliable lead times under a policy that plans with optimistic quoted lead times.

## KPI conventions

- **On-time delivery**: delivered *date* ≤ estimated *date* (Olist estimates carry no time of day). Orders without a delivery date are excluded from the denominator and flagged.
- **Days late** = max(delivered date minus  estimated date, 0).
- Money is in BRL; no currency conversion or inflation adjustment.

## Known limits

- Olist is a marketplace: sellers ship directly, so "supplier" = seller and "warehouse" is a modelling device.
- 2016 to 2018 data; results are illustrative, not current.
- Weekly granularity hides intra-week stockouts.
