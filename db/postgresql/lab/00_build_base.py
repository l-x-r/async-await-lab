"""构建基础数据库 lab_base(customers 100 万行 + orders N 行)。
各实验通过 CREATE DATABASE ... TEMPLATE lab_base 复制一份,互不污染。
用法: python3 00_build_base.py [new|old]   (old = 在 PostgreSQL 16 对照服务器上构建)
"""
import sys
import time

import common as c

which = sys.argv[1] if len(sys.argv) > 1 else "new"
port = c.PORT_OLD if which == "old" else c.PORT

conn = c.fresh_db("lab_base", port)
print("server", c.version(conn), "N =", c.N)
t = time.time()
c.q(conn, c.BASE_DDL)
c.q(conn, c.BASE_ROWS, {"n": c.N})
print("loaded in %.1fs" % (time.time() - t))
c.q(conn, "VACUUM (ANALYZE) customers")
c.q(conn, "VACUUM (ANALYZE) orders")
print("orders heap:", c.fmt_bytes(c.size(conn, "orders")),
      "| pk index:", c.fmt_bytes(c.index_size(conn, "orders_pkey")))
print(c.q(conn, "SELECT status, count(*), round(100.0*count(*)/sum(count(*)) over (),2) "
                "FROM orders GROUP BY 1 ORDER BY 2 DESC"))
conn.close()
print("done in %.1fs" % (time.time() - t))
