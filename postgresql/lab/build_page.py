"""从 results/*.json 与 rules.py 生成 ../index.html。运行:python3 build_page.py"""
import html
import json
import os
import re

import rules as RL
from checks import CHECKS

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "index.html")
CSS = os.path.join(HERE, "base.css")
e = html.escape

css = open(CSS, encoding="utf-8").read()
css += """
.rule{margin:18px 0}
.rule h3{font-size:18px}
.rule .oneline{font-size:15px;background:#eef3e9;border-left:4px solid var(--green);padding:10px 14px;border-radius:8px;margin:8px 0 12px}
.rule dl{display:grid;grid-template-columns:110px 1fr;gap:6px 16px;margin:0}
.rule dt{font:11px var(--mono);letter-spacing:1px;color:#879388;padding-top:3px}
.rule dd{margin:0;min-width:0}
.ev{margin:0 0 8px;padding:0;list-style:none}
.ev li{margin:5px 0}
.ev .k{font:10px var(--mono);padding:1px 7px;border-radius:99px;margin-right:6px;background:var(--lime);color:var(--green)}
.ev .k.x{background:#e6def4;color:#5d4590}
.ev a{word-break:break-all;color:var(--green)}
details{margin:8px 0}
summary{cursor:pointer;font:12px var(--mono);color:var(--green)}
.badge{font:10px var(--mono);background:#eaefe6;border-radius:99px;padding:2px 9px;margin-left:8px;color:#4f5d56}
.miss{background:#fbf3e4}
td.num{font-family:var(--mono);font-size:13px}
.matrix td,.matrix th{text-align:center;padding:8px 10px;font-size:13px}
.matrix td:first-child,.matrix th:first-child{text-align:left}
@media(max-width:820px){.rule dl{grid-template-columns:1fr}.rule dt{padding-top:10px}}
"""

rules = RL.build_rules()
groups = []
for r in rules:
    if r["group"] not in groups:
        groups.append(r["group"])


def pre(sql):
    return f"<pre><code>{e(sql.strip())}</code></pre>"


def rule_card(r):
    ev = ""
    for x in r["evidence"]:
        if x[0] == "doc":
            ev += f'<li><span class="k">官方文档</span><a href="{e(x[1])}">{e(x[1])}</a> — {e(x[2])}</li>'
        else:
            ev += f'<li><span class="k x">实验</span>{e(x[1])}</li>'
    chk = CHECKS[r["id"]]
    return f"""<article class="card rule" id="{r['id'].lower()}">
<span class="idx">{r['id']} · {e(r['group'])}</span>
<h3>{e(r['title'])}</h3>
<div class="oneline">{e(r['rule'])}</div>
<dl>
<dt>适用前提 / 版本</dt><dd>{e(r['premise'])}</dd>
<dt>依据</dt><dd><ul class="ev">{ev}</ul></dd>
<dt>后果与代价</dt><dd>{e(r['cost'])}</dd>
<dt>反例</dt><dd>{e(r['counter'])}</dd>
<dt>检查查询</dt><dd><p class="src">{e(chk[0])}</p><details><summary>展开 SQL</summary>{pre(chk[1])}</details></dd>
<dt>示例 SQL</dt><dd><details><summary>展开</summary>{pre(r['demo_sql'])}</details></dd>
</dl></article>"""


body_rules = ""
for g in groups:
    body_rules += f'<h3 style="margin:26px 0 4px">{e(g)}</h3>'
    body_rules += "".join(rule_card(r) for r in rules if r["group"] == g)

# 预测 vs 实测
R = RL.R
r06, r09, r14, r07 = R("R06"), R("R09"), R("R14"), R("R07")
med = lambda a: sorted(a)[len(a) // 2]
v6 = r06["variants"]
ins = {k: med(v["insert_s"]) for k, v in v6.items()}
wal = {k: med(v["wal_bytes"]) for k, v in v6.items()}
idx = {k: v["index_bytes"] for k, v in v6.items()}
k7, k4, kb = "uuid v7 (uuidv7())", "uuid v4 (uuidv4())", "bigint identity"
a9 = r09["steps"]["覆盖索引,VACUUM 之后"]; b9 = r09["steps"]["覆盖索引,大量 UPDATE 之后、VACUUM 之前"]
buf = lambda q: q["shared_hit"] + q["shared_read"]
pi, ci = r14["普通 CREATE INDEX"], r14["CREATE INDEX CONCURRENTLY"]
predictions = [
    ("R01", "倍率 ≥ 1000 倍", f"{R('R01')['speedup_x']:,} 倍", True),
    ("R03", "text ≈ varchar(n)(±5%);char(n) 更大", "text 与 varchar 相差约 4%;char(32) 体积 +25%/+43%。另有预测外的发现:char(n) 与 text 表达式比较不走索引", True),
    ("R06", "v4/v7 耗时 ≥ 1.5×", f"{ins[k4] / ins[k7]:.1f}×", True),
    ("R06", "v4/v7 的 WAL ≥ 2×", f"{wal[k4] / wal[k7]:.2f}×(只多 9%)", False),
    ("R06", "v4 索引比 v7 大 30–60%", f"+{(idx[k4] / idx[k7] - 1) * 100:.0f}%", True),
    ("R06", "v7 的表现接近 bigint", f"v7 比 bigint 慢 {ins[k7] / ins[kb]:.1f}×,索引大 {(idx[k7] / idx[kb] - 1) * 100:.0f}%", False),
    ("R07", "PG18 skip scan ≥ 10× 改善;高基数前导列无效", f"缓冲区减少 {buf(r07['versions']['PostgreSQL 16']['variants']['(status, created_at)']['queries']['B_仅时间范围(缺前导列status)']) / buf(r07['versions']['PostgreSQL 18']['variants']['(status, created_at)']['queries']['B_仅时间范围(缺前导列status)']):.0f}×;高基数列缓冲区相同", True),
    ("R08", "部分索引体积 ≤ 1%", "0.29%", True),
    ("R09", "VACUUM 后缓冲区读取减少 ≥ 5×", f"{buf(b9) / buf(a9):.1f}×(Heap Fetches {b9['heap_fetches']:,} → {a9['heap_fetches']})", False),
    ("R10", "OFFSET 线性变慢、keyset 恒定", "OFFSET 500 万:约 1 秒;keyset 约 0.01 ms", True),
    ("R11", "Hash 体积 30–60%、TPS ±15%", "体积 45%,TPS +9%", True),
    ("R12", "直接 SET NOT NULL 停顿 1–3 秒;NOT VALID 路径不阻塞", f"{RL.ms(R('R12')['A_直接_SET_NOT_NULL']['duration_ms'])};NOT VALID 路径最长停顿几毫秒", True),
    ("R13", "易变默认值 ≥ 10 秒并重写", "11.3 秒 / 21.7 秒,relfilenode 改变", True),
    ("R14", "CONCURRENTLY 耗时 1.5–2×", f"{ci['duration_ms'] / pi['duration_ms']:.2f}×", False),
    ("R15", "ON CONFLICT 必须匹配索引;(隐含预测:部分唯一索引不能被推断)", "写上 WHERE 谓词就可以;不写才报错", False),
]
pred_rows = "".join(
    f'<tr class="{"" if ok else "miss"}"><td>{rid}</td><td>{e(p)}</td><td class="num">{e(m)}</td><td>{"✓ 命中" if ok else "✗ 预测有误"}</td></tr>'
    for rid, p, m, ok in predictions)
n_ok = sum(1 for *_, ok in predictions if ok)

# 规则 x 来源矩阵
S = {"agree": "✓", "diff": "≠", "ment": "○", "none": "—"}
matrix = [
    ("R01 外键索引", "agree", "ment", "ment", "ment", "ment"),
    ("R02 timestamptz", "agree", "ment", "ment", "ment", "ment"),
    ("R03 text 而非 varchar(n)", "agree", "agree", "agree", "agree", "agree"),
    ("R04 金额 numeric", "agree", "ment", "ment", "none", "ment"),
    ("R05 identity 而非 serial", "agree", "ment", "agree", "agree", "ment"),
    ("R06 避免 UUIDv4 主键", "agree", "agree", "diff", "agree", "none"),
    ("R07 复合索引顺序 / skip scan", "agree", "diff", "diff", "ment", "none"),
    ("R08 部分索引", "agree", "ment", "ment", "ment", "ment"),
    ("R09 覆盖索引 + VACUUM", "agree", "none", "ment", "ment", "ment"),
    ("R10 keyset 分页", "agree", "agree", "none", "ment", "ment"),
    ("R11 Hash 索引", "agree", "agree", "none", "ment", "none"),
    ("R12 在线 NOT NULL", "agree", "none", "ment", "ment", "none"),
    ("R13 加列默认值", "agree", "none", "diff", "ment", "none"),
    ("R14 CONCURRENTLY", "agree", "ment", "ment", "ment", "none"),
    ("R15 UNIQUE / ON CONFLICT", "agree", "none", "diff", "ment", "none"),
]
cols = ["PG18 官方文档/wiki", "supabase", "wshobson", "neon", "ECC"]
mt = "".join(f"<tr><td>{e(row[0])}</td>" + "".join(f"<td>{S[x]}</td>" for x in row[1:]) + "</tr>" for row in matrix)

unverified = [
    ("规模", "迁移停顿(R12–R14)只在 1000 万行、数据在缓存中测过;停顿随表大小和 IO 放大,亿级冷数据会明显更长。"),
    ("绝对数值", "测试机为 2 核 / 8GB / 本地盘 / fsync=off,耗时不能直接套到云实例,只看倍数和量级。"),
    ("未测项", "numeric 相对 float8 的性能开销、uuidv7() 函数本身的生成开销没有单独测量。"),
    ("版本对比", "PG16 与 PG18 的对比只看缓冲区数,耗时受构建差异干扰。"),
    ("样本量", "每组重复 3–6 次,给的是中位数与范围,不是置信区间。"),
]
unv_html = "".join(f"<li><strong>{e(a)}</strong>:{e(b)}</li>" for a, b in unverified)

conflicts = [
    ("复合索引“最左前缀”", "supabase / wshobson 写作绝对规则", "PG18 Release Notes + 实验", "PG18 对低基数前导列不再成立(R07)"),
    ("ON CONFLICT 与部分唯一索引", "wshobson 说“partial indexes don't work”", "官方 INSERT 文档 + 实验", "带 WHERE 谓词即可(R15)"),
    ("volatile default 举例", "wshobson 把 now() 列为易变", "官方文档 + 实验", "now() 是 STABLE,不重写;gen_random_uuid() 才重写(R13)"),
    ("Hash 索引是否更快", "supabase“略快”,其余默认 B-tree", "实验", "略快约 9%、体积小一半,但只适用等值查询(R11)"),
    ("UUID 主键", "neon / supabase 反对 v4,wshobson 允许", "实验", "v4 慢 2.1×,索引大 30%(R06)"),
]
cf = "".join(f"<tr><td>{a}</td><td>{b}</td><td>{c}</td><td>{d}</td></tr>" for a, b, c, d in conflicts)

nav = '<a href="#rules">规则</a><a href="#todo">局限</a><a href="#repro">复测</a>'

page = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PostgreSQL 18 设计规则手册 — Lab</title>
<meta name="description" content="15 条 PostgreSQL 18 表结构设计规则:做法、代价、反例与检查查询。">
<style>{css}</style>
</head>
<body><div class="shell">
<header>
  <a class="brand" href="../"><span class="mark">↗</span> Lab <small>← 返回目录</small></a>
  <nav>{nav}</nav>
</header>
<main>
<div class="hero">
  <div class="eyebrow"><span class="dot"></span> POSTGRESQL 18 RULEBOOK</div>
  <h1>PostgreSQL 18 <em>表结构设计规则</em></h1>
  <p class="lead">15 条规则,覆盖键与约束、数据类型、索引、查询和在线迁移。每条给出做法、适用前提、数据、代价、反例,以及一条可以直接对你的库运行的检查查询。</p>
</div>

<section id="premise">
  <div class="note">前提:PostgreSQL 18 · 通用 OLTP · 千万到亿级行 · 托管云。文中数据来自 2 核 / 8GB 单机上 300 万–1000 万行的测试,只看倍数和量级,绝对耗时请在自己的实例上复测(脚本见 <code>lab/</code>)。</div>
</section>

<section id="rules">
  <div class="section-head"><h2><span>01 /</span>15 条规则</h2><span>规则 · 前提 · 依据 · 代价 · 反例 · 检查查询</span></div>
  {body_rules}
</section>





<section id="todo">
  <div class="section-head"><h2><span>02 /</span>局限</h2><span>使用时注意</span></div>
  <div class="card"><ul>{unv_html}</ul></div>
</section>

<section id="repro">
  <div class="section-head"><h2><span>03 /</span>自己复测</h2><span>lab/ 目录</span></div>
  <div class="card"><ol class="steps">
    <li>准备 PostgreSQL 18(以及对照用的 16),<code>git clone</code> 本仓库,进入 <code>postgresql/lab</code>。</li>
    <li>设置环境变量:<code>LAB_PGHOST LAB_PGPORT LAB_PGUSER</code>(以及 <code>LAB_N</code> 控制行数),<code>pip install psycopg2-binary</code>。</li>
    <li><code>python3 00_build_base.py new</code> 建基础数据,再依次运行 <code>exp_semantics.py / exp_index.py / exp_types.py / exp_migrate.py</code>,结果写入 <code>results/*.json</code>。</li>
    <li><code>python3 checks.py</code>:在“故意违规”的库里每条检查必须有输出,在“按规则建”的库里必须为空(自测)。<code>checks.sql</code> 可直接对你的库运行。</li>
    <li><code>python3 validate_sql.py</code>:把本页所有 SQL 在你的实例上执行一遍。</li>
    <li><code>npx squawk-cli migrations/bad.sql</code> 与 <code>migrations/good.sql</code>:用迁移 linter 复核 R12–R14。</li>
    <li><code>python3 build_page.py</code>:用你自己的 results 重新生成本页,看结论是否一致。</li></ol></div>
</section>
</main>
<footer><span>↗ <a href="../">LAB</a> / POSTGRESQL</span><span>PG 18.6 · 数据与脚本见 lab/</span></footer>
</div></body>
</html>
"""
os.makedirs(os.path.dirname(OUT), exist_ok=True)
open(OUT, "w", encoding="utf-8").write(page)
print("written", os.path.abspath(OUT), len(page))
