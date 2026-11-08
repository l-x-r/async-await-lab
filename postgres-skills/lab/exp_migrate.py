"""R12 在线加 NOT NULL / R13 加列默认值的易变性 / R14 CREATE INDEX CONCURRENTLY。

做法:对 1000 万行的 orders 表执行 DDL,同时有两个后台探针(一个不停 INSERT,一个不停按主键 SELECT),
并轮询 pg_locks 记录 DDL 会话在目标表上持有过的锁模式。看三件事:DDL 耗时、持锁级别、并发读写被阻塞多久。
"""
import sys
import time

import common as c

WRITER = "INSERT INTO orders (customer_id, status, created_at, amount, note) VALUES (1, 'paid', now(), 1, 'w')"
READER = "SELECT id FROM orders WHERE id = 1"


def step(db, ddl, rel="orders", port=c.PORT):
    return c.run_ddl_with_probes(db, port, ddl, rel, WRITER, READER)


def fresh():
    db = c.fresh_db("lab_mig", template="lab_base")
    c.q(db, "ANALYZE orders")
    return db


def r12():
    out = {"rule": "R12", "rows": c.N}
    # 方案 A:直接 SET NOT NULL
    db = fresh(); out["server"] = c.version(db); db.close()
    out["A_直接_SET_NOT_NULL"] = step("lab_mig", "ALTER TABLE orders ALTER COLUMN note SET NOT NULL")
    c.drop_db("lab_mig")
    # 方案 B:CHECK ... NOT VALID -> VALIDATE -> SET NOT NULL -> 删除 CHECK
    fresh().close()
    b = {}
    b["1_ADD_CHECK_NOT_VALID"] = step("lab_mig", "ALTER TABLE orders ADD CONSTRAINT note_nn CHECK (note IS NOT NULL) NOT VALID")
    b["2_VALIDATE"] = step("lab_mig", "ALTER TABLE orders VALIDATE CONSTRAINT note_nn")
    b["3_SET_NOT_NULL(已有已验证的CHECK)"] = step("lab_mig", "ALTER TABLE orders ALTER COLUMN note SET NOT NULL")
    b["4_DROP_CHECK"] = step("lab_mig", "ALTER TABLE orders DROP CONSTRAINT note_nn")
    out["B_CHECK_NOT_VALID四步法"] = b
    c.drop_db("lab_mig")
    # 方案 C:PostgreSQL 18 原生 NOT NULL ... NOT VALID
    fresh().close()
    cc = {}
    try:
        cc["1_ADD_NOT_NULL_NOT_VALID"] = step("lab_mig", "ALTER TABLE orders ADD CONSTRAINT note_nn2 NOT NULL note NOT VALID")
        cc["2_VALIDATE"] = step("lab_mig", "ALTER TABLE orders VALIDATE CONSTRAINT note_nn2")
        d = c.connect("lab_mig")
        cc["结果"] = c.q(d, "SELECT a.attnotnull, con.convalidated FROM pg_attribute a JOIN pg_constraint con ON con.conrelid=a.attrelid AND con.contype='n' WHERE a.attrelid='orders'::regclass AND a.attname='note'")
        d.close()
    except Exception as e:  # 语法不被支持时如实记录
        cc["error"] = str(e).strip().splitlines()[0]
    out["C_PG18原生NOT_NULL_NOT_VALID"] = cc
    c.drop_db("lab_mig")
    return out


def r13():
    out = {"rule": "R13", "rows": c.N}
    for label, ddl in (
        ("常量默认值 DEFAULT 0", "ALTER TABLE orders ADD COLUMN c_const int NOT NULL DEFAULT 0"),
        ("易变默认值 clock_timestamp()", "ALTER TABLE orders ADD COLUMN c_ts timestamptz NOT NULL DEFAULT clock_timestamp()"),
        ("易变默认值 uuidv7()", "ALTER TABLE orders ADD COLUMN c_uuid uuid NOT NULL DEFAULT uuidv7()"),
    ):
        db = fresh(); out["server"] = c.version(db)
        before = c.q1(db, "SELECT pg_relation_filenode('orders')")
        db.close()
        r = step("lab_mig", ddl)
        d = c.connect("lab_mig")
        after = c.q1(d, "SELECT pg_relation_filenode('orders')")
        d.close()
        r["relfilenode_changed(表被重写)"] = before != after
        out[label] = r
        c.drop_db("lab_mig")
    return out


def r14():
    out = {"rule": "R14", "rows": c.N}
    for label, ddl in (
        ("普通 CREATE INDEX", "CREATE INDEX ix14 ON orders (customer_id)"),
        ("CREATE INDEX CONCURRENTLY", "CREATE INDEX CONCURRENTLY ix14 ON orders (customer_id)"),
    ):
        db = fresh(); out["server"] = c.version(db); db.close()
        out[label] = step("lab_mig", ddl)
        c.drop_db("lab_mig")
    return out


FUNCS = {"r12": r12, "r13": r13, "r14": r14}

if __name__ == "__main__":
    for name in (sys.argv[1:] or FUNCS):
        t = time.time()
        r = FUNCS[name]()
        c.save(r["rule"], r)
        print(name, "done in %.0fs" % (time.time() - t), flush=True)
