"""15 条规则的内容。数字全部从 results/*.json 读取,页面不手写测量值。
每条规则的 demo_sql 是页面上展示的 SQL,validate_sql.py 会在真实 PG18 上逐块执行,保证它们能跑。
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DOC = "https://www.postgresql.org/docs/18/"


def R(name):
    with open(os.path.join(HERE, "results", f"{name}.json"), encoding="utf-8") as f:
        return json.load(f)


def mb(b):
    return f"{b / 1048576:,.0f} MB"


def ms(x):
    return f"{x:,.0f} ms" if x >= 10 else f"{x:.2f} ms"


def build_rules():
    r01, r03, r06, r07, r08, r09, r10, r11, r12, r13, r14, r15 = (
        R(n) for n in ("R01", "R03", "R06", "R07", "R08", "R09", "R10", "R11", "R12", "R13", "R14", "R15"))
    r02, r04, r05 = R("R02"), R("R04"), R("R05")

    def med(a):
        s = sorted(a)
        return s[len(s) // 2]

    v6 = r06["variants"]
    ins = {k: med(v["insert_s"]) for k, v in v6.items()}
    wal = {k: med(v["wal_bytes"]) for k, v in v6.items()}
    idx6 = {k: v["index_bytes"] for k, v in v6.items()}
    rd6 = {k: med(v["blks_read"]) for k, v in v6.items()}
    kb, k7, k4 = "bigint identity", "uuid v7 (uuidv7())", "uuid v4 (uuidv4())"

    r3 = r03["variants"]
    r11v = r11["variants"]
    v18 = r07["versions"]["PostgreSQL 18"]["variants"]
    v16 = r07["versions"]["PostgreSQL 16"]["variants"]
    qB = "B_仅时间范围(缺前导列status)"
    qC = "C_仅时间范围(前导列customer_id有90万个取值)"
    sc18, sc16 = v18["(status, created_at)"]["queries"], v16["(status, created_at)"]["queries"]
    cu18, cu16 = v18["(customer_id, created_at)"]["queries"], v16["(customer_id, created_at)"]["queries"]
    bufs = lambda q: q["shared_hit"] + q["shared_read"]

    r8 = r08["variants"]
    full8, part8 = r8["全量复合索引 (status, created_at)"], r8["部分索引 (created_at) WHERE status='pending'"]
    r9 = r09["steps"]
    a9, b9 = r9["覆盖索引,VACUUM 之后"], r9["覆盖索引,大量 UPDATE 之后、VACUUM 之前"]
    off, key = r10["offset"], r10["keyset"]
    A12 = r12["A_直接_SET_NOT_NULL"]
    B12 = r12["B_CHECK_NOT_VALID四步法"]
    C12 = r12["C_PG18原生NOT_NULL_NOT_VALID"]
    c13 = {k: v for k, v in r13.items() if isinstance(v, dict)}
    k13 = list(c13)
    pi, ci = r14["普通 CREATE INDEX"], r14["CREATE INDEX CONCURRENTLY"]

    rules = []

    rules.append(dict(
        id="R01", title="外键列必须建索引", group="键与约束",
        rule="每个外键的引用列(子表一侧)都要有以它为前导的索引;PostgreSQL 不会自动创建。",
        premise="所有版本;子表 ≥ 百万行时影响最大。读写比极端偏写、且从不删除/更新父行的表可以例外。",
        evidence=[
            ("doc", f"{DOC}ddl-constraints.html#DDL-CONSTRAINTS-FK", "官方文档明确:引用列不会自动建索引,删除/更新父行时需要在子表中查找引用行。"),
            ("exp", f"实验(PG18.6,子表 {r01['rows']:,} 行):删除一个没有子行的父行,无索引中位数 {ms(r01['median_without_ms'])},有索引 {ms(r01['median_with_ms'])}(6 次,"
                    f"无索引范围 {ms(min(r01['delete_unreferenced_parent_ms_without_index']))}–{ms(max(r01['delete_unreferenced_parent_ms_without_index']))})。"),
        ],
        cost=f"索引 {r01['index_size']},建索引 {r01['index_build_s']} 秒;批量写入子表 20 万行的中位数 {ms(med(r01['insert_200k_ms_without_index']))} → {ms(med(r01['insert_200k_ms_with_index']))}(每个索引都会增加写入成本)。",
        counter="子表很小(< 数万行);父表是只增不删的字典表;已有以外键列开头的复合索引时无需另建(检查查询已考虑这一点)。",
        demo_sql="""CREATE TABLE r01_parent (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY);
CREATE TABLE r01_child (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  parent_id bigint NOT NULL REFERENCES r01_parent (id)
);
CREATE INDEX ON r01_child (parent_id);""",
        disagree="四个 Skill 都认同,无分歧。倍率(约 %s 倍)受缓存与硬件影响很大,此处只证明量级,不要当作固定值。" % f"{r01['speedup_x']:,}",
    ))

    rules.append(dict(
        id="R02", title="时间点用 timestamptz,不用 timestamp", group="数据类型",
        rule="表示“某个时刻”的列一律用 timestamptz;不要给 timestamptz 指定精度;避免 timetz。",
        premise="所有版本、所有负载。timestamp 仅适用于“墙上时钟时间、与时区无关”的业务值(如固定的营业时间)。",
        evidence=[
            ("doc", "https://wiki.postgresql.org/wiki/Don't_Do_This", "官方 wiki《Don't Do This》:不要用 timestamp(无时区)来表示时刻。"),
            ("doc", f"{DOC}datatype-datetime.html", "官方文档:timestamptz 以 UTC 存储,按会话时区显示。"),
            ("exp", f"实验:同一个字面量 {r02['insert_literal']} 写入两种列。会话为 UTC 时 timestamp 列读出 {r02['session_UTC']['timestamp列']},timestamptz 读出 {r02['session_UTC']['timestamptz列']};"
                    f"会话为东京时两者读出相同——timestamp 列丢掉了时区信息,结果随会话变化的是 timestamptz 的显示而非时刻本身。纽约夏令时切换日:+1 day = {r02['dst_newyork_plus_1day_vs_24h']['+1 day']},+24 hours = {r02['dst_newyork_plus_1day_vs_24h']['+24 hours']}。"),
        ],
        cost="无额外存储成本(两者都是 8 字节)。需要在应用层明确会话时区。",
        counter="只表示“日历日期/每日固定营业时间”的列可以用 date / time / timestamp。",
        demo_sql="""CREATE TABLE r02_events (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  happened_at timestamptz NOT NULL DEFAULT now()
);""",
        disagree="无分歧。预测命中(语义演示)。",
    ))

    rules.append(dict(
        id="R03", title="字符串用 text(需要限长时加 CHECK),避免 char(n)", group="数据类型",
        rule="默认用 text;确需限长时用 CHECK (length(col) <= n) 而不是 varchar(n);不要用 char(n)。",
        premise="所有版本。若你的驱动/ORM 需要 varchar(n) 元数据,可保留 varchar(n),性能没有差别。",
        evidence=[
            ("doc", f"{DOC}datatype-character.html", "官方文档:text、varchar(n)、char(n) 在 PostgreSQL 中存储方式相同,char(n) 额外补空格,没有性能优势。"),
            ("exp", f"实验({r03['rows']:,} 行,点查 pgbench 5 轮):text 中位 {r3['text']['lookup']['tps_median']:,} TPS(范围 {r3['text']['lookup']['tps_min']:,}–{r3['text']['lookup']['tps_max']:,}),"
                    f"varchar(32) {r3['varchar(32)']['lookup']['tps_median']:,}(范围 {r3['varchar(32)']['lookup']['tps_min']:,}–{r3['varchar(32)']['lookup']['tps_max']:,});两者表/索引体积完全相同({mb(r3['text']['heap_bytes'])} / {mb(r3['text']['index_bytes'])})。"
                    f"char(32):表 {mb(r3['char(32)']['heap_bytes'])}(+{(r3['char(32)']['heap_bytes'] / r3['text']['heap_bytes'] - 1) * 100:.0f}%),索引 {mb(r3['char(32)']['index_bytes'])}(+{(r3['char(32)']['index_bytes'] / r3['text']['index_bytes'] - 1) * 100:.0f}%)。"),
            ("exp", "意外发现(预测没有想到):char(n) 列与 text 表达式比较(例如 col = md5(...))会被转成 ::text,无法使用 char(n) 上的索引,退化为顺序扫描——下面的 EXPLAIN 在 PG18 上实测。"),
        ],
        cost="改限长时 CHECK 约束可以 NOT VALID 在线添加;varchar(n) 缩小长度的重写行为属文档层面说法,本手册未验证。",
        counter="与遗留系统/外部 schema 对接且要求 char(n) 的场景。",
        demo_sql="""CREATE TABLE r03_users (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  email text NOT NULL CHECK (length(email) <= 254)
);
-- char(n) 与 text 比较:不能走索引(Seq Scan),显式转换为 char(n) 才行
CREATE TABLE r03_c (code char(32) PRIMARY KEY);
INSERT INTO r03_c SELECT md5(g::text) FROM generate_series(1, 20000) g;
ANALYZE r03_c;
EXPLAIN SELECT 1 FROM r03_c WHERE code = md5('5');
EXPLAIN SELECT 1 FROM r03_c WHERE code = md5('5')::char(32);""",
        disagree="supabase / wshobson / neon / ECC 一致主张 text。官方文档同样认为 varchar(n) 无性能收益——但这一点实测只在“相同长度语义”下成立,见上面 char(n) 的索引陷阱。",
    ))

    rules.append(dict(
        id="R04", title="金额用 numeric,不用 float / money", group="数据类型",
        rule="金额、费率等需要精确十进制的列用 numeric(p,s);不要用 real / double precision;不要用 money。",
        premise="所有版本。科学计算、传感器等本身有测量误差的数据可以用 float8。",
        evidence=[
            ("doc", "https://wiki.postgresql.org/wiki/Don't_Do_This", "官方 wiki:不要用 money;浮点数不适合存储精确金额。"),
            ("exp", f"实验:100 万个 0.1 求和,float8 = {r04['float8_sum_of_1e6_x_0.1']},numeric = {r04['numeric_sum_of_1e6_x_0.1']};0.1+0.2=0.3 在 float8 为 {r04['float8_0.1+0.2==0.3']},numeric 为 {r04['numeric_0.1+0.2==0.3']}。"),
        ],
        cost="numeric 比 float8 占更多空间、运算更慢(本手册未测量,属于文档层面的常识,标为“未验证”)。",
        counter="聚合分析、机器学习特征等不要求精确十进制语义的列。",
        demo_sql="""CREATE TABLE r04_payments (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  amount numeric(12,2) NOT NULL CHECK (amount >= 0)
);
SELECT sum(0.1::float8) AS float_sum, sum(0.1::numeric) AS numeric_sum FROM generate_series(1, 1000000);""",
        disagree="无分歧。",
    ))

    rules.append(dict(
        id="R05", title="自增主键用 identity,不用 serial", group="键与约束",
        rule="用 GENERATED ALWAYS AS IDENTITY(或 BY DEFAULT);不要用 serial / bigserial。",
        premise="PostgreSQL 10+。",
        evidence=[
            ("doc", "https://wiki.postgresql.org/wiki/Don't_Do_This", "官方 wiki 推荐用 identity 代替 serial(序列归属与权限更清晰)。"),
            ("exp", f"实验:只授予 INSERT 权限的角色——serial 表:{r05['role_with_INSERT_only']['t_serial'].replace('ERROR: ', '')};identity 表:{r05['role_with_INSERT_only']['t_ident']}。"
                    f"显式指定 id:serial {r05['explicit_id_insert']['t_serial']};identity(ALWAYS)被拒绝(防止手工值与序列冲突)。"),
        ],
        cost="迁移旧表时需要把 serial 转换为 identity(需要排他锁,计划维护窗口)。",
        counter="BY DEFAULT 允许手工指定 id,适用于数据导入场景。",
        demo_sql="""CREATE TABLE r05_items (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  name text NOT NULL
);""",
        disagree="wshobson 推荐 identity,neon 明确写 “Avoid serial / bigserial”,与官方 wiki 和实验一致,无分歧。",
    ))

    rules.append(dict(
        id="R06", title="大表主键:bigint identity 或 UUIDv7,避免随机 UUIDv4", group="键与约束",
        rule="亿级写入表默认 bigint identity;需要分布式生成/不可猜测 ID 时用 uuidv7();避免用 uuidv4 / gen_random_uuid() 作主键。",
        premise="PostgreSQL 18(原生 uuidv7());数据量大于 shared_buffers 时差距最明显。本次实验 shared_buffers=256MB,表索引总量远大于它。",
        evidence=[
            ("doc", f"{DOC}datatype-uuid.html", "PG18 文档:新增 uuidv7(),按时间有序生成;uuidv4() 为随机。"),
            ("exp", f"实验(PG18.6,{r06['rows']:,} 行插入,3 轮中位数):插入耗时 bigint {ins[kb]:.1f}s / uuidv7 {ins[k7]:.1f}s / uuidv4 {ins[k4]:.1f}s(uuidv4 = uuidv7 的 {ins[k4] / ins[k7]:.1f} 倍);"
                    f"WAL {mb(wal[kb])} / {mb(wal[k7])} / {mb(wal[k4])};主键索引 {mb(idx6[kb])} / {mb(idx6[k7])} / {mb(idx6[k4])}(uuidv4 比 uuidv7 大 {(idx6[k4] / idx6[k7] - 1) * 100:.0f}%);"
                    f"磁盘读块数 {rd6[kb]:.0f} / {rd6[k7]:.0f} / {rd6[k4]:.0f}。"),
        ],
        cost=f"uuid 是 16 字节,即便是 v7 也比 bigint 慢:本实验 uuidv7 比 bigint 慢 {ins[k7] / ins[kb]:.1f} 倍,索引大 {(idx6[k7] / idx6[kb] - 1) * 100:.0f}%(含 uuidv7() 的生成开销)。",
        counter="外部可见的 ID 需要不可猜测时,uuidv7 的时间前缀会泄露创建时间(它本身并不是密钥)。表小于内存时 v4 的惩罚不明显。",
        demo_sql="""CREATE TABLE r06_events (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  payload text
);
SELECT uuidv7() < uuidv7() AS ordered;""",
        disagree="supabase 与 neon 明确反对 UUIDv4 主键;wshobson 允许 gen_random_uuid() 作为备选。实测支持前者(耗时 2 倍以上)。",
    ))

    rules.append(dict(
        id="R07", title="复合索引:等值列在前、范围列在后——但 PG18 不再是绝对的“最左前缀”", group="索引",
        rule="多列索引默认把等值条件的列放前面、范围/排序列放后面;PG18 起,前导列取值很少(几十个以内)时,缺失前导列的查询也能借助 skip scan 使用该索引。",
        premise="PostgreSQL 18。PG17 及以前按旧的最左前缀行为。前导列取值很多时 skip scan 无益。",
        evidence=[
            ("doc", f"{DOC}release-18.html", "PG18 Release Notes:B-tree 多列索引支持 skip scan。"),
            ("doc", f"{DOC}indexes-multicolumn.html", "官方文档:多列索引的前导列条件对效率影响最大。"),
            ("exp", f"实验({r07['rows']:,} 行;status 5 个取值,customer_id 约 90 万个取值):索引 (status, created_at),只按时间范围查询(缺 status):"
                    f"PG18 {ms(sc18[qB]['ms_median'])}、{bufs(sc18[qB])} 个缓冲区、{sc18[qB]['index_searches']} 次索引搜索;PG16 {ms(sc16[qB]['ms_median'])}、{bufs(sc16[qB])} 个缓冲区。"
                    f"缓冲区减少 {bufs(sc16[qB]) / bufs(sc18[qB]):.0f} 倍(时间差受两个版本构建不同影响,以缓冲区为准)。"),
            ("exp", f"前导列取值多时无效:索引 (customer_id, created_at),只按时间查询,PG18 仍需 {bufs(cu18[qB]):,} 个缓冲区,与 PG16 的 {bufs(cu16[qB]):,} 相同。"),
        ],
        cost=f"索引体积约 {mb(v18['(status, created_at)']['index_bytes'])};把范围列放前面 (created_at, status) 对“等值+范围”查询略慢({ms(v18['(created_at, status)']['queries']['A_status等值+时间范围']['ms_median'])} vs {ms(sc18['A_status等值+时间范围']['ms_median'])})。",
        counter="skip scan 的搜索次数 ≈ 前导列的取值个数;取值个数多时反而不如重新设计索引。",
        demo_sql="""CREATE TABLE r07_orders (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, status text NOT NULL, created_at timestamptz NOT NULL);
INSERT INTO r07_orders (status, created_at)
SELECT (ARRAY['new','paid','shipped','done','cancelled'])[1 + g % 5], now() - (g || ' seconds')::interval
FROM generate_series(1, 200000) g;
CREATE INDEX r07_idx ON r07_orders (status, created_at);
ANALYZE r07_orders;
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF, TIMING OFF)
SELECT count(*) FROM r07_orders WHERE created_at > now() - interval '1 hour';""",
        disagree="supabase / wshobson / neon 都写“最左前缀,缺前导列则索引不可用”(supabase query-composite-indexes.md 注释:“Does NOT work for: WHERE created_at > …”)。在 PG18 上这条已被官方特性修正:前导列取值少时可用。裁决:以 PG18 文档 + 实验为准,但仅对低基数前导列成立。",
    ))

    rules.append(dict(
        id="R08", title="只查少数状态的大表,用部分索引", group="索引",
        rule="当查询总是命中表中占比很小的一部分(如 status='pending'),用 CREATE INDEX … WHERE … 只索引这部分。",
        premise="任意版本。分布倾斜明显(目标值占比 < 约 1%)时收益最大。",
        evidence=[
            ("doc", f"{DOC}indexes-partial.html", "官方文档:部分索引缩小索引体积、减少维护成本;查询条件必须蕴含索引谓词。"),
            ("exp", f"实验({r08['rows']:,} 行,pending 占 0.4%):部分索引 {part8['index_bytes']:,} 字节 vs 全量复合索引 {mb(full8['index_bytes'])}(体积 {part8['index_bytes'] / full8['index_bytes'] * 100:.2f}%);"
                    f"取最早 50 条 pending:{ms(part8['q_top50']['ms_median'])} vs {ms(full8['q_top50']['ms_median'])};批量写入 30 万行中位数 {ms(med(part8['insert_300k_ms']))} vs {ms(med(full8['insert_300k_ms']))}(无索引基线 {ms(med(r8['无索引']['insert_300k_ms']))})。"),
        ],
        cost="查询必须携带与索引谓词一致的条件(绑定参数写法可能无法证明蕴含,需要用 EXPLAIN 确认)。",
        counter="目标值占比较高(> 10%)时部分索引收益有限。",
        demo_sql="""CREATE TABLE r08_jobs (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, status text NOT NULL, created_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX r08_pending ON r08_jobs (created_at) WHERE status = 'pending';""",
        disagree="四个 Skill 的文本里都出现了部分索引相关内容(grep 命中),无已知分歧。",
    ))

    rules.append(dict(
        id="R09", title="覆盖索引(INCLUDE)要配合 VACUUM,否则不省回表", group="索引",
        rule="想用 Index Only Scan 时,用 INCLUDE 放入被查询的非键列,并确保 autovacuum 能及时更新可见性图。",
        premise="任意版本。更新频繁的表需要调低 autovacuum 阈值。",
        evidence=[
            ("doc", f"{DOC}indexes-index-only-scans.html", "官方文档:Index Only Scan 只有在页面被标记为全可见时才能省去回表。"),
            ("exp", f"实验({r09['rows']:,} 行):普通索引 {ms(r9['普通索引(customer_id)']['ms_median'])};覆盖索引 VACUUM 后 {ms(a9['ms_median'])}(Heap Fetches {a9['heap_fetches']});"
                    f"大量 UPDATE 后、VACUUM 前 {ms(b9['ms_median'])}(Heap Fetches {b9['heap_fetches']:,}),基本退回到普通索引水平;再次 VACUUM 后恢复 {ms(r9['覆盖索引,再次 VACUUM 之后']['ms_median'])}。"),
        ],
        cost=f"覆盖索引体积 {mb(r09['index_bytes_cov'])},所有 INCLUDE 列都会重复存储;写入更重。",
        counter="表几乎只读或只追加(可见性图稳定)时收益最大;热点更新表几乎得不到收益。",
        demo_sql="""CREATE TABLE r09_orders (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, customer_id bigint NOT NULL, total numeric(12,2) NOT NULL);
INSERT INTO r09_orders (customer_id, total) SELECT g % 1000, g FROM generate_series(1, 100000) g;
CREATE INDEX r09_cov ON r09_orders (customer_id) INCLUDE (total);
VACUUM r09_orders;
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF, TIMING OFF) SELECT sum(total) FROM r09_orders WHERE customer_id = 7;""",
        disagree="wshobson / neon / ECC 的文本里提到 INCLUDE;在 grep 到的片段中没有看到它们说明“依赖可见性图”这一前提(未逐文通读,标为待核实)。这一前提来自官方文档与本实验。",
    ))

    rules.append(dict(
        id="R10", title="分页用游标(keyset),大偏移的 OFFSET 会线性变慢", group="查询",
        rule="深分页使用 WHERE (排序键) > 上一页最后一个值 ORDER BY 排序键 LIMIT n,而不是 OFFSET。",
        premise="任意版本。偏移量小(几百行内)时 OFFSET 没问题。",
        evidence=[
            ("doc", f"{DOC}queries-limit.html", "官方文档:使用 OFFSET 时,被跳过的行仍然要被计算出来。"),
            ("exp", f"实验({r10['rows']:,} 行,按主键排序取 20 行,5 次):OFFSET 0 / 10,000 / 100,000 / 1,000,000 / 5,000,000 → "
                    f"{ms(off['0']['ms_median'])} / {ms(off['10000']['ms_median'])} / {ms(off['100000']['ms_median'])} / {ms(off['1000000']['ms_median'])} / {ms(off['5000000']['ms_median'])};"
                    f"keyset 在同样位置恒为 {ms(key['0']['ms_median'])}–{ms(max(v['ms_median'] for v in key.values()))}。"),
        ],
        cost="keyset 不能随机跳页,排序键必须唯一(或加主键作为次级键);需要前端配合。",
        counter="后台管理、小数据量列表;需要总页数/跳页的场景。",
        demo_sql="""CREATE TABLE r10_posts (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, title text NOT NULL);
INSERT INTO r10_posts (title) SELECT 'p' || g FROM generate_series(1, 1000) g;
-- 第一页
SELECT id, title FROM r10_posts ORDER BY id LIMIT 20;
-- 下一页:用上一页最后的 id
SELECT id, title FROM r10_posts WHERE id > 20 ORDER BY id LIMIT 20;""",
        disagree="supabase 有专门的游标分页条目(data-pagination.md)。其余 Skill 未逐文核对,无已知分歧。",
    ))

    rules.append(dict(
        id="R11", title="Hash 索引:更小,点查略快,但不是默认选择", group="索引",
        rule="只做等值查询的长文本键,可以考虑 Hash 索引;需要唯一约束、范围或排序时用 B-tree。",
        premise="PostgreSQL 10+(Hash 索引已写 WAL)。",
        evidence=[
            ("doc", f"{DOC}indexes-types.html", "官方文档:Hash 索引只支持等值比较;不支持唯一约束。"),
            ("exp", f"实验({r11['rows']:,} 行,32 字符 md5 键,pgbench 5 轮):Hash {mb(r11v['Hash']['index_bytes'])} vs B-tree {mb(r11v['B-tree']['index_bytes'])}(体积 {r11v['Hash']['index_bytes'] / r11v['B-tree']['index_bytes'] * 100:.0f}%);"
                    f"点查 TPS 中位数 {r11v['Hash']['lookup']['tps_median']:,}(范围 {r11v['Hash']['lookup']['tps_min']:,}–{r11v['Hash']['lookup']['tps_max']:,}) vs {r11v['B-tree']['lookup']['tps_median']:,}"
                    f"({r11v['B-tree']['lookup']['tps_min']:,}–{r11v['B-tree']['lookup']['tps_max']:,})(Hash 高 {(r11v['Hash']['lookup']['tps_median'] / r11v['B-tree']['lookup']['tps_median'] - 1) * 100:.0f}%,两者范围不重叠,但差距很小)。"),
        ],
        cost=f"构建更慢({r11v['Hash']['build_s']}s vs {r11v['B-tree']['build_s']}s);不能用于 ORDER BY / 范围 / 唯一约束 / 多列。",
        counter="只有等值查询、键很长、内存紧张的场景。",
        demo_sql="""CREATE TABLE r11_kv (k text NOT NULL, v text);
CREATE INDEX r11_hash ON r11_kv USING hash (k);""",
        disagree="supabase 写“Hash 对等值略快于 B-tree”,wshobson / ECC 未提及 Hash 索引。实验:略快(约 +9%)且体积小一半,但收益不大,所以只作为可选项,不是通用规则。",
    ))

    rules.append(dict(
        id="R12", title="大表在线加 NOT NULL:先 NOT VALID,再 VALIDATE", group="在线迁移",
        rule="PG18:ADD CONSTRAINT … NOT NULL col NOT VALID → VALIDATE CONSTRAINT;PG17 及以前:CHECK (col IS NOT NULL) NOT VALID → VALIDATE → SET NOT NULL → DROP CHECK。",
        premise="大表(扫描时间超过你能容忍的写入停顿)。PG18 原生语法需要 18+。",
        evidence=[
            ("doc", f"{DOC}sql-altertable.html", "官方文档:SET NOT NULL 需要全表扫描;VALIDATE CONSTRAINT 只需要 SHARE UPDATE EXCLUSIVE 锁。"),
            ("exp", f"实验(PG18.6,{r12['rows']:,} 行,并发写入 + 读取探针,pg_locks 轮询):直接 SET NOT NULL 耗时 {ms(A12['duration_ms'])},持有 {'/'.join(A12['lock_modes_seen'])},"
                    f"写入探针最长停顿 {ms(A12['writer']['max_ms'])},读取探针最长停顿 {ms(A12['reader']['max_ms'])}。"
                    f"CHECK 四步法:VALIDATE 耗时 {ms(B12['2_VALIDATE']['duration_ms'])},只持有 {'/'.join(B12['2_VALIDATE']['lock_modes_seen'])},最长停顿写 {ms(B12['2_VALIDATE']['writer']['max_ms'])} / 读 {ms(B12['2_VALIDATE']['reader']['max_ms'])};"
                    f"PG18 原生:VALIDATE {ms(C12['2_VALIDATE']['duration_ms'])},写 {ms(C12['2_VALIDATE']['writer']['max_ms'])} / 读 {ms(C12['2_VALIDATE']['reader']['max_ms'])}。"),
        ],
        cost=f"本实验数据全部在内存缓存中,{r12['rows']:,} 行的直接做法只停顿约 1 秒——停顿与表大小、IO 速度成正比,冷数据/亿级表会按比例放大(未在本环境验证亿级)。",
        counter="表很小或可停机时,直接 SET NOT NULL 更简单。锁排队:任何需要 ACCESS EXCLUSIVE 的 DDL 前都应设置 lock_timeout。",
        demo_sql="""CREATE TABLE r12_t (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, note text);
INSERT INTO r12_t (note) SELECT 'x' FROM generate_series(1, 1000);
ALTER TABLE r12_t ADD CONSTRAINT r12_note_nn NOT NULL note NOT VALID;   -- PG18
ALTER TABLE r12_t VALIDATE CONSTRAINT r12_note_nn;""",
        disagree="wshobson / neon 提到 NOT VALID(针对 CHECK / 外键);PG18 原生 NOT NULL NOT VALID 在它们的文本中没有出现——属于新版本特性带来的更新。",
    ))

    rules.append(dict(
        id="R13", title="大表加列:只用常量/稳定默认值,不用易变默认值", group="在线迁移",
        rule="ADD COLUMN … DEFAULT 常量(或 STABLE 函数如 now())是瞬时的;DEFAULT 易变函数(clock_timestamp()、gen_random_uuid()、uuidv7())会重写整表。",
        premise="PostgreSQL 11+。",
        evidence=[
            ("doc", f"{DOC}sql-altertable.html", "官方文档:默认值非易变时无需重写表;易变默认值会重写。"),
            ("exp", f"实验(PG18.6,{r13['rows']:,} 行):DEFAULT 0 {ms(c13[k13[0]]['duration_ms'])},relfilenode 未变;"
                    f"DEFAULT clock_timestamp() {ms(c13[k13[1]]['duration_ms'])}(重写,写入探针最长停顿 {ms(c13[k13[1]]['writer']['max_ms'])});"
                    f"DEFAULT uuidv7() {ms(c13[k13[2]]['duration_ms'])}(重写,停顿 {ms(c13[k13[2]]['writer']['max_ms'])})。"),
            ("exp", "额外验证(1,000,000 行小表,relfilenode 对比):DEFAULT now() 不重写;DEFAULT gen_random_uuid() 重写——now() 是 STABLE 而非 VOLATILE。"),
        ],
        cost="易变默认值需要分批回填:先加可空列,再分批 UPDATE,最后加约束。",
        counter="可以接受停机的小表。",
        demo_sql="""CREATE TABLE r13_t (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY);
INSERT INTO r13_t SELECT FROM generate_series(1, 1000);
ALTER TABLE r13_t ADD COLUMN c_const int NOT NULL DEFAULT 0;
ALTER TABLE r13_t ADD COLUMN c_now timestamptz NOT NULL DEFAULT now();""",
        disagree="wshobson 的 details.md 写 “volatile defaults (e.g., now(), gen_random_uuid()) rewrite the table”。实测:now() 不重写(它是 STABLE),gen_random_uuid() 才重写。这一条的例子写错了一半,裁决:以官方文档 + 实验为准。",
    ))

    rules.append(dict(
        id="R14", title="线上建索引用 CREATE INDEX CONCURRENTLY,并检查无效索引", group="在线迁移",
        rule="在有写入的表上建索引用 CONCURRENTLY;失败后要清理 INVALID 索引。",
        premise="任意版本。CONCURRENTLY 不能放在事务块里。",
        evidence=[
            ("doc", f"{DOC}sql-createindex.html", "官方文档:普通 CREATE INDEX 阻塞写入;CONCURRENTLY 持 SHARE UPDATE EXCLUSIVE,失败会留下无效索引。"),
            ("exp", f"实验({r14['rows'] if 'rows' in r14 else '10,000,000'} 行,并发写/读探针):普通建索引 {ms(pi['duration_ms'])},持有 {'/'.join(pi['lock_modes_seen'])},写入最长停顿 {ms(pi['writer']['max_ms'])};"
                    f"CONCURRENTLY {ms(ci['duration_ms'])}(为普通方式的 {ci['duration_ms'] / pi['duration_ms']:.2f} 倍),持有 {'/'.join(ci['lock_modes_seen'])},写入最长停顿 {ms(ci['writer']['max_ms'])}。"),
        ],
        cost="耗时更长(本实验 1.4 倍),需要等待旧事务结束;失败会留下 INVALID 索引占用写入成本。",
        counter="新建的空表/可停机窗口,普通 CREATE INDEX 更快。",
        demo_sql="""CREATE TABLE r14_t (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, customer_id bigint);
CREATE INDEX CONCURRENTLY r14_idx ON r14_t (customer_id);""",
        disagree="supabase / wshobson / neon 的文本里都出现 CONCURRENTLY,无分歧。预测的“1.5–2 倍耗时”被实测(1.4 倍)略微否定。",
    ))

    rules.append(dict(
        id="R15", title="UNIQUE 与 NULL;ON CONFLICT 必须匹配索引", group="键与约束",
        rule="可空列上的唯一约束默认允许多个 NULL,需要“最多一个 NULL”时用 NULLS NOT DISTINCT(PG15+);ON CONFLICT 的目标要和唯一索引(含 WHERE 谓词)一致。",
        premise="PostgreSQL 15+。",
        evidence=[
            ("doc", f"{DOC}ddl-constraints.html#DDL-CONSTRAINTS-UNIQUE-CONSTRAINTS", "官方文档:唯一约束中 NULL 互不相等,可用 NULLS NOT DISTINCT 改变。"),
            ("doc", f"{DOC}sql-insert.html", "官方文档:ON CONFLICT 支持 index_predicate,用于推断部分唯一索引。"),
            ("exp", f"实验:默认唯一约束 {r15['unique_null']['u_default']};NULLS NOT DISTINCT {r15['unique_null']['u_nnd'].split(' -> ')[0]}。"
                    f"部分唯一索引 + ON CONFLICT:不写谓词 → 报错 “no unique or exclusion constraint matching”;带谓词 → {r15['on_conflict_partial_index']['ON CONFLICT (email) WHERE deleted_at IS NULL —— 带谓词']}。"),
        ],
        cost="NULLS NOT DISTINCT 只有 PG15+;部分唯一索引的 ON CONFLICT 必须重复写谓词。",
        counter="业务上确实允许多个 NULL 的列(如可选的唯一编号)。",
        demo_sql="""CREATE TABLE r15_users (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, email text, deleted_at timestamptz);
CREATE UNIQUE INDEX r15_email_live ON r15_users (email) WHERE deleted_at IS NULL;
INSERT INTO r15_users (email) VALUES ('a@x.com')
ON CONFLICT (email) WHERE deleted_at IS NULL DO NOTHING;
CREATE TABLE r15_nnd (a int UNIQUE NULLS NOT DISTINCT);""",
        disagree="wshobson 的 details.md 写 “partial indexes don't work” with ON CONFLICT。实测:不带谓词确实报错,带 WHERE 谓词可以工作。这是 Skill 与官方文档/实验的分歧,裁决:以实验为准——Skill 的说法对一半。",
    ))
    return rules
