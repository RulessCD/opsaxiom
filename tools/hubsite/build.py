"""
Hub 静态网站生成器（V-5，docs/08 §3.4）——registry → 纯静态 HTML。

无 JS 框架依赖：首页嵌入 index 数据 + 原生 JS 关键词过滤；每个 Skill 一张详情页
（决策树文本可视化、cautions、maturity 徽章、attestation 列表与验签状态）。
私有环境对内网 registry 跑一遍即得内部 Hub。

导航（发起人验收意见）：浅色主题；全站导航栏（首页/怎么用/怎么贡献/治理与投诉）；
详情页有明显返回；每个 Skill 带 👎 投诉按钮 → registry 仓库 GitHub Issue（进入治理流程）。

用法：python tools/hubsite/build.py <registry_dir> <out_dir> [github_repo_url]
"""
import html
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import yaml            # noqa: E402

DEFAULT_REPO = "https://github.com/hf4Academic/opsaxiom-registry"
MAIN_REPO = "https://github.com/hf4Academic/opsaxiom"      # 客户端（OpsAxiom 本体）

# 徽章：图标 + 中文名 + 一句白话含义（递进：草稿→仿真→实地→认证）。
BADGE = {
    "draft":          ("✏️", "#94a3b8", "草稿",   "刚写好、还没验证过——先别照着在生产上做"),
    "sim_verified":   ("🧪", "#3b82f6", "仿真验证", "在模拟环境里跑通了整套判断逻辑"),
    "field_verified": ("🛡️", "#16a34a", "实地验证", "已有 3 位以上的人在真实机器上用过并签名背书"),
    "certified":      ("🏅", "#d97706", "官方认证", "领域评审人正式签署认可，最高可信度"),
}

# 浅色主题（发起人验收：浅色更好看）
CSS = """
:root{--bg:#f8fafc;--card:#ffffff;--fg:#1e293b;--mut:#64748b;--acc:#2563eb;--line:#e2e8f0}
*{box-sizing:border-box}body{margin:0;font:15px/1.6 -apple-system,Segoe UI,Roboto,'Noto Sans CJK SC',sans-serif;background:var(--bg);color:var(--fg)}
a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}
nav{display:flex;gap:18px;align-items:center;padding:12px 32px;background:#fff;border-bottom:1px solid var(--line);position:sticky;top:0}
nav .brand{font-weight:700;color:var(--fg)}nav a{color:var(--mut);font-size:14px}nav a.on,nav a:hover{color:var(--acc)}
nav .gh{margin-left:auto}
header{padding:22px 32px;border-bottom:1px solid var(--line);background:#fff}
h1{margin:0;font-size:22px}.sub{color:var(--mut);font-size:13px;margin-top:4px}
.wrap{max-width:1000px;margin:0 auto;padding:24px 32px}
input#q{width:100%;padding:12px 14px;border-radius:10px;border:1px solid var(--line);background:#fff;color:var(--fg);font-size:15px;margin-bottom:20px}
.dom{color:var(--mut);text-transform:uppercase;font-size:12px;letter-spacing:.08em;margin:22px 0 8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:8px 0;box-shadow:0 1px 2px rgba(15,23,42,.04)}
.card h3{margin:0 0 4px;font-size:16px}.badge{font-size:12px;padding:2px 8px;border-radius:999px;color:#fff;font-weight:600}
.mut{color:var(--mut);font-size:13px}
.node{background:#f1f5f9;border-left:3px solid #cbd5e1;padding:8px 12px;margin:6px 0;border-radius:6px}
.node .t{font-weight:600}.caut{color:#b45309;font-size:13px;margin:3px 0 3px 12px}
.exit-done{border-left-color:#22c55e}.exit-esc{border-left-color:#f59e0b}
table{width:100%;border-collapse:collapse;margin-top:8px}td,th{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);font-size:13px}
.pill{font-size:12px;color:var(--mut)}
.btn{display:inline-block;padding:8px 14px;border-radius:10px;border:1px solid var(--line);background:#fff;font-size:14px}
.btn:hover{border-color:var(--acc);text-decoration:none}
.btn.warn{color:#b91c1c;border-color:#fecaca}.btn.warn:hover{border-color:#b91c1c}
.badge{display:inline-flex;align-items:center;gap:4px;line-height:1.4}
.legend{background:#fff;border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:0 0 20px}
.legend-title{font-size:13px;color:var(--mut);margin-bottom:10px}
.legend-item{display:flex;align-items:center;gap:10px;padding:5px 0}
.legend-txt{font-size:13px;color:var(--fg)}
@media(min-width:640px){.legend{display:grid;grid-template-columns:1fr 1fr;gap:6px 24px}.legend-title{grid-column:1/-1}}
.step{background:#fff;border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:12px 0}
.step h3{margin:0 0 6px;font-size:15px}
.step.ch{border-color:var(--acc);box-shadow:0 1px 3px rgba(37,99,235,.08)}
details.foldy{background:#f8fafc;border:1px solid var(--line);border-radius:10px;padding:2px 14px;margin:10px 0}
details.foldy>summary{cursor:pointer;font-size:14px;font-weight:600;padding:11px 0;list-style:none;color:var(--acc)}
details.foldy>summary::-webkit-details-marker{display:none}
details.foldy>summary::before{content:"▸  "}
details.foldy[open]>summary::before{content:"▾  "}
details.foldy .foldbody{padding:2px 0 10px}
details.foldy .foldbody h4{margin:14px 0 4px;font-size:14px}
code,pre{background:#f1f5f9;border-radius:6px;padding:1px 6px;font-size:13px}
pre{padding:12px 14px;overflow:auto;line-height:1.5}
"""


def _esc(s):
    return html.escape(str(s or ""))


def _badge_html(m):
    icon, color, label, _meaning = BADGE.get(m, ("?", "#888", m, ""))
    return (f'<span class="badge" style="background:{color}" title="{_esc(_meaning)}">'
            f'{icon} {_esc(label)}</span>')


def _legend_html():
    """首页徽章图例：图标 + 名称 + 白话含义，按可信度从低到高排。"""
    order = ["draft", "sim_verified", "field_verified", "certified"]
    cells = []
    for m in order:
        icon, color, label, meaning = BADGE[m]
        cells.append(
            f'<div class="legend-item"><span class="badge" style="background:{color}">'
            f'{icon} {_esc(label)}</span><span class="legend-txt">{_esc(meaning)}</span></div>')
    return ('<div class="legend"><div class="legend-title">徽章含义'
            '（可信度从低到高）</div>' + "".join(cells) + '</div>')


def _nav(depth, on, repo):
    p = "../" * depth
    def cls(k):
        return ' class="on"' if k == on else ""
    return (f'<nav><span class="brand">🦫 OpsAxiom Hub</span>'
            f'<a href="{p}index.html"{cls("home")}>Skill 库</a>'
            f'<a href="{p}contribute.html"{cls("contrib")}>怎么贡献</a>'
            f'<a href="{p}governance.html"{cls("gov")}>治理与投诉</a>'
            f'<a class="gh" href="{repo}">GitHub ↗</a></nav>')


def _tree_html(skill):
    out = []
    entry = skill.get("tree", {}).get("entry")
    out.append(f'<p class="mut">入口：<code>{_esc(entry)}</code></p>')
    for n in skill.get("tree", {}).get("nodes", []):
        cls = "node"
        if n.get("type") == "done":
            cls += " exit-done"
        elif n.get("type") == "escalate":
            cls += " exit-esc"
        out.append(f'<div class="{cls}">')
        out.append(f'<div class="t">[{_esc(n.get("type"))}] {_esc(n.get("id"))} — {_esc(n.get("title") or n.get("summary",""))[:120]}</div>')
        for b in n.get("branch", []) or []:
            out.append(f'<div class="mut">· 当 <code>{_esc(b.get("when"))}</code> → {_esc(b.get("goto"))}</div>')
        if n.get("otherwise"):
            out.append(f'<div class="mut">· 否则 → {_esc(n["otherwise"])}</div>')
        for c in n.get("cautions", []) or []:
            out.append(f'<div class="caut">⚠ {_esc(c)}</div>')
        out.append('</div>')
    return "\n".join(out)


def _att_html(skill_dir):
    adir = skill_dir / "attestations"
    if not adir.is_dir():
        return '<p class="mut">暂无实地验证记录。</p>'
    rows = ['<table><tr><th>日期</th><th>结局</th><th>档位</th><th>环境</th><th>回滚</th><th>签名</th></tr>']
    for a in sorted(adir.glob("*.yaml")):
        att = yaml.safe_load(a.read_text(encoding="utf-8")) or {}
        env = att.get("env_fingerprint", {})
        os_ = env.get("os", {})
        sig = att.get("signature", "")
        sigshow = "✔ " + sig.split(":")[0] if sig and sig != "UNSIGNED-TODO" else "未签名"
        rows.append(f'<tr><td>{_esc(a.stem[:10])}</td><td>{_esc(att.get("outcome"))}</td>'
                    f'<td>{_esc(att.get("mode"))}</td>'
                    f'<td>{_esc(os_.get("family"))} {_esc(os_.get("version_bucket"))} / {_esc(env.get("arch"))}</td>'
                    f'<td>{"✔" if att.get("rollback_exercised") else "—"}</td><td class="pill">{_esc(sigshow)}</td></tr>')
    rows.append('</table>')
    return "\n".join(rows)


def _page(title, body):
    return (f'<!doctype html><html lang="zh"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{_esc(title)}</title><style>{CSS}</style></head><body>{body}</body></html>')


def _report_url(repo, skill_id):
    """👎 投诉链接：预填标题/标签的 GitHub Issue —— 治理流程入口。"""
    return (f'{repo}/issues/new?labels=report'
            f'&title=%5Breport%5D%20{skill_id}'
            f'&body=%23%23%20被投诉%20Skill%0A%60{skill_id}%60%0A%0A'
            f'%23%23%20问题（误导%2F翻车%2F过时%2F安全隐患）%0A%0A'
            f'%23%23%20环境与复现%0A')


def _contribute_html(repo):
    return f'''
<header><h1>怎么贡献你验证过的 Skill</h1>
<div class="sub">两条通道殊途同归：都落到本仓库的一个 Pull Request，CI 自动质检，维护者合入即上架。
装客户端只是通道一需要——通道二纯网页无需安装。</div></header>
<div class="wrap">
<div class="step ch"><h3>通道一：客户端（推荐，全程引导）</h3>
<p class="mut">用客户端把你刚做完的排查自动沉淀成 Skill 草稿、本地校验、仿真验证后一键打包。
第一次用需要先装客户端（下面折叠，点开看）。</p>
<details class="foldy"><summary>怎么安装 OpsAxiom 客户端（约 5 分钟，点开展开）</summary>
<div class="foldbody">
<h4>第 1 步 · 装客户端</h4>
<pre>git clone {MAIN_REPO}.git && cd opsaxiom
./install.sh                              # 装依赖、把 opsaxiom 命令软链进 PATH、自检
export PATH="$HOME/.local/bin:$PATH"      # 若安装末尾提示 PATH，加这行（可写进 ~/.bashrc）</pre>
<p class="mut">装完敲 <code>opsaxiom doctor</code>：🟢 全绿即可用（🟡 只是可选连接器没装，不影响）。
气隙/内网环境用离线包：<code>./install.sh --offline</code>。客户端源码见
<a href="{MAIN_REPO}">{MAIN_REPO}</a>。</p>
<h4>第 2 步 · 启动</h4>
<pre>opsaxiom                                  # 直接敲这一个词，进入交互界面</pre>
<p class="mut">进去后直接用中文描述你的故障（如"磁盘满了但 df 显示还有空间"）就能开始排查。
装了 node≥22 会自动进入 AI 对话界面；没装也没关系，进入经典逐步排查界面，功能一样全。
<code>opsaxiom classic</code> 可强制经典界面。</p>
<h4>第 3 步（可选）· 接一个 AI 模型，让它更懂人话</h4>
<pre>opsaxiom model install-local              # 一键装 ollama + 最小千问模型（本机离线跑）</pre>
<p class="mut">安装前会自动体检本机条件（磁盘 ≥3GB、内存 ≥1.5GB、root/sudo 权限、网络可达）——
<b>不满足会明确提示：本地不具备安装本地小模型的依赖或资源，请连接远端模型，或离线使用</b>。
连远端模型：<code>opsaxiom model use remote --endpoint … --api-key …</code>（或 AI 界面里
<code>/login</code> 选服务商填 Key）。完全不接模型也能用：判断全靠验证过的排查方案，不靠 AI 现猜。</p>
</div></details>
<p class="mut" style="margin-top:12px">装好后，把排查变成可投稿的 Skill：</p>
<pre>opsaxiom skill from-session &lt;会话id&gt;   # 把你刚做完的排查自动变成草稿
opsaxiom skill lint &lt;草稿&gt;             # 校验 + 缺口清单，补完
# 仿真验证晋级到 🔵 后：
opsaxiom hub push &lt;skill-id&gt;           # 打包 bundle（pi 界面里 /publish 上下键选）</pre>
<p class="mut">得到 bundle 后：fork 本仓库 → 解包到 <code>skills/&lt;id&gt;/&lt;version&gt;/</code> → 提 PR。</p></div>
<div class="step"><h3>通道二：纯网页（不装客户端）</h3>
<p>1. <a href="{repo}/fork">Fork 本仓库</a>；</p>
<p>2. 在你的 fork 里进入 <code>skills/</code>，用网页 <b>Add file → Upload files</b> 上传你的
<code>&lt;skill-id&gt;/&lt;version&gt;/</code> 目录（skill.yaml + tests/ + attestations/）；</p>
<p>3. 发起 Pull Request——PR 就是发布表单：描述你在什么环境验证过、结果如何。</p></div>
<div class="step"><h3>收录标准（CI 自动检查 + 维护者评审）</h3>
<p>① 校验全绿（结构 + 语义 S1–S13 + 命令语法树）；② 成熟度 ≥ 🔵 sim_verified（⚪draft 拒收）；
③ 实地验证记录（attestation）签名有效；④ 任何写操作必须带经过验证的回滚方案。</p>
<p class="mut">完整政策见 <a href="{repo}/blob/main/policy.md">policy.md</a>。</p></div>
</div>'''


def _governance_html(repo):
    return f'''
<header><h1>治理与投诉</h1>
<div class="sub">发现某个 Skill 误导、翻车、过时或有安全隐患？投诉它，进入治理流程。</div></header>
<div class="wrap">
<div class="step"><h3>怎么投诉（👎）</h3>
<p>每个 Skill 详情页都有 <b>👎 投诉此 Skill</b> 按钮——它会带着 Skill id 打开一个
GitHub Issue（标签 <code>report</code>），你只需写清：<b>什么问题 + 什么环境 + 怎么复现</b>。
不需要账号以外的任何东西。</p></div>
<div class="step"><h3>投诉之后发生什么（治理流程）</h3>
<p>1. <b>核实</b>：维护者按 Issue 复现（必要时在仿真环境重跑该 Skill 的 tests）；</p>
<p>2. <b>处置</b>（按严重度）：修正（提 PR 修复）／<b>降级</b>（撤销徽章，如 🟢→🔵）／
<b>下架</b>（git revert 移出 registry——留痕不删档，历史可查）；</p>
<p>3. <b>公示</b>：处置结论回帖在原 Issue，负面 attestation 同样入库（负面记录是社区的宝贵信号）。</p></div>
<div class="step"><h3>谁在治理</h3>
<p>维护者 = 本仓库有合入权限的人；可信签名者名单在 <code>keyring/trusted.pub</code>，
加入需维护者双人复核（PR 形式）。所有治理动作都是 git 提交——可追溯、可回滚。</p>
<p><a class="btn" href="{repo}/issues?q=label%3Areport">查看历史投诉与处置 ↗</a></p></div>
</div>'''


def build_site(registry_dir, out_dir, repo=DEFAULT_REPO):
    reg = pathlib.Path(registry_dir)
    out = pathlib.Path(out_dir)
    (out / "skill").mkdir(parents=True, exist_ok=True)
    index = json.loads((reg / "index.json").read_text(encoding="utf-8"))

    # 详情页（导航栏 + 明显返回 + 👎 投诉按钮）
    for e in index:
        sd = reg / "skills" / e["id"] / e["version"]
        skill = yaml.safe_load((sd / "skill.yaml").read_text(encoding="utf-8"))
        body = (_nav(1, "home", repo) +
                f'<header><h1>{_esc(e["name"])} {_badge_html(e["maturity"])}</h1>'
                f'<div class="sub"><code>{_esc(e["id"])}</code> · {_esc(e["taxonomy"])} · v{_esc(e["version"])}</div>'
                f'<div style="margin-top:10px"><a class="btn" href="../index.html">← 返回首页</a> '
                f'<a class="btn warn" href="{_report_url(repo, e["id"])}">👎 投诉此 Skill</a></div></header>'
                f'<div class="wrap"><h2>决策树</h2>{_tree_html(skill)}'
                f'<h2>实地验证（attestation）</h2>{_att_html(sd)}</div>')
        (out / "skill" / f"{e['id']}.html").write_text(_page(e["name"], body), encoding="utf-8")

    # 首页（导航栏 + 域分组 + 客户端搜索）
    doms = {}
    for e in index:
        doms.setdefault(e["domain"], []).append(e)
    cards = []
    for dom in sorted(doms):
        cards.append(f'<div class="dom" data-dom="{_esc(dom)}">{_esc(dom)}（{len(doms[dom])}）</div>')
        for e in sorted(doms[dom], key=lambda x: x["id"]):
            hay = f'{e["id"]} {e["name"]} {e["taxonomy"]} {e.get("summary","")}'.lower()
            cards.append(
                f'<div class="card" data-hay="{_esc(hay)}">'
                f'<h3><a href="skill/{_esc(e["id"])}.html">{_esc(e["name"])}</a> {_badge_html(e["maturity"])}</h3>'
                f'<div class="mut"><code>{_esc(e["id"])}</code> · 实地记录 {e["attestations"]}</div>'
                f'<div class="mut">{_esc(e.get("summary",""))}</div></div>')
    js = ("<script>const q=document.getElementById('q');q.addEventListener('input',()=>{"
          "const v=q.value.toLowerCase();document.querySelectorAll('.card').forEach(c=>{"
          "c.style.display=c.dataset.hay.includes(v)?'':'none';});"
          "document.querySelectorAll('.dom').forEach(d=>{let n=d.nextElementSibling,any=false;"
          "while(n&&n.classList.contains('card')){if(n.style.display!=='none')any=true;n=n.nextElementSibling;}"
          "d.style.display=any?'':'none';});});</script>")
    body = (_nav(0, "home", repo) +
            f'<header><h1>OpsAxiom Skills Hub</h1>'
            f'<div class="sub">{len(index)} 个可信运维 Skill · 徽章表示成熟度 · 点开看决策树与实地验证 · '
            f'终端接入：<code>opsaxiom hub pull &lt;id&gt;</code>（首次自动指向本社区）</div></header>'
            f'<div class="wrap">{_legend_html()}'
            f'<input id="q" placeholder="搜索症状 / id / 域…（如 磁盘、xid、暴破）">'
            f'{"".join(cards)}</div>{js}')
    (out / "index.html").write_text(_page("OpsAxiom Skills Hub", body), encoding="utf-8")

    # 贡献 / 治理页
    (out / "contribute.html").write_text(
        _page("怎么贡献", _nav(0, "contrib", repo) + _contribute_html(repo)), encoding="utf-8")
    (out / "governance.html").write_text(
        _page("治理与投诉", _nav(0, "gov", repo) + _governance_html(repo)), encoding="utf-8")
    return len(index)


def main():
    if len(sys.argv) not in (3, 4):
        print("用法：python tools/hubsite/build.py <registry_dir> <out_dir> [github_repo_url]",
              file=sys.stderr)
        return 2
    repo = sys.argv[3].rstrip("/") if len(sys.argv) == 4 else DEFAULT_REPO
    n = build_site(sys.argv[1], sys.argv[2], repo=repo)
    print(f"已生成静态站点：{sys.argv[2]}（{n} 个 Skill，首页 index.html）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
