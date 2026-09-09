# 评审队列（REVIEW-QUEUE）

> Opus 4.8 在执行中发现的设计缺陷、schema 表达不了的场景、语义存疑点，记录在此，
> 由 Fable 5 在评审轮统一裁决。格式：编号 / 发现于哪个任务 / 问题描述 / 建议方案（可选）。

---
# 第一轮裁决（Fable 5，2026-07-09）

**R-1 采纳 / R-2 采纳变体 / R-3 采纳 / R-4 采纳（统一到表达式文法）。**
规范性细节已写入 `docs/03-skill-schema.md §7`（v0.2 决议），实施任务在 TODO-opus 第二轮 P-1。
四条均保留原文于下，状态：已裁决，待实施后关闭。

> **[P-1 关闭 R-1~R-4 + F-2/F-4，2026-07-09 Opus]** v0.2 已实施并迁移全部 31 Skill：
> schema 增 params/ask.binds/verify(assert+note)/rollback.advisory；exprlang 增 avg/sum +
> parse_template_ref；S9 升 ERROR；新增 S11(回滚空转)；facts 注册表 tools/facts.yaml + docs/06 +
> FACTS 成员告警。校验 31/31、pytest 100/100 全绿。R-1/R-3/R-4/F-2/F-4 关闭；
> R-2 部分关闭（verify.assert 已强制表达式；watch.expect/abort_if 按裁决保留散文）。
> **遗留给 Fable 复核**：迁移中 verify.assert 引用了若干"解析器应产出但尚未实现"的字段
> （service_active/metrics_ok/rollout_succeeded/mount_rw/pcent_before 等）——这些是解析器契约，
> 记为新条目 R-5，待解析器补齐时对齐。

## 对抗评审新发现（F 系列）

审查范围：29 个 Opus 生成 Skill 全部通读，6 个带写操作的逐行审
（fs-readonly / k8s-rollback / clock-drift / agent-deploy / systemd-unit-failed / rollout-stuck 相关动作）。

- **F-1（已修复）** `raid-degraded` 把设备写死为 `/dev/md0`，多阵列机器会查错对象。
  已改为遍历 `/dev/md[0-9]*`。教训入生成规范：**设备/实例名永远不许写死**。
- **F-2（裁决→S11）** `fs-readonly` 的 fsck 回滚是 echo 占位——形式过 S1、实质违宪(R1)。
  裁决：新增 S11"回滚不得空转"，human_only 节点允许显式 `advisory: true`。见 docs/03 §7.5。
- **F-3（待修，P-2）** `iops-saturated` 的 `latency_vs_throughput` 节点是退化分支
  （所有分支同一去向），要么区分出口要么删节点。
- **F-4（裁决→facts 注册表）** facts 单位无规范：`memory-leak` 里 `rows[0].rss > mem_total * 1024 * 0.4`
  隐含 rss=KB、mem_total=MB 的约定但无处声明。见 docs/03 §7.6，P-1 实施。
- **F-5（待修，P-2）** k8s 域 exec 类检查假设容器内有 nslookup 等工具（distroless 镜像会失败），
  相关节点需补 cautions 与降级命令（如改用 `kubectl debug`）。
- **F-6（待建，P-3）** `agent-deploy` 引用的 `opsaxiom-deploy` 工具不存在（与上轮 quarantine 同型问题）。
- **F-7（风格，P-2）** `conntrack-full` 用 `count`/`max` 作字段名，与内置函数同名——能跑但脆弱，
  改名 `ct_count`/`ct_max`。生成规范补充：**字段名不得与函数名冲突**。

## 抽查结论

- 高危 Skill 的领域判断整体合格：fs-readonly 的"硬件错误禁 fsck"、raid 的"重建期不碰阵列"、
  clock-drift 的"生产慎 step"、k8s-rollback 的"DB 迁移不随代码回滚"等关键 caution 都在且正确。
- `chronyd -Q`（只查不设）用法正确；`rollout undo --to-revision` 的事务语义正确。
- **maturity**：`host.storage.capacity.disk-full` 依据 O-6 仿真证据晋级 `sim_verified`
  （Fable 代行流水线，证据记录于该 Skill 的 provenance.source_trace）；其余 30 个维持 draft——
  它们的 tests 场景还没有可执行的仿真上下文（只有 disk-full 有），这是下一轮 P-5 的主要产出。

---

## R-1 (O-2) S9 无法强制：schema 缺少变量声明位

- **发现于**：O-2 实现校验器时。
- **问题**：S9 要求"模板变量 `{{x}}` 必须在 facts/discovery/ask 有来源"，但当前 schema 没有地方
  声明 action/ask 节点产出的变量名，也没有"skill 入参"的声明位。金标准里 `{{mount}}`、
  `{{files}}`（来自 ask 选择）、`{{svc}}`、`{{qdir}}`、`{{threshold}}`、`{{peer_ip}}` 等
  都是运行时上下文/入参，无法回溯到 facts 或 discovery。
- **本轮处理**：S9 降级为 WARNING（不阻断），金标准照常通过。校验器已能列出无来源变量。
- **建议（待 Fable 定夺）**：schema v0.2 增加两处声明：
  1. `metadata.params: [{name, source: alert|user|derived, desc}]` —— 声明 skill 级入参；
  2. `ask` 节点增加 `binds: <varname>` —— 声明该 ask 的答案绑定到哪个变量。
  之后 S9 可升为 ERROR：变量来源 = facts ∪ discovery.id ∪ params ∪ ask.binds ∪ builtin{sid}。

## R-2 (O-2) `verify.expect` / `watch.expect` / `abort_if` 是自由文本

- **发现于**：O-2。金标准里这些字段混用"表达式"（`rows[0].pcent < 90`）与"散文"
  （`使用率下降`、`服务 active 且...`）。当前只有 `branch.when` 被 S5 强制为可解析表达式。
- **影响**：运行时引擎/模型需要"解释"这些散文断言，弱模型上可靠性存疑（与 R7 张力）。
- **建议**：v0.2 考虑要求 `verify.expect` 也走受限表达式（保留 `expect_human` 作为附带说明），
  或至少要求 `verify` 提供一个机器可判的 `assert` 字段 + 可选散文 `note`。


## R-3 (O-3) 表达式缺 avg/sum 聚合函数

- **发现于**：O-3 批量生成 host 域诊断 Skill 时。
- **问题**：docs/03 §5 允许的聚合仅 `max/min/count/any/all/delta`。诊断里大量需要"多次采样取均值"
  的判断（vmstat/iostat/mpstat 的 wa、util、steal 等），没有 `avg`/`sum` 很别扭。
- **本轮处理**：一律改用 `count(rows[].x > 阈值) >= k` 表达"k 个以上采样超阈值"，语义上更稳健
  （不受单次尖峰影响），已能覆盖需求，但对"求和类"指标（如总带宽）仍不便。
- **建议（待 Fable 定夺）**：v0.2 在允许函数集加入 `avg` 与 `sum`（纯确定性、对弱模型无负担），
  同时保留 count 模式作为推荐写法。若同意，需同步更新 docs/03 §5、tools/exprlang.py 的 _FUNCS、
  以及本文 R-3 关闭。

## R-4 (O-3) 模板变量命名：数组元素字段引用（rows0_comm 之类）

- **发现于**：O-3。`done` 节点的 summary 想引用"排第一的进程名"，schema 的 `{{}}` 模板不支持
  `{{rows[0].comm}}` 这种带下标的路径（点分变量名限制），只能退而用 `{{rows0_comm}}` 这种拍平写法，
  需要引擎在渲染时提供别名映射。
- **建议**：v0.2 明确模板变量语法是否允许下标/点路径（如 `{{discovery.free.available_pct}}`），
  并规定 discovery 输出如何绑定到模板命名空间。当前 summary 里的变量渲染契约未定义，记此备忘。

## R-5 (P-1) verify.assert 引用了未实现的解析器字段

- **发现于**：P-1 迁移各 action 的 verify.expect(散文) → assert(表达式) 时。
- **问题**：为让 assert 机器可判，我引用了解析器"应当产出"的结构化字段：
  service_active、metrics_ok、rollout_succeeded、unready_pods、mount_rw、fs_errors、pcent_before。
  这些字段的解析器尚未实现（服务健康类、kubectl status 类），且 pcent_before 是"执行前基线"，
  需要引擎在 action 前快照——属运行时引擎契约，当前 sim 不覆盖 verify。
- **不阻断**：assert 只被 S5 校验语法（已通过），不校验字段来源；sim 不执行 verify。
- **建议**：解析器补齐时（后续轮次）定义这批"健康类字段"的标准命名与解析器；
  引擎实现 verify 前快照（pcent_before 之类）。届时可加一条"assert 字段须有解析器产出"的校验。

## R-6 (P-5) S8 对纯诊断 Skill 不可满足——已精化，待 Fable 批准

- **发现于**：P-5 给诊断 Skill 写仿真、准备晋级时。
- **问题**：S8 原文要求 sim_verified 必须有 `rollback_assert:true` 测试，但**纯 Diagnostic Skill
  没有 action → 没有回滚 → 永远无法晋级**。这显然违背意图（诊断 Skill 也该能 sim_verified）。
- **本轮处理（需 Fable 追认）**：精化 S8——`rollback_assert` **仅对含 action 的 Skill 强制**；
  纯诊断 Skill 满足"tests 非空 + 所有路径 sim 通过"即可晋级。validate.py 与 promote.py 已同步实现。
  据此已晋级 16 个 Skill（10 host 诊断 + agent-deploy 含真实部署回滚 + 5 k8s 诊断），连同 disk-full 共 17 个 sim_verified。
- **建议**：Fable 在 docs/03 §7 或 docs/05 §1 明确这条口径（诊断类 vs 变更类的晋级门槛差异）。

---
# 第二轮裁决（Fable 5，2026-07-09）

## 裁决

- **R-6 追认关闭**：S8 精化（rollback_assert 仅对含 action 的 Skill 强制）符合设计意图，
  口径已写入 docs/05 §1（诊断类 vs 变更类晋级门槛）。Opus 的实现保留。
- **R-5 方向确认，保持 open**：健康字段属解析器输出契约、`*_before` 属引擎快照契约，
  规范见 docs/03 §7.6b；实施在第三轮 Q-2（解析器注册表带字段声明 + assert 字段校验）。
- **诚实性问题 ①（network 域领域正确性）**：10 个 Skill 逐个通读。
  合格项：光功率用模块自带 DDM 阈值而非写死数值（好设计）、OSPF 状态机映射正确
  （ExStart/Exchange=MTU、Init=单向、2Way 多路访问正常）、MTU 黑洞 df-bit 用法正确、
  received-routes 需 soft-reconfig 的 caution 正确、acl-block 守住了 D1 只诊断边界。
  **不合格项见 F-8（已修复）**。
- **诚实性问题 ②（sim_verified 证据强度）**：接受 context_walk 作为 🔵 门槛，但必须
  分级记录并展示（docs/05 已定稿）。17 个晋级维持有效；真实靶机执行器落地后逐步补跑升级。

## F-8（二轮新发现，最重要）投影语义缺陷导致静默误判

- **发现**：stp-loop 与 acl-block 用 `any(A) and any(B)` / `any(A and B)` 表达"存在同时满足
  A、B 的元素"——前者是独立存在判断（正常交换机也命中），后者在求值器下列表参与 and 按
  "非空"求真（deny 零命中也判为在拦）。**两个 Skill 在正常环境会误报故障**，已实测复现。
- **处置**：两处已由 Fable 改为解析器派生标量字段（inconsistent_ports / deny_hit_count）；
  规范落 docs/03 §7.6a + docs/07 B6；**S12 静态检测**列入第三轮 Q-1。
- **幸运**：两个 Skill 均为 draft 未晋级——但这恰说明需要 S12：这类错误校验器今天挡不住，
  又恰是"树逻辑"层错误，context_walk 仿真若场景写得顺着错误语义走，也可能测不出来。
- **对应解析器契约新增字段**：stp 解析器须产出 `inconsistent_ports`、`tcn_rate`；
  acl 解析器须产出 `deny_hit_count`（并入 R-5 的字段契约清单）。

## F-9 (Q-3, 真实执行器发现) disk-full 的 locate_mount 命令列序与 df-v1 解析器不匹配

- **发现于**：Q-3 真实靶机执行器首次在本机跑 disk-full——它落到了 escalate 而非预期分支。
- **根因**：`locate_mount` 的命令是 `df -B1 --output=target,pcent,avail {{mount}}`（pcent 在中间），
  但它声明的解析器 `table/df-v1` 期望 pcent 在**最后一列**（discovery 的 df 命令是
  `--output=target,size,used,avail,pcent`）。同一 Skill 里两条 df 命令列序不同却共用一个解析器，
  真实解析时 locate_mount 的 pcent 落空 → rows 为空 → 走 otherwise escalate。
- **意义**：这正是 context_walk 测不出、real 模式一跑就现形的一类 bug（评审二轮预言过）。
  证明真实执行器的价值。
- **处置建议（留给 Fable，因属金标准）**：把 locate_mount 命令列序改为
  `--output=target,avail,pcent`（pcent 末列），或给它单独的解析器。一行修复。
- **现状**：3 个纯诊断（load-high/swap-thrash/memory-leak）真实模式跑通并升级证据为
  real_roundtrip；disk-full 待此 F-9 修复后可补跑升级。

## R-7 (Q-5) 检查节点的标量输出无模板引用约定

- **发现于**：Q-5 写 middleware Skill 时，done.summary 想引用 `{{max_query_time}}`（解析器标量输出）。
- **问题**：§7.4 只给了 rows/output/lines 三个"节点输出裸引用根"，解析器产出的**标量字段**
  （max_query_time/seconds_behind 等）在 summary 里无合法模板写法，S9 判其无来源。
- **本轮处理**：把这类引用从 summary 移除（改为泛化描述），不阻断。
- **建议（待 Fable 定夺）**：v0.3 给"当前节点标量输出"一个约定，如 `{{node.max_query_time}}`
  或把解析器标量并入 `output` 命名空间 `{{output.max_query_time}}`；渲染引擎据节点 parser 的
  scalars 声明解析。与 Q-2 的字段契约天然对齐（parser 已声明 scalars）。

---
# 第三轮裁决（Fable 5，2026-07-09）

## 裁决

- **F-9 已修复（Fable）**：disk-full 全部 4 处 df 命令统一为解析器期望列序（pcent 末列），
  两个 verify 补 parser 声明；真实模式复跑正确走到 false_alarm（本机<90%，语义正确），
  4 场景重新晋级，证据升级 **real_roundtrip**。教训入 docs/07（见 B7 新增）。
- **R-7 采纳**：节点标量输出经 `{{output.<scalar>}}` 引用，规范入 docs/03 §7.6c，实施 T-3。
- **middleware 10 Skill 抽查合格**：关键领域判断全部正确——复制 1062/1032 的反 skip 立场
  （这是 DBA 的生死线）、Seconds_Behind_Master 线程停时为 NULL、noeviction 拒写语义、
  --hotkeys 需 LFU 的 caution、UNLINK vs DEL、分区数=消费并行上限、URP 的容错余量紧迫性。
  **一个 nit（T-4）**：mysql Skill 用的 performance_schema.data_lock_waits /
  replication_applier_status_by_worker 是 8.0 表名，5.7 对应 sys/information_schema——
  platforms 缺版本限定，需补 `{engine: mysql, versions: ">=8.0"}` 或加降级命令 caution。
- **S12/FIELD 严格度**：接受现状。S12 拦"静态可判定"的投影错误（裸投影入布尔/顶层）；
  `any(A) and any(B)` 的同集合语义误用**原理上不可静态判定**（需要知道作者意图），
  由 docs/07 B6（源头）+ 解析器派生标量（正道）+ 评审（兜底）三层防御。记录此残余风险为已知边界。
- **attest 的 PII 防护**（schema 强制 version_bucket 只能 N.x）是好设计，超出任务书要求，认可。

## 关闭状态汇总（三轮后）

R-1~R-4 关闭 / R-5 部分闭合（6 个健康解析器已实现，引擎快照 pcent_before 待 T 轮）/
R-6 关闭 / R-7 已裁决待实施(T-3) / F-1~F-9 全部关闭。

---
# 第四轮裁决（Fable 5，2026-07-09）

## 裁决

- **运行时 CLI 产品体验（导航档）**：**符合最初设想，验收通过**。变更简报的呈现
  （影响面/盯什么/何时中止/渲染后的回滚命令原文）正是"踏实感产品化"的样子；审批默认拒绝
  是正确的安全默认；"Agent 不代执行"的措辞边界清晰。**四个打磨项（U-1）**：
  ①审计缺 verify 结果与粘贴输出摘要；②action 未确认直接 escalate，应提供"跳过走其他分支"选项；
  ③无断点续跑；④模板渲染缺失字段输出空串（demo 里出现"持有已删除文件共 （详单见上）"的空洞）
  ——缺失应显示 `⟨?⟩` 占位而非静默空串。
- **F-10（aicomp 抽查，已修）**：xid-error 把 **XID 92（可纠正单比特错误率预警）误归入
  不可纠正硬件类**，会对好卡触发 drain+RMA；且分支清单与 caution 清单不一致（caution 是对的）。
  已改归显存退化监控类并重新晋级。教训入 docs/07 **B9**（枚举清单双向核对）。
  其余 9 个 aicomp 合格：XID 13/31/43/45 软件类、48/94/95 硬件类、63/64 退休、79 掉卡均正确；
  DBE 禁重试、watchdog 语义、NCCL 退化 TCP、木桶效应判断均对。
- **or-bug 启示：采纳，新增 S13"求值冒烟"**（docs/03 §7.6d）：所有 when/assert 入库前在
  空 ctx 上实跑求值器一次。语法校验(S5)证明"写得像表达式"，S13 证明"求值器真吃得下"。
  已实现，全库 61 Skill 通过。
- **F-11（记录，U-2）**：slow-node/gpu-util-low 引用未实现的 `opsaxiom-collect`（F-6 同型）。
  该模式已出现两次，追加规则：**Skill 引用 opsaxiom-* 自研工具时，工具必须已存在或在同轮任务
  中排期**——写入 docs/07 E 清单第 6 项（由下轮执行时补）。

## 里程碑判定

四轮累计：61 Skill（38 sim_verified 含 xid 复晋）、S1-S13 十三条语义规则、
证据分级流水线、attest 骨架、**导航档运行时 CLI + 症状匹配**。
产品已具备最小可演示形态（diagnose → run → 简报 → attest 提示）。
下一个产品级里程碑是 **IM 渠道接入**（留存的生命线，docs/01 §2）与 **Skills Hub 雏形**。

## R-8 (U-3) proc 纯生成域挤进 Diagnostic 决策树 schema——可用但不理想，待 Fable 定夺

- **发现于**：U-3 生成 proc 域（变更单/故障报告/交接摘要）。
- **背景**：Fable 预判"proc 纯生成类可能挑战 schema，表达不了就记 REVIEW-QUEUE"。实测结论：
  **能表达，但是借道**。关键约束两条：
  1. `tree` 是 schema 顶层 required——纯生成 Skill 也必须有一棵树，无法只有"输入 + 模板"。
  2. `ask` 节点是**多选分支**（options + goto），不是自由文本采集。自由文本靠 `metadata.params
     (source:user)` 承接，done.summary 用 `{{param}}` 渲染。
- **我的处理（未硬凑，反而找到合理形态）**：把 proc 建成"params 采集自由文本 + 一个**真实决策**
  路由到不同模板"——change=风险级→审批链、incident=SEV 级→报告详尽度、handover=有无活跃故障→
  交接要求。这个分支是有语义的（SEV1 报告≠SEV3），不是为凑树而凑。三个已 sim_verified。
- **但仍有别扭处**，留给 Fable 判断是否值得演进 schema：
  1. proc 借用 `kind: Diagnostic` 名不副实（它不诊断）。是否加 `kind: Playbook`/`Generator`？
  2. 生成类没有 discovery/check，`connectors: [human]` 是我造的占位值（schema 只要求非空数组）——
     是否应有正式的"无连接器/人工"枚举？
  3. 多行模板塞在 done.summary 的单个字符串里，可读性差。是否给终态一个 `artifact:` 块
     （带 `template` 字段）比塞进 summary 更合适？
- **不阻断**：当前形态可用、已验证、已交付 3 个。以上是"要不要为 proc 演进 schema"的设计问题，
  纯属 Fable 职权（我不改 schema）。若维持现状，我把这套 proc 写法补进 docs/07 作为规范即可。

---
# 第五轮裁决（Fable 5，2026-07-10）

## 裁决

- **U-1~U-4 验收通过**。resume 的"控制 token 不覆盖 state"处理正确；E-6 当场拦住自己
  （collect events）是规范生效的最好证据；proc 域"params 采集 + 真实决策路由"的形态
  比硬凑决策树高明。
- **R-8 裁决：维持现状，暂不动 schema**。理由：① proc 三个 Skill 的分支确有语义
  （审批链/详尽度/交接要求），Diagnostic 借道成立；② `kind: Playbook` 与 `artifact:` 块
  的真实需求方是 Hub 网站的渲染与分类——Hub（第六轮 V 系列）落地后按实际渲染需求再定，
  避免为未出现的消费者改 schema。proc 写法作为规范补 docs/07（V-6，含 connectors:[human]
  官方化说明）。
- **F-12（五轮抽查发现，已修）**：obs/false-positive 分支顺序错误——阈值判据排在抖动判据前，
  抖动告警采样瞬间值常落回阈值下，会被误判为"阈值不当"并给出错误的改阈值建议。
  已调序（flapping 先判）。**教训入 docs/07 C7：分支顺序即优先级，
  "更特异的判据必须排在更宽泛的判据前"**（与 xid 的 79 先于 ECC 组同理）——V-6 落笔。
- **collect mock 边界：通过**。确定性 + stderr 诚实标注 + --from-file 真实集成点，三要素齐。
  约束一条：**mock 数据永不参与 promote 到 field_verified**（field 级证据必须 real 来源），
  promote.py 已天然满足（field 靠 attestation 而非 sim）。
- **U-4 信任模型：TOFU 作为过渡正确**，终态信任锚在 Hub registry 的 keyring
  （维护者签核、hub sync 分发）。设计已入 docs/08 §3.3。

## 抽查结论（obs/sec 9 个诊断）

合格：暴破"失败后成功=疑似攻破最高优先"、漏洞"KEV/可修复优先于 CVSS"、
target-down 按 lastError 三分、cert 分桶重叠被 expired→7d→30d 短路顺序化解、
abnormal-login 守住"只诊断不封禁"的 D1 边界。不合格项仅 F-12（已修）。

## 产品缺口盘点（应发起人要求）

经验捕获/一键认证打通/hub 拉推/社区网站/一键部署/用户手册——**全部缺失或半缺失**，
架构设计已出（docs/08），实施为第六轮 V-1~V-6。这是"人侧飞轮"，与前四轮的
"模型侧飞轮"合起来才是完整社区。

---
# 第六轮裁决（Fable 5，2026-07-10）

## 裁决

- **V-1~V-6 验收通过**。人侧飞轮闭环成立：捕获(from-session/record/向导)→lint 缺口清单→
  流水线晋级→push；pull 三门→隔离目录→用。docs/10 从运维视角合格（说人话、有起步卡）。
- **F-13（六轮评审发现，已修）**：Dockerfile 在**构建期** `--keygen`，把 Ed25519 私钥
  烤进镜像——所有容器共享同一私钥，attestation 签名失去主体含义（谁验证的？）。
  已删该行，密钥由 attest 首次签名时在容器内惰性生成。教训入 docs/07 **B10：
  凭据/密钥永不在构建期产生或进入制品**（镜像/包/仓库），只在运行时首次需要时生成。
- **pull origin 标记合法性实测通过**（provenance 未禁额外字段），信任门自洽。
- **三道安全门信任模型：MVP 够用**。keyring 治理（谁签核 trusted.pub）= Hub 运营问题，
  记 **R-9 open**：待第一个真实 registry 建立时定（建议：registry 维护者 = keyring 签核人，
  PR 双人复核入 trusted.pub）。不阻塞当前轮次。
- **静态站 vs 动态服务边界：维持静态优先**。账号/评论/下载计数等动态需求出现前不上服务端。
- **V-3 改进项（W-3）**：一键认证预填 outcome 恒为 resolved——若人反馈了 👎，
  应引导选 partial/failed（负面 attestation 同样是社区的宝贵信号，docs/05 本就允许）。

## 交互入口检查（发起人指令）

- **实测：裸敲 `opsaxiom` 报错**（argparse required=True）。docs/08 §4.2 设计的"入口 A
  交互态"六轮任务书未排——缺口坐实。
- **裁决：Terminal REPL 是产品默认交互入口**（发起人拍板：终端最普遍，不是所有运维能接
  飞书/钉钉；IM 是增强渠道不是底座）。规格已写入 docs/08 §4.2a（自然语言当一等公民、
  数字选择、导航档原地跑、Ctrl-C 语义、无 TTY 降级、防镀金边界），第七轮 W-1 实施。

---
# 第七轮裁决（Fable 5，2026-07-11）

## 裁决

- **W-1~W-4 验收通过，REPL 产品体验合格**。pty 真 TTY 实测：欢迎屏、中文症状匹配
  （"gpu 掉卡 xid 79"→掉卡 Skill top-1）、info、quit 全程顺畅；无 TTY 降级正确；
  防镀金边界（非 chatbot/不做多轮澄清）守住了。"敲一个词就能用"成立。
- **F-14（评审发现，已修）**：REPL `resume` 列出会话后选中却"没有进度"——`_run` 用
  skill_id 重新派生 sid，丢了状态文件的真实 sid（子命令/自定义 --sid 的会话全中招）。
  已改为 `_resume_pick` 传状态文件名里的真实 sid。教训：**标识符只能有一个事实来源，
  展示层拿到什么就传什么，不许重新推导**（docs/07 工具类通则，X-5 落 T 系列条目）。
- **F-15（诚实性）**：HANDOFF 声称"接缝 diagnose --json 已具备"，实测不存在
  （`--json` 未实现）。虚报比缺功能严重——交接文档里的"已具备"必须是实测过的。
  兑现列入 X-1；**交接规则补一条：HANDOFF 中所有"已具备/已实现"表述在写入前必须实测**。

## 第八轮方向（发起人此前定的候选依序推进）

IM 是增强渠道（Terminal 是底座，六轮已定），本轮做 webhook→IM 的最小闭环 + 信任治理收尾。

> **[X-3 关闭 R-9，2026-07-11 Opus]** keyring 治理落地：hub keyring list/add/remove/export
> 管理本地 trusted/；registry 侧签核流程（维护者=签核人，PR 双人复核入 trusted.pub，
> hub sync 分发）写入 docs/08 §3.3a。"签名有效"（密码学）与"签名者可信"（治理）分离，
> TOFU 有了收敛路径。R-9 关闭。

---
# 第八轮裁决（Fable 5，2026-07-11）

## 裁决

- **X-1~X-5 验收通过**。特别肯定两处诚实性自纠：run_real 只认 linux 键会让 k8s real
  场景空跑却谎报 real_roundtrip（Opus 自己发现自己修）；F-15 兑现且固化为 T-2。
- **F-16（评审发现，已修，严重）**：kubectl 白名单可被 **shell 注入穿透**——
  `kubectl get pods; rm -rf /`、`` `rm -rf /` ``、`$(reboot)` 全被当只读放行。
  token 判定挡不住元字符拼接。修复：只读 kubectl 绝不需要 `;|&$()<>`` 反引号`，
  出现任一 shell 元字符即拒。教训入 docs/07 **T-3：白名单校验必须先拒 shell 元字符，
  再看动词——动词白名单挡不住命令链**（X-5 已建 T 系列，本条由九轮落笔）。
- **F-17（评审发现，已修，R11 违规）**：webhook 卡片把告警 description **原样回显**进
  钉钉/飞书——自由文本里的密码/连接串/token 会被推到外部服务。Opus 声称"卡片不含凭据"
  不成立（它防的是自己不加凭据，没防告警自带）。修复：**匹配文本与展示文本分离**——
  匹配用全文（本地），外发卡片只用结构化 alertname+instance 且过脱敏正则
  （password/token/user:pass@/常见 token 形态）。教训：**凡出站到外部服务的文本，
  只能来自结构化字段+脱敏，不许透传自由文本**（T-4，九轮落笔）。
- **field 独立性判定：通过**。贪心最大独立子集（attestor 与 env 分桶双维去重）符合
  docs/05 §3 本意；同人 3 份只算 1 份独立，测试覆盖正确。
- F-16/F-17 已补回归测试（test_sim/test_webhook）。

## 反思（给两个模型的共同教训）

F-16/F-17 同根：**安全边界的测试必须是对抗性的**。Opus 的测试全是"正例放行、已知反例拒绝"，
没有测"攻击者会怎么绕"。九轮起，凡涉白名单/脱敏/验签的交付，任务书将强制要求
"对抗用例"小节（注入/夹带/绕过各至少 2 例）。

## G-3 编写判断（Opus，待 Fable 追认）
任务书原定 act_backup_first 为 human_only action。实测：human_only + advisory 回滚的
action 在现行 promote(S8) 下无法晋级 sim_verified（拿不到真回滚往返，fs-readonly 因此
至今 draft）。判断：备份是"产生数据"非"破坏性变更"、无可回滚状态，且 human_only 本就
Agent 永不执行只出指导——故建成强指导 done_dying 节点（语义等价，对齐 fs-readonly 的
hardware_exit done 写法），G-3 保持纯 Diagnostic，得以 sim_verified。请 Fable 追认或
指示是否需要 schema 层支持"无回滚 human_only action"的晋级路径。

## H-1 草稿抢救结果（Opus）
- 删脏数据：skills-drafts/captured（id=智算，向导测试残留）。
- 建场景自动求解器 tools/authoring/scenario_solver.py：读树反推走到 done 的
  context_walk 场景，合成满足 when 的 node_ctx，用真实 exprlang+run_sim 验证。
- **抢救 19 个 draft → sim_verified**（含金标准 stp-loop/ospf 及大量 network/k8s/host）。
- 剩 5 个留 draft（都含 action 且声明 rollback_assert，S8 要求真回滚往返，
  现有 mock 回滚器不覆盖其回滚类型）：fs-readonly(human_only fsck)、
  systemd-unit-failed(restart)、clock-drift(timesync restart)、
  k8s.release.rollback(kubectl rollout undo transaction)、bgp.neighbor-down(配置类)。
  待 Fable 决定：是否为 restart/transaction 类回滚补 mock 回放器（新回滚模式=Fable 设计，B13）。

## B 轮收尾（Opus，第十七轮，待 Fable 评审）

**v2 档位真机两档全验证通过**（白名单档/root 档/没通道不给假 root 三场景，见 HANDOFF
第十七轮），验证途中暴露并修复 8 项。以下是要点与需 Fable 复核的判断：

1. **失败探针转贴回的信任边界**：自动执行失败/超时的探针按 cmd 并入手动桶走 nonce
   贴回——错误输出（如部分超时后远端已产生的stdout 残段）不会入事实库（连接器
   异常路径直接 raise，无 stdout），但**贴回内容本身**依然是"人在真机跑的"同一
   信任级别，nonce 防伪造边界不变。请复核"失败转贴回"是否引入把别的命令输出
   贴错位置的新混淆面（现状：贴回 UI 每条单独显示命令行，OnTrack）。
2. **SSHConnectError 边界**：ssh_conn 用 `connected` 标志位区分 connect 前/后
   失败。banner reset 恰好发生在 connect 成功后、exec 前——归"连接失败"是刻意的
   （ hadn't started executing）。若 Fable 认为应以 exec_command 调用为界，改一行。
3. **fail-fast 条件**：全部探针失败 且 全部为连接级失败 且 manual 桶为空 → 短路。
   连接级判定靠错误文本含"连接"——脆弱（异常消息改文案会失灵），待 Fable 定夺
   是否升级为结构化 status（error 报告加 `err_kind: connect|timeout|exec`）。
4. **paramiko 全局日志静音**（连接器 import 即 CRITICAL）：影响面是"任何 import
   ssh_conn 的进程"——如果未来需要在排障时看 paramiko 内部日志，需要临时开关。
5. **registry 同步纪律**（流程教训，建议固化）：修 skill 探针必须同时改仓库存档
   与 ~/.opsaxiom/hub/registry（独立 git 仓库）两份——本轮 inode-exhausted 上轮
   漏修 + registry 未同步，真机暴露。是否在 docs/07 生成规范里立条？

## B 轮全量修复清单（对账）

df -i --output 互斥（仓库+registry 双修，inode-exhausted 是上轮漏网）；批量路径
白名单授权问答；提示语按目标去重；socket.timeout 空 str 兜底（连接器/审计/sweep
三处）；失败探针转贴回 + 连接级全灭 fail-fast；连接器异常 decision=error 审计；
paramiko traceback 静音；SSHConnectError/SSHError 分类；弃用告警入口过滤；
target list os 列 + grant/revoke picker 过滤。全部带测试，789 passed。

## 十七轮 Fable 返工对账（Opus，待 Fable 复核）

Fable 评审裁定"暂缓合并"附 3 返工 + 2 P2 补账，已全部执行（分支待合并）：
- **B-1（P0，已修）**：enroll 渲染 sudoers 把 (bin, prefix) 降成裸名 → 前缀全丢
  （`sudo -n systemctl restart nginx` 物理可达）。修复：gen_sudoers 复合型二进制
  （systemctl/journalctl/ip 等 _COMPOSITE_LEAD）只按已登记子命令/flag 前缀发条目
  （`bin p *` + `bin p` 双形态，sudoers fnmatch 语义），裸名仅限单用途二进制；
  复合型裸探针零条目 fail-closed；enroll/target_cli/gate._wl_member 全链路
  (bin,prefix) 同源（客户端成员判定 = 远端 sudoers，互证测试
  test_wl_member_prefix_mirror_matches_sudoers + 裸 systemctl 物理不在场回归）。
- **裁定 3（P1，已修）**：err_kind 结构化（connect/timeout/exec 按异常类，
  gate.err_kind 弃"错误文本含'连接'"）——gate/sweep 报告带 err_kind，
  repl fail-fast 按【目标×err_kind 聚合】判定死目标（全部 connect 才短路，
  活目标照常贴回）。对抗测试：rc 级失败 err 含"连接"→ err_kind=exec 不短路；
  双目标 web-01 全灭（短路）+ web-02 rc 级含"连接"（照常贴回入库）。
- **docs/07 T 补账（P2，已落笔）**：T-3（F-16 元字符）/T-4（F-17 出站文本）
  八轮裁决欠账 + 新 T-5（错误分类结构化）/T-6（registry 同步纪律，回应"是否立条"）。
- **发起人裁定（cat/grep）**：白名单语义=写侧焊死、读侧全盘（sudo -n cat 可读
  root 文件属接受范围），cat/grep 保留不移——白名单的价值在防写不在防读。
- **P2 未做**：_DENY 写动词补 systemctl/ip 子命令（黑名单补刀，白名单+前缀门后
  收益边际，Fable 认可延后）。

## 十七轮二轮返工对账（Fable 复核 F-19~F-23 后，Opus 执行，待 Fable 复核）

Fable 复核 17-RW（commit 0496d02）裁定"暂缓合并"，5 项返工已全部执行：
- **F-19（P0，已修，语义升级 v3）**：flag 前缀条目结构性禁止——flag 与命令词
  正交，`systemctl --failed *` 挡不住 `--failed restart`。复合型二进制只认
  gen_sudoers._RO_COMPOSITE_SUBCMDS 登记的【只读子命令】作前缀
  （systemctl is-active/status/show；timedatectl status/show；networkctl
  status/list；nvidia-smi dmon/topo），flag/裸名/名单外语义全部 fail-closed。
  代价（发起人确认）：journalctl -u（5 skill）/systemctl --failed（1）/
  ip neigh（arp-table-full 部分）/nvidia-smi flag 型（~24 条）转贴回或升 root 档。
- **F-23（P1，已修）**：numactl/taskset/chrt 等"策略+任意命令"执行器并入
  _INTERPRETERS 硬拒；sysctl/nvidia-smi/smartctl/chronyc/coredumpctl 等裸名
  写面入 _DENY_BINS（sysctl -w/GPU reset/删核转储不再物理可达）。nvidia-smi
  归复合型只放行 dmon/topo 纯观察子命令，query 形态 fail-closed。
- **F-18（P1，已修）**：-u skip 表删除（连带 sudo 变体 skip 一并撤）；_wl_member
  重写为 (target 精确二段) 判定 + 消费 extract 产物（不再手写镜像），
  startswith 宽松 fallback 删除；对称性测试双向断言
  （客户端 True ⟺ sudoers fnmatch 命中）。
- **F-20（P1，已修）**：test_b1_write_subcommand_physically_absent 重写——按
  段解析 spec（基名+第二段白名单），渲染带 bin_paths + 预览双形态；新增
  test_b1_gate_has_teeth 把 B-1 缺陷条目掺回渲染输入验证断言必炸。
- **F-21（P1，已修）**：repl failed_cmds 键从 cmd 单键改 (target, cmd) 二元组，
  already 集合循环内收敛（done.add）；测试改三件套（贴回盘问次数=1/入库
  target=web-02/貼回调用清单精确断言），恒真断言删除。
- **F-22（P2，已修）**：err_kind 死条目清除（socket.timeout 类名为 timeout、
  NetworkDownError 无定义者）；network 统一归 exec 的"有意保守"口径写入
  err_kind docstring 与 docs/07 T-5。
- **F-24/F-25/F-27（P2，Fable 二轮，已随手修）**：测试名单改 gen_sudoers 派生
  （F-24）；对称性近似函数补"裸名条目=任意参数"支路（sudoers(5) 手册语义，
  F-25）；flock/nsenter/unshare/setpriv/capsh/machinectl 入 _INTERPRETERS
  防御纵深（F-27）。近似函数教训补入 T-6。
- **F-26（P2，发起人裁定收窄，已执行）**：mount/conntrack/kafka-topics.sh 入
  _DENY_BINS（2026-09-09）——裸名条目=任意参数含写动作（挂任意盘/删状态表/
  写 Kafka），物理面关闭；自动路径不变（本就被客户端拦）；相关 skill 探针
  白名单档转贴回。回归测试 test_f26_write_face_bins_never_whitelisted。
- 真机回归清单（对应 HANDOFF 待办①）新增一条：`sudo -n systemctl --failed
  restart nginx` 须被远端拒（F-19 专属探针）。

## 十七轮真机回归暴露（2026-09-09，高等云肆重开通实测）

- **F-28（P1，已修）**：运行时执行门只读名单第三镜像分叉。真机白名单档批量
  取证实曝：`iotop -b … \|\| pidstat` 被执行门以"写/非只读命令"误拒——路由层
  `_wl_member` 吃 registry extract 产物（同源 ✓），执行门 `_readonly_ok` 却
  复用 sim/run_sim._ALLOW_LEAD 手写动词表（15 个 registry 收录命令缺席：
  iotop/numastat/getent/tail/top/getenforce/ibstat/perfquery/squeue/
  nstat/slabtop/pgrep/timedatectl/systemd-detect-virt/kafka-broker-api-versions.sh）。
  修复守 T-6：gen_sudoers 新增 lead_tokens（形态解析与硬拒过滤分离），
  gate 新增 _runtime_ro_leads() = registry 白名单 ∪ sim._ALLOW_LEAD 派生
  （不手抄），_readonly_ok 改吃派生名单；kubectl/mount 语义特判与 _DENY
  写词照旧。回归：test_runtime_gate_allows_registry_whitelisted_probes +
  test_runtime_ro_leads_consumes_registry_not_mirror（并集联动断言）。
  教训：T-6 的"镜像"范畴从测试助手扩到【任何消费方之外的名单拷贝】——
  运行时执行门自己的手写动词表同样是镜像。
