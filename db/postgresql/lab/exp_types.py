"""R03 text/varchar/char / R06 UUID 主键 / R11 Hash vs B-tree。"""
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time

import common as c

N6 = int(os.environ.get("LAB_N6", "5000000"))     # R06 每个变体的插入行数
N3 = int(os.environ.get("LAB_N3", "3000000"))     # R03 行数
N11 = int(os.environ.get("LAB_N11", "5000000"))   # R11 行数


def pgbench(db, script, seconds=6, runs=5, port=c.PORT):
    """用 pgbench 跑自定义只读脚本,返回每轮 tps。先热身一轮丢弃。"""
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as f:
        f.write(script)
        path = f.name
    tps = []
    for i in range(runs + 1):
        out = subprocess.run(
            ["pgbench", "-n", "-f", path, "-T", str(seconds), "-c", "1", "-h", c.HOST, "-p", str(port), "-U", c.USER, db],
            capture_output=True, text=True).stdout
        m = re.search(r"tps = ([\d.]+)", out)
        if i > 0 and m:
            tps.append(round(float(m.group(1))))
    os.unlink(path)
    return {"tps_median": statistics.median(tps), "tps_min": min(tps), "tps_max": max(tps), "runs": tps}


def r03():
    DBNAME = "lab_r03"; db = c.fresh_db(DBNAME)
    out = {"rule": "R03", "server": c.version(db), "rows": N3, "variants": {}}
    for label, typ in (("text", "text"), ("varchar(32)", "varchar(32)"), ("char(32)", "char(32)")):
        tbl = "t_" + label.split("(")[0]
        c.q(db, f"CREATE TABLE {tbl} (id bigint, code {typ})")
        t = time.time()
        # 长度 8~32 的变长字符串
        c.q(db, f"INSERT INTO {tbl} SELECT g, substr(md5(g::text), 1, 8 + (g % 25)::int) FROM generate_series(1, {N3}) g")
        load_s = round(time.time() - t, 1)
        c.q(db, f"CREATE INDEX {tbl}_code ON {tbl} (code)")
        c.q(db, f"VACUUM (ANALYZE) {tbl}")
        script = f"\\set k random(1, {N3})\nSELECT id FROM {tbl} WHERE code = substr(md5(:k::text), 1, 8 + (:k %% 25)::int);\n".replace("%%", "%")
        out["variants"][label] = {
            "heap_bytes": c.size(db, tbl), "index_bytes": c.index_size(db, f"{tbl}_code"),
            "load_s": load_s, "lookup": pgbench(DBNAME, script),
        }
    db.close(); c.drop_db("lab_r03")
    return out


def r06():
    db = c.fresh_db("lab_r06")
    variants = {
        "bigint identity": "id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY",
        "uuid v7 (uuidv7())": "id uuid PRIMARY KEY DEFAULT uuidv7()",
        "uuid v4 (uuidv4())": "id uuid PRIMARY KEY DEFAULT uuidv4()",
    }
    out = {"rule": "R06", "server": c.version(db), "rows": N6, "shared_buffers": c.q1(db, "SHOW shared_buffers"), "variants": {}}
    for rep in range(3):
        for label, col in variants.items():            # 每轮重新建表,避免互相干扰
            tbl = "p_" + str(abs(hash(label)) % 10**6)
            c.q(db, f"DROP TABLE IF EXISTS {tbl}")
            c.q(db, f"CREATE TABLE {tbl} ({col}, payload text)")
            c.q(db, "CHECKPOINT")
            lsn0 = c.q1(db, "SELECT pg_current_wal_insert_lsn()")
            st0 = c.q(db, "SELECT blks_read, blks_hit FROM pg_stat_database WHERE datname = current_database()")[0]
            t = time.time()
            c.q(db, f"INSERT INTO {tbl} (payload) SELECT 'payload-payload-payload' FROM generate_series(1, {N6})")
            dur = round(time.time() - t, 2)
            wal = c.q1(db, "SELECT pg_wal_lsn_diff(pg_current_wal_insert_lsn(), %s)", (lsn0,))
            st1 = c.q(db, "SELECT blks_read, blks_hit FROM pg_stat_database WHERE datname = current_database()")[0]
            pk = c.q1(db, f"SELECT indexrelid::regclass::text FROM pg_index WHERE indrelid='{tbl}'::regclass AND indisprimary")
            v = out["variants"].setdefault(label, {"insert_s": [], "wal_bytes": [], "blks_read": [], "blks_hit": []})
            v["insert_s"].append(dur)
            v["wal_bytes"].append(int(wal))
            v["blks_read"].append(st1[0] - st0[0])
            v["blks_hit"].append(st1[1] - st0[1])
            v["index_bytes"] = c.index_size(db, pk)
            v["heap_bytes"] = c.size(db, tbl)
            c.q(db, f"DROP TABLE {tbl}")
            print("R06", rep, label, dur, "s", flush=True)
    db.close(); c.drop_db("lab_r06")
    return out


def r11():
    DBNAME = "lab_r11"; db = c.fresh_db(DBNAME)
    c.q(db, f"CREATE TABLE kv AS SELECT g AS id, md5(g::text) AS k, g % 100 AS v FROM generate_series(1, {N11}) g")
    c.q(db, "VACUUM (ANALYZE) kv")
    script = f"\\set k random(1, {N11})\nSELECT v FROM kv WHERE k = md5(:k::text);\n"
    out = {"rule": "R11", "server": c.version(db), "rows": N11, "key": "md5 文本(32 字符)", "variants": {}}
    for label, ddl in (("B-tree", "CREATE INDEX kv_k ON kv USING btree (k)"), ("Hash", "CREATE INDEX kv_k ON kv USING hash (k)")):
        t = time.time()
        c.q(db, ddl)
        build_s = round(time.time() - t, 1)
        c.q(db, "ANALYZE kv")
        plan = c.explain(db, "SELECT v FROM kv WHERE k = md5('12345')")
        out["variants"][label] = {
            "index_bytes": c.index_size(db, "kv_k"), "build_s": build_s,
            "plan_nodes": plan["nodes"], "plan_indexes": plan["indexes"],
            "lookup": pgbench(DBNAME, script),
        }
        c.q(db, "DROP INDEX kv_k")
    db.close(); c.drop_db("lab_r11")
    return out


FUNCS = {"r03": r03, "r06": r06, "r11": r11}

if __name__ == "__main__":
    for name in (sys.argv[1:] or FUNCS):
        t = time.time()
        r = FUNCS[name]()
        c.save(r["rule"], r)
        print(name, "done in %.0fs" % (time.time() - t), flush=True)
