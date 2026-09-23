# OpsAxiom

> 一个面向运维人员的开源智能体：把运维专家的判断编译成**可验证、可回滚、可认证**的
> Skill 资产，让任何模型——包括跑在你内网的本地小模型——都能安全地使用它。

> **社区已上线**：Skill 仓库 [opsaxiom-registry](https://github.com/hf4Academic/opsaxiom-registry) ·
> 浏览网站 [hf4academic.github.io/opsaxiom-site](https://hf4academic.github.io/opsaxiom-site)

## 一句话定位

**像带着运维专家一起排查故障**。每一步操作都可回滚，每个 Skill 都经过社区验证——它给你方案，你来做决定。

## 开箱即用（实测流程）

### 在线安装

**1) 一键安装**（装依赖、软链命令、初始化密钥、自动体检）：

```bash
git clone <本仓库> && cd OpsAxiom
./install.sh
export PATH="$HOME/.local/bin:$PATH"   # 若安装末尾提示 PATH，加这行（可写进 ~/.bashrc）
```

装完自动跑 `opsaxiom doctor`：🟢 全绿即可用；🟡 只是可选连接器缺失（如本机没装
kubectl，只影响 k8s 域的智能诊断，**导航档（指引模式）不受影响**）。

### 离线安装（气隙环境 / 无网络）

前往 [Releases](https://github.com/hf4Academic/opsaxiom/releases) 页面
下载最新 `opsaxiom-offline-vX.Y.Z.tar.gz`，然后：

```bash
tar xzf opsaxiom-offline-v*.tar.gz && cd opsaxiom-offline-*
./install.sh --offline
```

离线包内包含：全部 Skill 库、工具链依赖 wheel、本机小模型(Qwen2.5-0.5B)，解压即装，无需外网。

**2) 用：敲一个词，然后说人话**

```bash
$ opsaxiom
OpsAxiom v0.1 · 205 个 Skill（205 已验证）· 输入你遇到的问题，或 help 看用法
axiom> 磁盘满了但 df 显示还有空间 mount=/data
  经检索匹配，技能库中存在以下 3 个经过验证的排查方法：
  [1] host.storage.capacity.disk-full (磁盘空间耗尽诊断与处置)
  [2] ...
  → 输入序号进入对应 Skill 逐步诊断；回车则批量诊断。
  ▶ 诊断中（本机只读自动执行）… 完成
  ── 诊断卷宗 ──────────────────────────────
  ✔ 已证实  磁盘空间耗尽  证据: df -i → rows[0].ipcent = 99  → 待处置：移入隔离区
  ✘ 已排除  已删除未释放  证据: lsof +L1 → 无残留句柄
  → 处置：run host.storage.capacity.disk-full（进入指引模式执行变更）
axiom> gpu 掉卡 xid 79        ← 换个域接着问
axiom> report                 ← 把当前卷宗导出为故障报告（贴工单/转人工）
axiom> quit
```

规矩：**写操作永远由你亲手执行**（指引模式），它只给方案、变更影响说明和回滚命令；
诊断阶段只跑只读命令；判读全由机器按解析器字段算，证据不足就明说还差什么。
远端设备则给你一整块命令一次贴回。
中途 Ctrl-C 暂停（进度已存），`resume` 继续；输候选序号可回到老式逐步排查（兜底）。

**3) 脚本/自动化用子命令**：

```bash
opsaxiom doctor                                   # 环境自检（排障第一命令）
opsaxiom diagnose "kafka 积压" --json             # 结构化候选（给自动化/webhook 用）
opsaxiom run host.storage.capacity.disk-full \
  --answers demos/disk-full-guided.answers.yaml   # 脚本驱动演示；去掉 --answers 即真人交互

# 告警→IM 卡片（--dry-run 只打印不出站）：
echo '{"alerts":[{"labels":{"alertname":"GPU 掉卡 XID 79"}}]}' | opsaxiom-webhook --dry-run
```

## 三种配合方式（你放心到哪一步，就用到哪一步）

默认开箱即处于**指引模式**（项目内称"导航档"）：只出分析结论和方案，动手由你亲自来，
不把任何密码/密钥交给它，最安全。如果需要更多自动化：

1. **它出主意、你动手**：指引模式。给诊断步骤和变更影响说明，命令你自己敲
2. **只读的它自己跑**：查看类命令（不改系统的）白名单内自动执行，要改系统的仍然你来。
   通过 `target add` 接入设备后开启**受限执行**（项目内称"白名单档"）
3. **改动它也代劳**：`target grant` 升级到**完整执行权限**（项目内称"root 档"），
   管理账号直登全量自动执行，但高风险动作会先停下来等你批准。
   TTL 到期自动退回受限模式

## 与现有工具的区别

- **HolmesGPT / K8sGPT**：只读诊断、K8s 为主 → 我们覆盖主机/网络设备/智算等全栈，且能安全执行变更
- **通用 Agent**：临场靠模型现推，换个环境就翻车 → 我们的排查步骤是从你这台机器的真实情况
  （版本、配置、拓扑）里长出来的；每条命令先核对语法对不对，每个会改动系统的操作都先备好"怎么撤销"
- **传统 runbook（操作手册）**：一份静态文档 → 我们是一步步执行、每步都验证结果对不对的交互式排查，
  且每个 Skill 带社区验证徽章（⚪ 没验证 → 🔵 仿真验证过 → 🟢 有人在真机上验证过 → 🟡 官方认证）

## 项目状态

- **205 个 Skill**（host 46 / network 38 / middleware 31 / k8s 30 / aicomp 28 / obs 15 / sec 14 / proc 3），
  **205 个全部 `sim_verified`**（每个都有可重现的仿真证据；含 action 的还通过了回滚往返验证）。
- 完整工具链：校验器（结构 + 语义 S1–S13 + 投影语义 + 字段契约 + 命令语法树）、
  解析器库、仿真执行器（context_walk + 真实靶机，含 kubectl 只读白名单）、
  **maturity 流水线（sim_verified → field_verified，≥3 份独立签名 attestation）**、
  **Ed25519 签名的 attestation + keyring 治理**。
- **告警入口**：`opsaxiom diagnose --json` + `opsaxiom-webhook` 收 Alertmanager
  告警 → 匹配 Skill → 推钉钉/飞书卡片（只推荐不代执行）。
- **运行时 CLI**：`opsaxiom` 裸敲进交互态，说一遍问题 → 并行多假设、一轮批量诊断
  （只读命令自动跑/远端一次粘贴）→ 诊断卷宗 → 处置审批 → 复盘导出。
  支持 `--resume` 断点继续、变更节点 skip/升级/退出多选。
- **远程接入**（docs/12）：`target` 交互菜单管理设备（list/add/grant/revoke/delete/
  import-ssh-config/doctor，缺参数自动引导；add 向导含端口/VPN 标签），ssh/网络设备/k8s/http 四连接器。
  **add 一站式开通**：本机密钥检测/生成 → 密码一次上门 → 公钥双装 → ro 账号+sudoers 只读白名单，
  密码用完即弃不落任何文件。诊断时选"非本机-远程"并挑一台设备：执行门统一把关（授权 TTL + 只读白名单 +
  注入防护 + 审计），探针自动远程执行，名单外/未授权/不可达的降级为逐条粘贴人工贴回；
  `cred set` 本地钥匙串存密码类凭证——**凭证不出本机，清单只存引用**。
- **本地化 Skill**（docs/13）：linkbook 个人网页台账、overlay 叠加层（填 placeholder/贴注记，
  不碰通用树）、fork 派生——**个人层结构性不出门**（打包/CI 拒收，`report --share` 自动剥离 📌 与内网地址）。
- **可选接模型**（只做理解/叙事/建议，永不出命令、不判分支）：
  本机小模型(Qwen2.5-0.5B)（`opsaxiom model pull` 本机离线跑，开箱备用）/ Ollama / OpenAI 兼容
  远程 API / **Pi Agent Harness 多 provider 网关**，`opsaxiom model` 一条命令切换，
  首启有向导；任一后端不可用自动降级为关键词匹配模式，绝不阻塞诊断。
- 一键部署（`install.sh`/docker/离线包）+ `doctor` 自检；经验捕获三通道
  （`skill from-session`/`record`/`skill new`）把日常诊断变 Skill 草稿；
  诊断终点一键认证（30 秒签名沉淀）；**Skills Hub**（`hub pull` 含安全校验 / `hub push` / 静态站生成器）。

## 仓库导览

| 路径 | 内容 |
|---|---|
| `HANDOFF.md` | **模型交接协议与当前状态（接手先读这个）** |
| `docs/00-golden-rules.md` | 黄金准则（宪法，12 条） |
| `docs/01-architecture.md` | 核心架构与差异化逻辑层 |
| `docs/02-rollback-design.md` | 可回滚保证机制 |
| `docs/03-skill-schema.md` + `schema/` | Skill Schema v0.1（法律层） |
| `docs/04-taxonomy.md` | 运维知识地图 / 故障分类树 |
| `docs/05-certification.md` | 社区验证与认证体系 |
| `skills/` | Skill 库（含 2 个金标准样例） |
| `docs/09-interaction-v2.md` | 交互模型 v2：诊断（陈述→诊断→卷宗→处置→复盘） |
| `tools/pi/opsaxiom.ts` | pi 智能入口扩展 |
| `Dockerfile` + `docker-compose.yml` | 多阶段镜像（core / llm / full 三档重量） |
| `TODO-opus.md` | 当前执行批次的任务书 |

## pi 智能入口（可选升级层）

装了 [Pi Agent Harness](https://pi.dev)（node≥22.19）后，裸敲 `opsaxiom` 会**自动进 pi 智能入口**；
探测不到就无感回落到 Terminal REPL。模型能调的只有 `axiom_diagnose`/`axiom_incident`/
`axiom_report` 三个工具，工具内部全是确定性引擎——**模型永远拿不到出命令、判分支的权力**；
写操作仍走指引模式的审批门。

```bash
export PATH="$HOME/.local/node22/bin:$PATH"
npm install --prefix ~/.local/pi-agent @earendil-works/pi-coding-agent
opsaxiom            # 裸敲即自动进 pi 入口（首次先 /connect 接你的模型）
```

`/connect` 支持**远程 API**（自己输 Key）/ **本机 Ollama** /
**本机小模型(Qwen2.5-0.5B)**（`opsaxiom model pull` 下载，离线备用），预置 DeepSeek/Claude/OpenAI 等常用服务商。

## 开发者快速上手

```bash
pip install -r tools/requirements.txt
python3 tools/validate.py skills/            # 校验全部 Skill（结构 + 语义 S1–S13 + 命令语法树）
python3 -m pytest tools -q                   # 运行全部测试
python3 sim/run_sim.py sim/scenarios/disk-full-inode-exhaustion.yaml   # 跑一条仿真场景
```

> 命名说明：项目名 **OpsAxiom**（axiom = 公理，呼应"黄金准则"与"Schema 是法律"的设计哲学）。
> 仓库内部标识符（`opsagent-*` CLI 工具名、schema $id）在 O-6 实现工具时统一改为 `opsaxiom-*`。

## License

Apache-2.0（拟）