ALTER TABLE orders ALTER COLUMN note SET NOT NULL;
ALTER TABLE orders ADD COLUMN c_ts timestamptz NOT NULL DEFAULT clock_timestamp();
CREATE INDEX ix14 ON orders (customer_id);
