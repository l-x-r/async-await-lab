"""R02 timestamptz / R04 numeric / R05 identity / R15 UNIQUE 与 NULL —— 语义类实验(确定性,不涉及耗时)。"""
import psycopg2

import common as c


def r02():
    conn = c.fresh_db("lab_r02")
    c.q(conn, "CREATE TABLE ev (ts timestamp, tstz timestamptz)")
    c.q(conn, "SET TimeZone = 'UTC'")
    # 同一个带偏移的字面量(东京 12:00)写入两种列
    c.q(conn, "INSERT INTO ev VALUES ('2026-03-08 12:00:00+09', '2026-03-08 12:00:00+09')")
    in_utc = c.q(conn, "SELECT ts::text, tstz::text FROM ev")[0]
    c.q(conn, "SET TimeZone = 'Asia/Tokyo'")
    in_tokyo = c.q(conn, "SELECT ts::text, tstz::text FROM ev")[0]
    # 两列"本应表示同一时刻",在不同会话时区下的比较结果
    c.q(conn, "SET TimeZone = 'UTC'")
    eq_utc = c.q1(conn, "SELECT ts = tstz FROM ev")
    c.q(conn, "SET TimeZone = 'Asia/Tokyo'")
    eq_tokyo = c.q1(conn, "SELECT ts = tstz FROM ev")
    # 夏令时:纽约 3 月 8 日 2 点跳变。+ interval '1 day' 与 + interval '24 hours' 对 timestamptz 的差异
    c.q(conn, "SET TimeZone = 'America/New_York'")
    dst = c.q(conn, """SELECT (timestamptz '2026-03-07 12:00' + interval '1 day')::text,
                              (timestamptz '2026-03-07 12:00' + interval '24 hours')::text""")[0]
    out = {
        "rule": "R02",
        "server": c.version(conn),
        "insert_literal": "2026-03-08 12:00:00+09 (东京中午12点)",
        "session_UTC": {"timestamp列": in_utc[0], "timestamptz列": in_utc[1]},
        "session_Tokyo": {"timestamp列": in_tokyo[0], "timestamptz列": in_tokyo[1]},
        "ts_equals_tstz_when_session_UTC": eq_utc,
        "ts_equals_tstz_when_session_Tokyo": eq_tokyo,
        "dst_newyork_plus_1day_vs_24h": {"+1 day": dst[0], "+24 hours": dst[1]},
    }
    conn.close()
    c.drop_db("lab_r02")
    return out


def r04():
    conn = c.fresh_db("lab_r04")
    f = c.q1(conn, "SELECT sum(0.1::float8) FROM generate_series(1, 1000000)")
    n = c.q1(conn, "SELECT sum(0.1::numeric) FROM generate_series(1, 1000000)")
    out = {
        "rule": "R04",
        "server": c.version(conn),
        "float8_sum_of_1e6_x_0.1": repr(f),
        "numeric_sum_of_1e6_x_0.1": str(n),
        "float8_0.1+0.2==0.3": c.q1(conn, "SELECT 0.1::float8 + 0.2::float8 = 0.3::float8"),
        "numeric_0.1+0.2==0.3": c.q1(conn, "SELECT 0.1::numeric + 0.2::numeric = 0.3::numeric"),
    }
    conn.close()
    c.drop_db("lab_r04")
    return out


def r05():
    conn = c.fresh_db("lab_r05")
    c.q(conn, "DROP ROLE IF EXISTS lab_writer")
    c.q(conn, "CREATE ROLE lab_writer")
    c.q(conn, """
        CREATE TABLE t_serial (id serial PRIMARY KEY, v int);
        CREATE TABLE t_ident  (id int GENERATED ALWAYS AS IDENTITY PRIMARY KEY, v int);
        GRANT INSERT ON t_serial, t_ident TO lab_writer;
    """)
    res = {}
    for t in ("t_serial", "t_ident"):
        c.q(conn, "SET ROLE lab_writer")
        try:
            c.q(conn, f"INSERT INTO {t} (v) VALUES (1)")
            res[t] = "OK"
        except psycopg2.Error as e:
            res[t] = "ERROR: " + e.pgerror.strip().splitlines()[0]
        finally:
            c.q(conn, "RESET ROLE")
    # 手工覆盖 id
    manual = {}
    for t in ("t_serial", "t_ident"):
        try:
            c.q(conn, f"INSERT INTO {t} (id, v) VALUES (999, 2)")
            manual[t] = "OK(允许手工指定 id)"
        except psycopg2.Error as e:
            manual[t] = "ERROR: " + e.pgerror.strip().splitlines()[0]
    out = {
        "rule": "R05",
        "server": c.version(conn),
        "role_with_INSERT_only": res,
        "explicit_id_insert": manual,
    }
    c.q(conn, "DROP OWNED BY lab_writer; DROP ROLE lab_writer")
    conn.close()
    c.drop_db("lab_r05")
    return out


def r15():
    conn = c.fresh_db("lab_r15")
    c.q(conn, """
        CREATE TABLE u_default (a int UNIQUE);
        CREATE TABLE u_nnd     (a int UNIQUE NULLS NOT DISTINCT);
    """)
    res = {}
    for t in ("u_default", "u_nnd"):
        c.q(conn, f"INSERT INTO {t} VALUES (NULL)")
        try:
            c.q(conn, f"INSERT INTO {t} VALUES (NULL)")
            res[t] = "第二个 NULL:允许"
        except psycopg2.Error as e:
            res[t] = "第二个 NULL:拒绝 -> " + e.pgerror.strip().splitlines()[0]
    # ON CONFLICT 与部分唯一索引
    c.q(conn, """
        CREATE TABLE acct (email text, deleted_at timestamptz);
        CREATE UNIQUE INDEX acct_email_active_uq ON acct (email) WHERE deleted_at IS NULL;
        INSERT INTO acct VALUES ('a@x.com', NULL);
    """)
    conflict = {}
    for label, sql in {
        "ON CONFLICT (email) —— 不写谓词": "INSERT INTO acct VALUES ('a@x.com', NULL) ON CONFLICT (email) DO NOTHING",
        "ON CONFLICT (email) WHERE deleted_at IS NULL —— 带谓词": "INSERT INTO acct VALUES ('a@x.com', NULL) ON CONFLICT (email) WHERE deleted_at IS NULL DO NOTHING",
    }.items():
        try:
            c.q(conn, sql)
            conflict[label] = "OK"
        except psycopg2.Error as e:
            conflict[label] = "ERROR: " + e.pgerror.strip().splitlines()[0]
    out = {"rule": "R15", "server": c.version(conn), "unique_null": res, "on_conflict_partial_index": conflict}
    conn.close()
    c.drop_db("lab_r15")
    return out


if __name__ == "__main__":
    for fn in (r02, r04, r05, r15):
        r = fn()
        print(r)
        c.save(r["rule"], r)
