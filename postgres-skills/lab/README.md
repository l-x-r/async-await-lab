# PostgreSQL 表结构设计手册 — 复现脚本

页面:`../handbook/index.html`(由 `build_page.py` 从 `results/*.json` 生成)。

## 环境
- PostgreSQL 18(实验用 18.6),版本对照用 16.15(`LAB_PGPORT_OLD`)。
- Python 3 + `psycopg2-binary`;`pgbench`、`psql` 在 PATH 中;可选:`npm i squawk-cli`。
- 环境变量:`LAB_PGHOST`(默认 /tmp)、`LAB_PGPORT`(54318)、`LAB_PGPORT_OLD`(54316)、`LAB_PGUSER`(postgres)、`LAB_N`(基础表行数,默认 10,000,000)。
- 实验机:2 核 / 8GB / 本地盘;`shared_buffers=256MB`、`random_page_cost=1.1`、`fsync=off`。R10 检查需要 `shared_preload_libraries='pg_stat_statements'`。
- **绝对耗时不能外推到托管云实例,只有相对关系可参考。** 请在自己的实例上复测。

## 运行顺序
1. `python3 00_build_base.py new`(PG16 用 `old`)— 建 lab_base:customers 100 万行、orders `LAB_N` 行(约 45 秒)。
2. `python3 exp_semantics.py`(R02/R04/R05/R15) · `python3 exp_index.py`(R01/R07/R08/R09/R10) · `python3 exp_types.py r03 r06 r11` · `python3 exp_migrate.py`(R12/R13/R14,不要与其他重负载并发)。
3. `python3 checks.py` — 自测:故意违规库必须报出,合规库必须为空;导出 `checks.sql`。
4. `python3 validate_sql.py` — 在真实库上执行页面上的全部 SQL。
5. `npx squawk-cli migrations/bad.sql`(应报 adding-not-nullable-field / adding-field-with-default / require-concurrent-index-creation),`migrations/good.sql` 不应触发这三条。
6. `python3 build_page.py` — 用你的结果重新生成页面。

## 文件
- `predictions.md`:实验前写下的预测(在运行任何实验之前)。
- `results/*.json`:原始结果(每次重复的耗时、缓冲区、字节数、锁模式、停顿)。
- `common.py`:连接、EXPLAIN 解析、并发探针、pg_locks 轮询、模板库复制。
- `rules.py`:15 条规则内容,数字从 results 读取。

## 已知局限
见页面“未验证 / 待核实”一节。
