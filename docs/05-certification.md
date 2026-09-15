# 社区验证与认证体系（Certification v0.1）

> 设计目标：把"人在真实环境里验证过"变成可累积、可信任、可普惠的公共资产（R12），
> 同时防刷、防腐烂。认证的对象是 **Skill 的特定版本**，不是 Skill 本身。

## 1. 成熟度阶梯

| 级别 | 徽章 | 晋级条件 | 运行时权限 |
|---|---|---|---|
| `draft` | ⚪ 生成 | 通过 schema + 语义校验（S1–S10） | 仅导航档，且每屏带"未验证"警示 |
| `sim_verified` | 🔵 仿真 | 全部 tests 在 CI 仿真环境通过，含回滚断言 | 导航 + 协驾档 |
| `field_verified` | 🟢 实地 | ≥ 3 份**独立**有效 attestation，且无未决否决票 | 全部三档（自驾档高危仍需审批） |
| `certified` | 🟡 认证 | field_verified + 领域评审人签署 review + 累计 ≥ 10 份 attestation | 全部三档；进入官方推荐列表 |

**晋级 sim_verified 的口径（二轮评审定稿，追认 R-6）**：
- **诊断类（无 action）**：tests 非空 + 全部声明路径在仿真中复现即可。
- **变更类（含 action）**：额外要求至少一个 `rollback_assert: true` 场景**实际执行**回滚往返。

**证据分级（诚实性声明）**：sim_verified 的证据强度分两档，记录于晋级证据文件
（`<skill>/.maturity/promote.json`），并应在 Skill 页面展示：
- `context_walk`——给定结构化上下文走决策树（验证的是**树逻辑**，未验证命令与解析器本身）；
- `real_roundtrip`——真实执行了动作与回滚（quarantine/deploy 级）。
`context_walk` 是合法的 🔵 门槛（它挡住的是决策树层面的错误），但升 🟢/🟡 时
实地 attestation 会自然补足命令层的验证——这正是两级徽章分开存在的原因。
真实靶机执行器落地后，`context_walk` 晋级的 Skill 应逐步补跑升级为 `real_roundtrip`。

降级规则（自动，由流水线执行）：
- 滚动 90 天内 👎 比例 > 30% 或收到任一"回滚失败"报告 → 立即降至 `draft` 并冻结自驾档；
- `expires_review` 过期未复审 → 降一级；
- Skill MAJOR 版本变更 → maturity 重置为 `draft`（旧版本的 attestation 不继承，但保留可查）。

## 2. Attestation（实地验证记录）—— 核心数据结构

用户在真实环境走完一个 Skill（任意档位）后，Agent 发起两段式反馈：

1. **单比特**（必答一次点击）：解决了吗 👍/👎 —— 零摩擦，人人会答。
2. **结构化 attestation**（自愿，30 秒表单）：这才是认证货币。

```yaml
# skills/<domain>/<slug>/attestations/2026-07-08-a1b2c3.yaml
skill: host.storage.disk-full
skill_version: 0.3.0
outcome: resolved            # resolved | partial | failed | made_worse
mode: navigator              # navigator | copilot | autopilot
env_fingerprint:             # 脱敏的环境画像——自动探测（零询问零自报，2026-09-11 起）
  os: {family: rhel, version_bucket: "8.x"}
  arch: x86_64               # 本机架构；旧字段 scale_bucket 已废除采集（存量文件保留）
deviations: []               # 与决策树的偏离步骤（有偏离 = 决策树改进线索）
rollback_exercised: false    # 是否实际触发过回滚（true 的权重显著更高）
attestor: someuser           # 签名者 = GitHub 账号（token 派生，与发件 issue 作者强一致）
signature: <detached-sig>    # 对本文件内容的签名，工具链自动生成
```

提交通道：y 反馈自动生成并经 token 同步社区（issue → bot 落树），或
`opsaxiom attest --from-session <会话号>` 补交；`attestor=anonymous` 的凭据
仅在本地留存，不上传社区。**attestation 目录 append-only**：只增不改不删。

### 2.1 签名机制（U-4 落地）

- **算法**：Ed25519（`cryptography` 库）。无库时降级 HMAC-SHA256 过渡并显式告警
  （HMAC 不可跨主体验证，仅防篡改）。
- **密钥**：`opsaxiom-attest --keygen` 幂等生成密钥对于 `$OPSAXIOM_HOME/keys/`
  （私钥 `attest_ed25519` 权限 0600，绝不外传；公钥 `.pub`）。
- **签名内容**：attestation 去掉 `signature` 字段后的 JSON 规范序列化（sort_keys）。
- **signature 字段格式**：`ed25519:<公钥b64>:<签名b64>`——**自包含公钥**，验签者无需预先分发公钥
  即可验完整性（防篡改）。是否"可信"另看本地 keyring：`keys/trusted/*.pub` 命中 = 可信，
  未命中 = TOFU（signature 有效但签名者身份未建立信任）。
- **验签**：`opsaxiom-attest --verify <file>`。篡改被签名内容→验签失败。
- 注：schema 的 `signature` 是普通字符串字段（`additionalProperties:false` 下公钥只能内嵌进
  signature 串，不能另设字段），故采用自包含格式。

## 3. 独立性与防刷

"≥ 3 份独立 attestation"中的**独立**定义为：
- 不同 attestor，且
- env_fingerprint 不同分桶（(os 家族, os 主版本桶, 架构) 三元组；
  scale 维度 2026-09-11 起废除，存量文件按旧值参与），且
- attestor 之间无同组织标记（企业域名 hash 分桶）。

防刷机制：
- **attestor = GitHub 账号**（token 派生）：入库凭据要求 attestor 与发件
  issue 作者强一致（bot 五道门之⑤），anonymous 凭据不入树——伪冒身份面焊死。
- attestor 信誉分：新账号的 attestation 权重 0.3，随历史记录被交叉印证而上升；
  被发现虚假记录 → 信誉清零并回溯削权其全部历史记录。
- 认证流水线核算 maturity 时只认加权和，权重规则开源、可复算（S10：不接受手工声明）。
- 否决票（`made_worse` / 回滚失败）任何人可投，但需附结构化细节，触发强制人工复审。

## 4. 人力如何被 involve、成果如何普惠（激励设计）

三种角色，三种持久产出：

| 角色 | 做什么 | 持久产出 | 激励 |
|---|---|---|---|
| **验证者**（任何用户） | 真实环境跑 Skill 后提交 attestation | attestation 文件 | Skill 页面署名"verified by"；信誉分；验证数排行榜 |
| **评审人**（领域专家，申请+历史记录准入） | 审 `field_verified → certified` 的晋级；审否决票 | review 记录（签名文件） | Skill 页面署名"certified by"；领域评审人头衔（网络/智算/K8s 各设组） |
| **贡献者** | 写/改 Skill、解析器、测试 | git 提交历史 | 常规开源署名 + 其 Skill 的 attestation 数即影响力指标 |

关键设计决策：
- **所有三类产出都是仓库里的签名文件**，不是平台数据库里的一行记录——项目被 fork、
  registry 迁移、公司倒闭，社区的验证劳动都不丢失（R12）。
- 偏离记录（deviations）是隐藏的金矿：某步骤被反复偏离 = 决策树该改了，
  流水线自动聚合偏离并生成改进 issue。
- 企业内网用户（无法外发数据）可运行**私有 registry**：attestation 留在内网，
  但格式相同——企业内部照样形成自己的认证层，与公共层叠加使用。

## 5. 与运行时的联动

- 运行时加载 Skill 时按 maturity 决定可用档位（§1 表）；
- 呈现任何建议时附带徽章与数字（"🟢 已被 17 个环境验证，最近验证 3 天前"）——
  这行字本身就是产品的信任界面（R8）；
- `draft` Skill 的每条指导下方附"这条建议来自模型生成，尚未经实地验证"。

## 6. 冷启动策略

发布 v1 时没有社区，先自建三层：
1. 官方 Skill 全部过仿真 → 带 🔵 出厂；
2. 项目方与种子用户（内测企业）在真实环境跑出首批 🟢；
3. 邀请 5–10 位有公开声誉的运维专家做首批评审人，产出首批 🟡。
发布时页面上必须已经存在三种徽章的真实样本，让新用户第一眼就理解这套体系。
