-- One row per order line (Olist: each item row = 1 unit).
CREATE TABLE core.fact_order_line (
    order_id          TEXT NOT NULL,
    item_no           INT  NOT NULL,
    product_key       INT  NOT NULL REFERENCES core.dim_product,
    supplier_key      INT  NOT NULL REFERENCES core.dim_supplier,
    customer_key      INT  NOT NULL REFERENCES core.dim_customer,
    warehouse_key     INT  NOT NULL REFERENCES core.dim_warehouse,   -- SIMULATED fulfilment DC (customer's regional DC)
    purchase_date_key INT  NOT NULL REFERENCES core.dim_date,
    order_status      TEXT NOT NULL,
    qty               INT  NOT NULL DEFAULT 1,
    price             NUMERIC(12,2) NOT NULL CHECK (price > 0),
    freight_value     NUMERIC(12,2) NOT NULL CHECK (freight_value >= 0),
    PRIMARY KEY (order_id, item_no)
);
INSERT INTO core.fact_order_line (order_id, item_no, product_key, supplier_key, customer_key, warehouse_key,
                                  purchase_date_key, order_status, qty, price, freight_value)
SELECT i.order_id, i.item_no, p.product_key, s.supplier_key, c.customer_key, c.warehouse_key,
       to_char(o.purchase_ts, 'YYYYMMDD')::int, o.status, 1, i.price, i.freight_value
FROM staging.order_items i
JOIN staging.orders o     USING (order_id)
JOIN core.dim_product p   ON p.product_id = i.product_id
JOIN core.dim_supplier s  ON s.seller_id = i.seller_id
JOIN core.dim_customer c  ON c.customer_id = o.customer_id;

CREATE INDEX ON core.fact_order_line (product_key, warehouse_key, purchase_date_key);
CREATE INDEX ON core.fact_order_line (supplier_key);
