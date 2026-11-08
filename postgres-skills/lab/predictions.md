# 实验前预测(在运行任何实验之前写下)

写下时间:2026-11-08,先于所有实验运行。预测依据:官方文档、Release Notes、以及对 PostgreSQL 内部机制的理解。
实验结束后,页面会逐条对照"预测 vs 实测",预测错了也会如实展示。

环境:PostgreSQL 18.6(主),PostgreSQL 16.15(仅用于版本差异对照),2 核 / 8GB / 本地盘,
`shared_buffers=256MB`,`random_page_cost=1.1`,`fsync=off`。绝对耗时不能外推到云托管实例,只有"相对关系"可参考。

| 编号 | 规则 | 预测 |
|---|---|---|
| R01 | 外键列要建索引 | 1000 万行子表上,没有索引时删除一个"无子行"的父行要整表扫描,每次约 0.2–1.5 秒;有索引时 < 1 毫秒。倍率 ≥ 1000 倍。 |
| R02 | 用 timestamptz | 会话时区改变后,`timestamp` 列与 `timestamptz` 的比较结果会随之改变,而 `timestamptz` 表示的时刻不变。(语义演示,非性能。) |
| R03 | 用 text 而非 varchar(n)/char(n) | text 与 varchar(n) 的表/索引体积相同、查找耗时差异在噪声内(±5%);char(n) 因补空格体积更大。 |
| R04 | 金额用 numeric | 对 100 万个 0.1 做 float8 求和,结果不等于 100000;numeric 精确等于 100000。 |
| R05 | identity 优于 serial | 只有 INSERT 权限的角色往 serial 表插入会报序列权限不足;identity 表可以正常插入。 |
| R06 | 主键别用随机 UUID v4 | 同样行数下:v4 的插入更慢、索引更大、产生的 WAL 更多;v7 的表现接近 bigint identity。预测 v4 比 v7 慢 ≥ 1.5 倍,WAL ≥ 2 倍,索引大 30–60%。 |
| R07 | 复合索引列顺序 + PG18 skip scan | 等值列在前、范围列在后的 (status, created_at) 在两个版本上都最快。只按 created_at 查询时:PG16 退化为全索引扫描或顺序扫描(慢);PG18 在前导列只有几个取值时用 skip scan,明显更快(≥ 10 倍);前导列有 100 万个取值时,skip scan 帮不上忙。 |
| R08 | 部分索引 | 对只占 0.4% 行的 `status='pending'` 建部分索引,体积约为全量复合索引的 1% 以内;查询耗时差距不大(2 倍内),收益主要在体积和写入开销。 |
| R09 | 覆盖索引需要可见性图 | 大量更新后、VACUUM 前,Index Only Scan 的 Heap Fetches ≈ 命中行数(并不比普通索引扫描省);VACUUM 之后 Heap Fetches ≈ 0,共享缓冲区读取量下降 ≥ 5 倍。 |
| R10 | 游标分页优于 OFFSET | OFFSET 耗时随偏移量近似线性增长(偏移 500 万行时达秒级);游标分页耗时恒定、< 1 毫秒。 |
| R11 | Hash 索引并不比 B-tree 快(Skill 之间有分歧) | 36 字符文本键上:Hash 索引体积更小(约 B-tree 的 30–60%),点查吞吐与 B-tree 相差 ±15% 以内,没有决定性优势。 |
| R12 | 在线加 NOT NULL | 直接 `SET NOT NULL` 要在 ACCESS EXCLUSIVE 锁下全表扫描,并发读写被阻塞整个扫描期间(1000 万行约 1–3 秒);CHECK ... NOT VALID + VALIDATE 的做法不阻塞读写(只在毫秒级持锁)。PG18 原生 `NOT NULL ... NOT VALID` 同样不阻塞。 |
| R13 | 加列默认值的易变性 | 常量默认值:瞬时(< 50 毫秒),relfilenode 不变;易变默认值(`clock_timestamp()`、`uuidv7()`):重写整表(relfilenode 改变),1000 万行耗时 ≥ 10 秒,期间读写被阻塞。 |
| R14 | CREATE INDEX CONCURRENTLY | 普通 `CREATE INDEX` 在整个构建期间阻塞写入;CONCURRENTLY 不阻塞写入(停顿 < 100 毫秒),但总耗时约 1.5–2 倍以上。 |
| R15 | UNIQUE 与 NULL | 默认允许多个 NULL;`NULLS NOT DISTINCT` 拒绝第二个 NULL;`ON CONFLICT` 必须匹配完全一致的唯一索引。 |
