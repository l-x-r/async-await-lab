-- 每条规则的检查查询。对任意库运行:psql -d yourdb -f checks.sql

-- R01: 外键列没有以它为前导的索引
SELECT c.conrelid::regclass AS table_name, c.conname AS foreign_key
FROM pg_constraint c
WHERE c.contype = 'f'
  AND NOT EXISTS (
    SELECT 1 FROM pg_index i
    WHERE i.indrelid = c.conrelid
      AND (string_to_array(i.indkey::text, ' ')::int2[])[1:array_length(c.conkey, 1)] = c.conkey);

-- R02: 使用了 timestamp / timetz,或给 timestamptz 指定了精度
SELECT a.attrelid::regclass AS table_name, a.attname AS column_name, format_type(a.atttypid, a.atttypmod) AS type
FROM pg_attribute a JOIN pg_class cl ON cl.oid = a.attrelid JOIN pg_namespace n ON n.oid = cl.relnamespace
WHERE a.attnum > 0 AND NOT a.attisdropped AND 
  cl.relkind IN ('r','p') AND n.nspname NOT IN ('pg_catalog','information_schema','pg_toast')

  AND (a.atttypid IN ('timestamp'::regtype, 'timetz'::regtype)
       OR (a.atttypid = 'timestamptz'::regtype AND a.atttypmod <> -1));

-- R03: 使用了 char(n) / varchar(n)
SELECT a.attrelid::regclass AS table_name, a.attname AS column_name, format_type(a.atttypid, a.atttypmod) AS type
FROM pg_attribute a JOIN pg_class cl ON cl.oid = a.attrelid JOIN pg_namespace n ON n.oid = cl.relnamespace
WHERE a.attnum > 0 AND NOT a.attisdropped AND 
  cl.relkind IN ('r','p') AND n.nspname NOT IN ('pg_catalog','information_schema','pg_toast')

  AND a.atttypid IN ('bpchar'::regtype, 'varchar'::regtype) AND a.atttypmod <> -1;

-- R04: 金额类列使用了 money 或浮点数
SELECT a.attrelid::regclass AS table_name, a.attname AS column_name, format_type(a.atttypid, a.atttypmod) AS type
FROM pg_attribute a JOIN pg_class cl ON cl.oid = a.attrelid JOIN pg_namespace n ON n.oid = cl.relnamespace
WHERE a.attnum > 0 AND NOT a.attisdropped AND 
  cl.relkind IN ('r','p') AND n.nspname NOT IN ('pg_catalog','information_schema','pg_toast')

  AND (a.atttypid = 'money'::regtype
       OR (a.atttypid IN ('float4'::regtype, 'float8'::regtype)
           AND a.attname ~* '(price|amount|total|balance|cost|fee|salary|money)'));

-- R05: 仍在使用 serial(默认值为 nextval 的非 identity 列)
SELECT a.attrelid::regclass AS table_name, a.attname AS column_name, pg_get_expr(d.adbin, d.adrelid) AS default_expr
FROM pg_attribute a JOIN pg_class cl ON cl.oid = a.attrelid JOIN pg_namespace n ON n.oid = cl.relnamespace
JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
WHERE a.attnum > 0 AND NOT a.attisdropped AND 
  cl.relkind IN ('r','p') AND n.nspname NOT IN ('pg_catalog','information_schema','pg_toast')

  AND a.attidentity = '' AND pg_get_expr(d.adbin, d.adrelid) LIKE 'nextval(%';

-- R06: 主键是 uuid 且默认值为随机 UUID(v4)
SELECT a.attrelid::regclass AS table_name, a.attname AS column_name, pg_get_expr(d.adbin, d.adrelid) AS default_expr
FROM pg_index i
JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey)
JOIN pg_class cl ON cl.oid = a.attrelid JOIN pg_namespace n ON n.oid = cl.relnamespace
JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
WHERE i.indisprimary AND a.atttypid = 'uuid'::regtype AND 
  cl.relkind IN ('r','p') AND n.nspname NOT IN ('pg_catalog','information_schema','pg_toast')

  AND pg_get_expr(d.adbin, d.adrelid) ~ '(gen_random_uuid|uuidv4|uuid_generate_v4)';

-- R07: 多列 B-tree 索引的前导列只有少量取值(≤200),需复核列顺序;PG18 起此类索引可被 skip scan 利用
SELECT i.indexrelid::regclass AS index_name, i.indrelid::regclass AS table_name, a.attname AS leading_column, s.n_distinct
FROM pg_index i
JOIN pg_class ic ON ic.oid = i.indexrelid
JOIN pg_am am ON am.oid = ic.relam AND am.amname = 'btree'
JOIN pg_class tc ON tc.oid = i.indrelid
JOIN pg_namespace n ON n.oid = tc.relnamespace
JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = i.indkey[0]
JOIN pg_stats s ON s.schemaname = n.nspname AND s.tablename = tc.relname AND s.attname = a.attname
WHERE n.nspname NOT IN ('pg_catalog','information_schema','pg_toast') AND i.indnkeyatts > 1 AND s.n_distinct BETWEEN 1 AND 200;

-- R08: 在分布高度倾斜的列上建了全量索引,可考虑部分索引
SELECT i.indexrelid::regclass AS index_name, i.indrelid::regclass AS table_name, a.attname AS leading_column,
       round((s.most_common_freqs)[1]::numeric, 3) AS top_value_share,
       pg_size_pretty(pg_relation_size(i.indexrelid)) AS index_size
FROM pg_index i
JOIN pg_class tc ON tc.oid = i.indrelid JOIN pg_namespace n ON n.oid = tc.relnamespace
JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = i.indkey[0]
JOIN pg_stats s ON s.schemaname = n.nspname AND s.tablename = tc.relname AND s.attname = a.attname
WHERE n.nspname NOT IN ('pg_catalog','information_schema','pg_toast') AND i.indpred IS NULL AND NOT i.indisprimary AND NOT i.indisunique
  AND (s.most_common_freqs)[1] > 0.9;

-- R09: 带 INCLUDE 的覆盖索引所在表,可见性图覆盖率低(Index Only Scan 会退化)
SELECT i.indexrelid::regclass AS index_name, tc.oid::regclass AS table_name,
       tc.relallvisible AS all_visible_pages, tc.relpages AS pages,
       round(tc.relallvisible::numeric / tc.relpages, 2) AS visible_ratio
FROM pg_index i JOIN pg_class tc ON tc.oid = i.indrelid
WHERE i.indnatts > i.indnkeyatts AND tc.relpages > 0
  AND tc.relallvisible::numeric / tc.relpages < 0.9;

-- R10: pg_stat_statements 中平均耗时 > 10ms 且使用了 OFFSET 的查询(需先启用 pg_stat_statements)
SELECT round(mean_exec_time::numeric, 1) AS mean_ms, calls, left(query, 100) AS query
FROM pg_stat_statements
WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
  AND query ~* '\moffset\M' AND mean_exec_time > 10 AND query !~* 'pg_stat_statements'
ORDER BY mean_exec_time DESC;

-- R11: 使用了 Hash 索引(需确认确有收益,且不需要唯一性 / 范围 / 排序)
SELECT i.indexrelid::regclass AS index_name, i.indrelid::regclass AS table_name,
       pg_size_pretty(pg_relation_size(i.indexrelid)) AS index_size
FROM pg_index i JOIN pg_class ic ON ic.oid = i.indexrelid JOIN pg_am am ON am.oid = ic.relam
WHERE am.amname = 'hash';

-- R12: 可空但统计显示从无 NULL 的列,可评估补上 NOT NULL(用 NOT VALID 在线添加)
SELECT a.attrelid::regclass AS table_name, a.attname AS column_name, s.null_frac
FROM pg_attribute a JOIN pg_class cl ON cl.oid = a.attrelid JOIN pg_namespace n ON n.oid = cl.relnamespace
JOIN pg_stats s ON s.schemaname = n.nspname AND s.tablename = cl.relname AND s.attname = a.attname
WHERE a.attnum > 0 AND NOT a.attisdropped AND NOT a.attnotnull AND 
  cl.relkind IN ('r','p') AND n.nspname NOT IN ('pg_catalog','information_schema','pg_toast')

  AND s.null_frac = 0 AND cl.reltuples > 0;

-- R13: (无法用目录查询检查)迁移脚本里为大表加列时使用了易变默认值 —— 用迁移 linter(Squawk)检查迁移文件,见 README
SELECT 'run: squawk migrations/*.sql' AS how_to_check WHERE false;

-- R14: 失败的 CREATE INDEX CONCURRENTLY 留下的无效索引
SELECT i.indexrelid::regclass AS index_name, i.indrelid::regclass AS table_name
FROM pg_index i WHERE NOT i.indisvalid;

-- R15: 唯一索引涉及可空列但允许多个 NULL(未使用 NULLS NOT DISTINCT)
SELECT i.indexrelid::regclass AS index_name, i.indrelid::regclass AS table_name
FROM pg_index i JOIN pg_class tc ON tc.oid = i.indrelid JOIN pg_namespace n ON n.oid = tc.relnamespace
WHERE i.indisunique AND NOT i.indisprimary AND NOT i.indnullsnotdistinct AND 
  tc.relkind IN ('r','p') AND n.nspname NOT IN ('pg_catalog','information_schema','pg_toast')

  AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = i.indrelid
              AND a.attnum = ANY ((string_to_array(i.indkey::text, ' ')::int2[])[1:i.indnkeyatts])
              AND NOT a.attnotnull);

