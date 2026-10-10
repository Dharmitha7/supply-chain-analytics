-- SIMULATED facts (is_simulated = TRUE on every row). Built from sim.* written by sca.simulate.
-- Demand inside the simulation is real Olist sales; stock, orders and lead times are synthetic.
DROP TABLE IF EXISTS core.fact_inventory_snapshot, core.fact_purchase_order CASCADE;


CREATE TABLE core.fact_purchase_order (
    po_id             TEXT PRIMARY KEY,
    supplier_key      INT NOT NULL REFERENCES core.dim_supplier,
    product_key       INT NOT NULL REFERENCES core.dim_product,
    warehouse_key     INT NOT NULL REFERENCES core.dim_warehouse,
    order_date_key    INT NOT NULL REFERENCES core.dim_date,
    expected_date     DATE NOT NULL,
    receipt_date      DATE NOT NULL,
    qty               INT NOT NULL CHECK (qty > 0),
    unit_cost         NUMERIC(12,2) NOT NULL,
    lead_days         INT NOT NULL CHECK (lead_days >= 1),
    days_late         INT NOT NULL,                        -- receipt - expected, floored at 0
    is_on_time        BOOLEAN NOT NULL,
    is_received       BOOLEAN NOT NULL,                    -- receipt on/before last simulated day (else still in transit)
    is_simulated      BOOLEAN NOT NULL DEFAULT TRUE
);
INSERT INTO core.fact_purchase_order
SELECT po.po_id, po.supplier_key, po.product_key, po.warehouse_key, to_char(po.order_date, 'YYYYMMDD')::int,
       po.expected_date, po.receipt_date, po.qty, po.unit_cost, po.lead_days,
       greatest(po.receipt_date - po.expected_date, 0), po.receipt_date <= po.expected_date,
       po.receipt_date <= (SELECT max(snapshot_date)::date + 6 FROM sim.inventory_weekly),
       TRUE
FROM sim.purchase_order po;

CREATE INDEX ON core.fact_purchase_order (supplier_key);

CREATE TABLE core.fact_inventory_snapshot (
    snapshot_date_key INT NOT NULL REFERENCES core.dim_date,     -- week start (Monday); values are end-of-week
    product_key       INT NOT NULL REFERENCES core.dim_product,
    warehouse_key     INT NOT NULL REFERENCES core.dim_warehouse,
    supplier_key      INT NOT NULL REFERENCES core.dim_supplier,
    on_hand           INT NOT NULL CHECK (on_hand >= 0),
    on_order          INT NOT NULL CHECK (on_order >= 0),
    reorder_point     INT NOT NULL,
    safety_stock      INT NOT NULL,
    units_demand      INT NOT NULL,
    units_sold        INT NOT NULL,
    units_received    INT NOT NULL,
    unit_cost         NUMERIC(12,2) NOT NULL,
    is_stockout       BOOLEAN NOT NULL,                          -- on_hand = 0 at week end
    is_simulated      BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (snapshot_date_key, product_key, warehouse_key)
);
INSERT INTO core.fact_inventory_snapshot
SELECT to_char(i.snapshot_date, 'YYYYMMDD')::int, i.product_key, i.warehouse_key, i.supplier_key,
       i.on_hand, i.on_order, i.reorder_point, i.safety_stock, i.units_demand, i.units_sold, i.units_received,
       i.unit_cost, i.on_hand = 0, TRUE
FROM sim.inventory_weekly i;

CREATE INDEX ON core.fact_inventory_snapshot (product_key, warehouse_key);

INSERT INTO dq.run_log(step, table_name, rows_in, rows_out) VALUES
 ('core', 'core.fact_purchase_order',     (SELECT count(*) FROM sim.purchase_order),    (SELECT count(*) FROM core.fact_purchase_order)),
 ('core', 'core.fact_inventory_snapshot', (SELECT count(*) FROM sim.inventory_weekly),  (SELECT count(*) FROM core.fact_inventory_snapshot));
