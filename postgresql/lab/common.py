"""实验公共工具:连接、造数据、EXPLAIN 计时、并发读写探针、结果保存。

只依赖 psycopg2。服务器地址通过环境变量配置,见 README.md。
"""
import json
import os
import statistics
import threading
import time

import psycopg2

HOST = os.environ.get("LAB_PGHOST", "/tmp")
PORT = int(os.environ.get("LAB_PGPORT", "54318"))          # 主实验服务器(PostgreSQL 18)
PORT_OLD = int(os.environ.get("LAB_PGPORT_OLD", "54316"))  # 对照服务器(PostgreSQL 16),仅 R07 使用
USER = os.environ.get("LAB_PGUSER", "postgres")
N = int(os.environ.get("LAB_N", "10000000"))               # 主表行数
RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")


def connect(db="postgres", port=PORT, autocommit=True):
    c = psycopg2.connect(host=HOST, port=port, user=USER, dbname=db)
    c.autocommit = autocommit
    return c


def q(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        try:
            return cur.fetchall()
        except psycopg2.ProgrammingError:
            return None


def q1(conn, sql, params=None):
    r = q(conn, sql, params)
    return r[0][0] if r else None


def fresh_db(name, port=PORT, template=None):
    a = connect("postgres", port)
    q(a, f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
    q(a, f"CREATE DATABASE {name}" + (f" TEMPLATE {template}" if template else ""))
    a.close()
    return connect(name, port)


def drop_db(name, port=PORT):
    a = connect("postgres", port)
    q(a, f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
    a.close()


def version(conn):
    return q1(conn, "SHOW server_version")


def size(conn, rel):
    return q1(conn, "SELECT pg_relation_size(%s::regclass)", (rel,))


def total_size(conn, rel):
    return q1(conn, "SELECT pg_total_relation_size(%s::regclass)", (rel,))


def index_size(conn, idx):
    return q1(conn, "SELECT pg_relation_size(%s::regclass)", (idx,))


def _walk(plan, acc):
    acc["nodes"].append(plan.get("Node Type"))
    if plan.get("Index Name"):
        acc["indexes"].append(plan["Index Name"])
    if plan.get("Node Type") == "Index Only Scan":
        acc["heap_fetches"] += plan.get("Heap Fetches", 0)
    if "Index Searches" in plan:  # PostgreSQL 18 起 EXPLAIN ANALYZE 会输出索引搜索次数
        acc["index_searches"] = acc.get("index_searches", 0) + plan["Index Searches"]
    for ch in plan.get("Plans", []) or []:
        _walk(ch, acc)


def explain(conn, sql, repeats=5, warmup=1, params=None, settings=None):
    """重复执行 EXPLAIN (ANALYZE, BUFFERS),返回耗时分布与计划摘要。"""
    cur = conn.cursor()
    for k, v in (settings or {}).items():
        cur.execute(f"SET {k} = {v}")
    ms, last = [], None
    for i in range(warmup + repeats):
        cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, params)
        doc = cur.fetchone()[0]
        doc = doc[0] if isinstance(doc, list) else json.loads(doc)[0]
        if i >= warmup:
            ms.append(doc["Execution Time"])
        last = doc
    for k in (settings or {}):
        cur.execute(f"RESET {k}")
    root = last["Plan"]
    acc = {"nodes": [], "indexes": [], "heap_fetches": 0}
    _walk(root, acc)
    return {
        "ms_median": round(statistics.median(ms), 3),
        "ms_min": round(min(ms), 3),
        "ms_max": round(max(ms), 3),
        "runs": [round(x, 3) for x in ms],
        "shared_hit": root.get("Shared Hit Blocks", 0),
        "shared_read": root.get("Shared Read Blocks", 0),
        "nodes": acc["nodes"],
        "indexes": acc["indexes"],
        "heap_fetches": acc["heap_fetches"],
        "index_searches": acc.get("index_searches"),
    }


def timed(conn, sql, params=None):
    t = time.perf_counter()
    q(conn, sql, params)
    return round((time.perf_counter() - t) * 1000, 1)


class Probe(threading.Thread):
    """后台探针:循环执行一条语句,记录每次耗时,用来观察 DDL 期间的并发停顿。"""

    def __init__(self, db, port, sql, interval=0.002):
        super().__init__(daemon=True)
        self.conn = connect(db, port)
        self.sql, self.interval = sql, interval
        self.lat, self.stop_flag = [], False

    def run(self):
        cur = self.conn.cursor()
        while not self.stop_flag:
            t = time.perf_counter()
            try:
                cur.execute(self.sql)
            except Exception:  # 实验里不应出现,出现就记为 -1 便于发现
                self.lat.append(-1)
                continue
            self.lat.append((time.perf_counter() - t) * 1000)
            time.sleep(self.interval)

    def stop(self):
        self.stop_flag = True
        self.join()
        lat = [x for x in self.lat if x >= 0]
        return {
            "count": len(lat),
            "max_ms": round(max(lat), 1) if lat else None,
            "p99_ms": round(sorted(lat)[int(len(lat) * 0.99) - 1], 1) if len(lat) > 10 else None,
            "stalls_over_100ms": sum(1 for x in lat if x > 100),
            "errors": sum(1 for x in self.lat if x < 0),
        }


class LockWatcher(threading.Thread):
    """后台采样 pg_locks:记录目标表上、指定后端进程持有/等待过的锁模式。"""

    def __init__(self, db, port, rel, pid):
        super().__init__(daemon=True)
        self.conn = connect(db, port)
        self.rel, self.pid, self.modes, self.stop_flag = rel, pid, set(), False

    def run(self):
        while not self.stop_flag:
            rows = q(
                self.conn,
                "SELECT mode FROM pg_locks WHERE pid=%s AND locktype='relation' "
                "AND relation=%s::regclass AND granted",
                (self.pid, self.rel),
            )
            for (m,) in rows or []:
                self.modes.add(m)
            time.sleep(0.02)

    def stop(self):
        self.stop_flag = True
        self.join()
        return sorted(self.modes)


def run_ddl_with_probes(db, port, ddl_sql, rel, writer_sql, reader_sql):
    """在并发写入/读取探针存在时执行一条 DDL,返回耗时、锁模式与探针停顿统计。"""
    main = connect(db, port)
    pid = q1(main, "SELECT pg_backend_pid()")
    w, r = Probe(db, port, writer_sql), Probe(db, port, reader_sql)
    lw = LockWatcher(db, port, rel, pid)
    for t in (w, r, lw):
        t.start()
    time.sleep(0.5)  # 让探针先跑一会儿,建立基线
    t0 = time.perf_counter()
    q(main, ddl_sql)
    dur = (time.perf_counter() - t0) * 1000
    time.sleep(0.3)
    out = {
        "ddl": ddl_sql,
        "duration_ms": round(dur, 1),
        "lock_modes_seen": lw.stop(),
        "writer": w.stop(),
        "reader": r.stop(),
    }
    main.close()
    return out


def save(name, data):
    os.makedirs(RESULTS, exist_ok=True)
    with open(os.path.join(RESULTS, f"{name}.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    print(f"[saved] results/{name}.json")


def fmt_bytes(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.1f} {u}" if u != "B" else f"{n} B"
        n /= 1024


# 主表造数据 SQL:created_at 随 id 单调递增(贴近真实 OLTP 的插入顺序),状态分布倾斜
BASE_DDL = """
CREATE TABLE customers (
  id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  name text NOT NULL
);
CREATE TABLE orders (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  customer_id bigint NOT NULL,
  status      text NOT NULL,
  created_at  timestamptz NOT NULL,
  amount      numeric(10,2) NOT NULL,
  note        text
);
"""

BASE_ROWS = """
INSERT INTO customers (name)
SELECT 'customer_' || g FROM generate_series(1, 1000000) g;

INSERT INTO orders (customer_id, status, created_at, amount, note)
SELECT 1 + floor(random() * 900000)::bigint,
       CASE WHEN r < 0.920 THEN 'paid'
            WHEN r < 0.980 THEN 'shipped'
            WHEN r < 0.995 THEN 'cancelled'
            WHEN r < 0.999 THEN 'pending'
            ELSE 'refunded' END,
       timestamptz '2024-01-01' + (g::numeric / %(n)s) * interval '730 days'
         + random() * interval '1 hour',
       round((random() * 1000)::numeric, 2),
       md5(g::text) || md5((g + 1)::text)
FROM (SELECT g, random() AS r FROM generate_series(1, %(n)s) g) s;
"""
