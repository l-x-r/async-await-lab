"""R01 外键索引 / R07 复合索引顺序与 skip scan / R08 部分索引 / R09 覆盖索引 / R10 游标分页。"""
import statistics
import sys
import time

import common as c

T0 = "2025-06-01"


def r01():
    db = c.fresh_db("lab_r01", template="lab_base")
    t = time.time()
    c.q(db, "ALTER TABLE orders ADD CONSTRAINT orders_customer_fk FOREIGN KEY (customer_id) REFERENCES customers(id)")
    fk_validate_s = round(time.time() - t, 1)

    def delete_batch(first, n):
        ms = []
        for cid in range(first, first + n):  # 这些客户没有订单(订单只引用 id <= 900000)
            ms.append(c.timed(db, "DELETE FROM customers WHERE id = %s", (cid,)))
        return ms

    no_idx = delete_batch(950001, 6)
    t = time.time()
    c.q(db, "CREATE INDEX orders_customer_id_idx ON orders (customer_id)")
    build_s = round(time.time() - t, 1)
    idx_bytes = c.index_size(db, "orders_customer_id_idx")
    with_idx = delete_batch(950101, 6)

    # 代价:批量插入 20 万行,在有/无该索引时的耗时(各 3 次取中位数)
    ins = "INSERT INTO orders (customer_id,status,created_at,amount,note) SELECT 1+floor(random()*900000)::bigint,'paid',now(),1,'x' FROM generate_series(1,200000)"
    def insert_ms():
        out = []
        for _ in range(3):
            out.append(c.timed(db, ins))
            c.q(db, "DELETE FROM orders WHERE note = 'x'")
        return out
    ins_with = insert_ms()
    c.q(db, "DROP INDEX orders_customer_id_idx")
    ins_without = insert_ms()
    out = {
        "rule": "R01", "server": c.version(db), "rows": c.N,
        "fk_add_and_validate_s": fk_validate_s,
        "delete_unreferenced_parent_ms_without_index": no_idx,
        "delete_unreferenced_parent_ms_with_index": with_idx,
        "median_without_ms": statistics.median(no_idx),
        "median_with_ms": statistics.median(with_idx),
        "speedup_x": round(statistics.median(no_idx) / max(statistics.median(with_idx), 0.01)),
        "index_build_s": build_s, "index_bytes": idx_bytes, "index_size": c.fmt_bytes(idx_bytes),
        "insert_200k_ms_with_index": ins_with, "insert_200k_ms_without_index": ins_without,
    }
    db.close(); c.drop_db("lab_r01")
    return out


def _variant_runs(db, label, create_sql, drop_sql, queries, settings=None):
    if create_sql:
        c.q(db, create_sql)
    res = {"index_bytes": None, "queries": {}}
    if create_sql:
        res["index_bytes"] = c.index_size(db, create_sql.split()[2])
    for qn, sql in queries.items():
        res["queries"][qn] = c.explain(db, sql, repeats=5, warmup=1, settings=settings)
    if drop_sql:
        c.q(db, drop_sql)
    return res


def r07():
    queries = {
        "A_status等值+时间范围": f"SELECT count(*) FROM orders WHERE status='shipped' AND created_at >= '{T0}' AND created_at < '{T0}'::timestamptz + interval '1 day'",
        "B_仅时间范围(缺前导列status)": f"SELECT count(*) FROM orders WHERE created_at >= '{T0}' AND created_at < '{T0}'::timestamptz + interval '1 day'",
        "C_仅时间范围(前导列customer_id有90万个取值)": f"SELECT count(*) FROM orders WHERE created_at >= '{T0}' AND created_at < '{T0}'::timestamptz + interval '1 hour'",
    }
    variants = {
        "无索引(顺序扫描基线)": (None, None),
        "(status, created_at)": ("CREATE INDEX ix_sc ON orders (status, created_at)", "DROP INDEX ix_sc"),
        "(created_at, status)": ("CREATE INDEX ix_cs ON orders (created_at, status)", "DROP INDEX ix_cs"),
        "(customer_id, created_at)": ("CREATE INDEX ix_cc ON orders (customer_id, created_at)", "DROP INDEX ix_cc"),
    }
    out = {"rule": "R07", "rows": c.N, "versions": {}}
    for label, port in (("PostgreSQL 18", c.PORT), ("PostgreSQL 16", c.PORT_OLD)):
        db = c.fresh_db("lab_r07", port, template="lab_base")
        c.q(db, "VACUUM ANALYZE orders")
        ver = c.version(db)
        vres = {}
        for vn, (cr, dr) in variants.items():
            vres[vn] = _variant_runs(db, vn, cr, dr, queries)
        out["versions"][label] = {"server": ver, "variants": vres}
        db.close(); c.drop_db("lab_r07", port)
    return out


def r08():
    db = c.fresh_db("lab_r08", template="lab_base")
    q_top = "SELECT * FROM orders WHERE status='pending' ORDER BY created_at DESC LIMIT 50"
    q_rng = f"SELECT count(*) FROM orders WHERE status='pending' AND created_at >= '{T0}'"
    ins = "INSERT INTO orders (customer_id,status,created_at,amount,note) SELECT 1+floor(random()*900000)::bigint,'paid',now(),1,'x' FROM generate_series(1,300000)"
    variants = {
        "无索引": None,
        "全量复合索引 (status, created_at)": "CREATE INDEX ix8 ON orders (status, created_at)",
        "部分索引 (created_at) WHERE status='pending'": "CREATE INDEX ix8 ON orders (created_at) WHERE status = 'pending'",
    }
    out = {"rule": "R08", "server": c.version(db), "rows": c.N, "variants": {}}
    for vn, cr in variants.items():
        if cr:
            c.q(db, cr)
        r = {"index_bytes": c.index_size(db, "ix8") if cr else 0}
        r["q_top50"] = c.explain(db, q_top)
        r["q_range"] = c.explain(db, q_rng)
        ms = []
        for _ in range(3):
            ms.append(c.timed(db, ins))
            c.q(db, "DELETE FROM orders WHERE note='x'")
        r["insert_300k_ms"] = ms
        out["variants"][vn] = r
        if cr:
            c.q(db, "DROP INDEX ix8")
    db.close(); c.drop_db("lab_r08")
    return out


def r09():
    db = c.fresh_db("lab_r09", template="lab_base")
    c.q(db, "CREATE TABLE o9 AS SELECT * FROM orders WHERE id <= 3000000")
    c.q(db, "ALTER TABLE o9 ADD PRIMARY KEY (id)")
    c.q(db, "ALTER TABLE o9 SET (autovacuum_enabled = off)")
    c.q(db, "VACUUM (ANALYZE) o9")
    qy = "SELECT sum(amount) FROM o9 WHERE customer_id BETWEEN 1000 AND 3000"
    out = {"rule": "R09", "server": c.version(db), "rows": 3000000, "steps": {}}
    c.q(db, "CREATE INDEX o9_plain ON o9 (customer_id)")
    out["steps"]["普通索引(customer_id)"] = c.explain(db, qy, settings={"enable_bitmapscan": "off"})
    c.q(db, "DROP INDEX o9_plain")
    c.q(db, "CREATE INDEX o9_cov ON o9 (customer_id) INCLUDE (amount)")
    out["index_bytes_cov"] = c.index_size(db, "o9_cov")
    out["steps"]["覆盖索引,VACUUM 之后"] = c.explain(db, qy)
    c.q(db, "UPDATE o9 SET note = note WHERE id % 5 = 0")  # 20% 行产生新版本,清除多数页的可见性位
    out["steps"]["覆盖索引,大量 UPDATE 之后、VACUUM 之前"] = c.explain(db, qy)
    c.q(db, "VACUUM o9")
    out["steps"]["覆盖索引,再次 VACUUM 之后"] = c.explain(db, qy)
    db.close(); c.drop_db("lab_r09")
    return out


def r10():
    db = c.fresh_db("lab_r10", template="lab_base")
    out = {"rule": "R10", "server": c.version(db), "rows": c.N, "offset": {}, "keyset": {}}
    for off in (0, 10_000, 100_000, 1_000_000, 5_000_000):
        out["offset"][off] = c.explain(db, f"SELECT * FROM orders ORDER BY id OFFSET {off} LIMIT 20")
        out["keyset"][off] = c.explain(db, f"SELECT * FROM orders WHERE id > {off} ORDER BY id LIMIT 20")
    db.close(); c.drop_db("lab_r10")
    return out


FUNCS = {"r01": r01, "r07": r07, "r08": r08, "r09": r09, "r10": r10}

if __name__ == "__main__":
    for name in (sys.argv[1:] or FUNCS):
        t = time.time()
        r = FUNCS[name]()
        c.save(r["rule"], r)
        print(name, "done in %.0fs" % (time.time() - t))
