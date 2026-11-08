"""验证页面上展示的每一段 SQL 都能在真实的 PostgreSQL 上执行(psql -v ON_ERROR_STOP=1)。
用法:python3 validate_sql.py   (使用 LAB_PGHOST / LAB_PGPORT / LAB_PGUSER)
"""
import os
import subprocess
import sys
import tempfile

import common as c
import rules
from checks import CHECKS

HOST, PORT, USER = os.environ.get("LAB_PGHOST", "/tmp"), os.environ.get("LAB_PGPORT", "54318"), os.environ.get("LAB_PGUSER", "postgres")


def psql(db, sql):
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False, encoding="utf-8") as f:
        f.write(sql)
    p = subprocess.run(["psql", "-h", HOST, "-p", PORT, "-U", USER, "-d", db, "-q", "-v", "ON_ERROR_STOP=1", "-f", f.name],
                       capture_output=True, text=True)
    os.unlink(f.name)
    return p.returncode, (p.stderr or "").strip()


def main():
    db = "lab_validate_sql"
    c.fresh_db(db).close() if False else None
    subprocess.run(["psql", "-h", HOST, "-p", PORT, "-U", USER, "-d", "postgres", "-qc", f"DROP DATABASE IF EXISTS {db}"], check=True)
    subprocess.run(["psql", "-h", HOST, "-p", PORT, "-U", USER, "-d", "postgres", "-qc", f"CREATE DATABASE {db}"], check=True)
    bad = 0
    for r in rules.build_rules():
        rc, err = psql(db, r["demo_sql"])
        print(r["id"], "demo_sql", "OK" if rc == 0 else "FAIL: " + err)
        bad += rc != 0
    for rid, (title, sql) in CHECKS.items():
        rc, err = psql(db, sql.strip() + ";") if rid != "R10" else (0, "")
        print(rid, "check", "OK" if rc == 0 else "FAIL: " + err)
        bad += rc != 0
    subprocess.run(["psql", "-h", HOST, "-p", PORT, "-U", USER, "-d", "postgres", "-qc", f"DROP DATABASE {db}"], check=True)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
