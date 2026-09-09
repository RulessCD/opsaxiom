# Skill 生成规范（Authoring Rules）

> 这份文件是"评审教训 → 下一轮生成 prompt"的飞轮载体（docs/01 §4）。
> 每一条都来自真实踩坑（编号对应 REVIEW-QUEUE 的 F/R 系列）。批量生成 Skill 前，
> 把本文件作为 system prompt 的一部分喂给生成模型。校验器能挡住的写进校验器，
> 挡不住的（领域判断、风格）写在这里靠评审兜底。

## A. 硬约束（校验器已强制，写错直接 ERROR）

- **A1 可回滚**：每个 action 必有 `rollback`，平台键与 `run` 一致（S1）。回滚不许空转
  （全是 echo/注释）——除非 `human_only: true` 且显式 `rollback.advisory: true`（S11）。
- **A2 变更简报**：`risk>=medium` 必有 `preflight`（watch/abort_if/approval:required）（S2）。
- **A3 verify**：每个 action 必有 `verify`，其 `assert` 必须是可解析表达式（S3+S5）。
  散文说明放 `note`，不放 `assert`。
- **A4 兜底分支**：每个 check 必有 `otherwise`（S4）。
- **A5 表达式**：`branch.when` / `verify.assert` 只用受限文法（字段引用、比较、and/or/not、
  matches、函数 max/min/count/any/all/avg/sum/delta）。禁止散文、禁止自造函数（S5）。
- **A6 命令语法**：网络设备命令必须过平台语法树，别跨平台混用（S6）。
- **A7 变量来源**：每个 `{{x}}` 的根必须来自 facts/params/ask.binds/discovery/节点输出/{sid}（S9）。
  新增入参在 `metadata.params` 声明；ask 的产出用 `binds` 声明。
- **A8 facts 注册**：用到的 fact 必须在 `tools/facts.yaml` 注册（含单位）（FACTS 告警）。

## B. 命令编写铁律（血泪教训）

- **B1 设备/实例名不写死**（F-1：raid 写死 `/dev/md0`）。用遍历/参数：
  `for d in /dev/md[0-9]*; do ...; done`，或 `{{device}}` 入参。
- **B2 字段名不与函数名冲突**（F-7：conntrack 用 `count`/`max` 作字段名）。
  给解析字段起领域名：`ct_count`、`ct_max`、`tw_count`，不要 `count`/`sum`/`max`。
- **B3 exec/命令不假设工具存在**（F-5：`kubectl exec ... nslookup` 在 distroless 挂）。
  凡依赖目标端有某工具的命令，必须在 cautions 写明降级路径
  （如 `kubectl debug --image=netshoot`、`pidstat` 退 `/proc`）。
- **B4 只查不改的诊断命令优先**。discovery 与 check 只读；任何写操作只能在 action 节点，
  且过 A1~A3。诊断命令别带副作用（别用会清计数器的命令，如某些 `clear`）。
- **B5 管道后半段别决定分支**。`when` 基于解析器结构化输出，不基于 grep 文本碰运气。
- **B10 凭据/密钥永不在构建期产生或进入制品**（F-13：Dockerfile 构建期 keygen 把私钥
  烤进镜像，所有容器共享同一把——签名失去主体含义）。镜像/离线包/仓库里只能有公钥和
  keyring；私钥一律运行时首次需要时惰性生成。同理适用于 token/口令：R11 的制品版。
- **B11 linux 命令保守主义（批量轮新增，H-0）**：linux 侧没有语法树校验兜底，
  批量生成时只用 coreutils/procps/iproute2 的常见形态；任何非常见 flag 或版本特有
  flag 必须附 caution 声明依赖并给降级命令。宁可两条朴素命令，不写一条炫技命令。
- **B12 解析器选择顺序（H-0）**：专用解析器 > generic 族 > 无解析器默认。用 generic
  时分支表达式只许引用该形态定义上必然产出的字段（kv-num→output.<归一键>；
  count→output.count/value；table→rows[].<归一列名>/output.row_count），归一键规则
  见 tools/parsers/generic.py。generic 无字段声明、FIELD 检查跳过——所以判据字段名
  必须与样例输出逐个核对（B7 的 generic 版）。
- **B13 批量轮的 Hybrid 限制（H-0）**：批量生成的 Skill 默认 Diagnostic；要带 action
  必须复用已验证的回滚模式之一（quarantine 隔离/deploy 卸载/mysqlvar 逆设置/
  k8s transaction），否则做成强指导 done 节点（G-3 判断的推广）。新回滚模式=Fable 设计。
- **B9 分支里的枚举清单必须与 caution 里的清单逐项一致**（F-10：xid-error 的 caution 写硬件类
  是 48/79/94/95，分支却多塞了 92——而 92 是可纠正错误率预警，误归会导致对好卡执行 drain+RMA）。
  错误码/状态码分组时，逐码对照官方文档，且 branch 与 caution 双向核对。
- **B8 用到版本/发行版特有的表、命令、字段，必须声明版本限定 + 给降级路径**（三轮评审 nit：
  mysql Skill 用了 8.0 的 performance_schema.data_lock_waits 等，5.7 没有）。
  `platforms` 写清 `versions`（如 `>=8.0`），并在相关节点 caution 里给旧版本等价命令。
  同理适用于发行版差异（rhel vs debian 的包名/路径）、内核版本特有的 /proc 项。
- **B7 命令输出格式必须与解析器契约逐列核对**（F-9：金标准 disk-full 踩坑）。
  同一 Skill 里多处调用同类命令（如 df）时，列序/格式必须统一且与所声明解析器的期望一致——
  作者"顺手少写两列"就会让解析静默落空、树走向 otherwise。最便宜的核对法：真实模式跑一遍
  （`mode: real` 场景），解析不出来立刻现形。
- **B6 同元素多条件必须用解析器派生标量**（F-8：stp-loop/acl-block 踩坑）。
  `any(xs[].a==1) and any(xs[].b==2)` ≠ "存在同时满足 a、b 的元素"——两个 any 各自独立；
  把投影直接 and 起来更是静默错误。正确做法：解析器产出 `xxx_count` 派生字段，表达式只比标量。
  （S12 静态检测在路上，但先从源头别写。）

## C. 决策树结构

- **C1 一个症状一个入口**，L3 叶子按用户口语命名（docs/04 §3）。
- **C2 不要退化节点**（F-3：iops 的 latency 节点所有分支同一去向）。每个 check 的分支
  必须导向**实质不同**的结论或下一步；做不到就删掉这个 check。
- **C3 树要浅、要有出口**。多数诊断 3~7 节点即可；每条路径都要能到 done 或 escalate。
- **C4 escalate 必可达**（S7）。常见错误：所有 branch + otherwise 都指向别处，escalate 悬空。
  把 otherwise 指向 escalate 是最稳的写法。
- **C5 分支求值抗尖峰**：多采样判断用 `count(rows[].x > 阈值) >= k` 而非 `avg`，
  避免单次尖峰误判（avg/sum 可用，但持续性判断优先 count 模式）。
- **C7 分支顺序即优先级——更"特异"的判据必须排在更"宽泛"的判据前**（F-12：obs/false-positive
  把"值未越界=阈值误报"排在"抖动"前，抖动告警采样瞬间值常落回阈值下，被误判成阈值不当，
  给出错误的改阈值建议）。求值是**首个命中即走**，所以：先判独占性强的特殊情况（KEV、抖动、
  XID 79 掉卡、暴破后成功登录），再判一般情况。写完从上到下问一遍"这条会不会把下面那条该管的
  情况提前截走"。同理见 sec/vuln 的 KEV 先于 critical、xid-error 的 79 先于 ECC 组。

## Cp. proc 纯生成域写法（R-8 裁决，五轮）

proc（变更单/故障报告/交接摘要）不诊断、无命令，但仍用 `kind: Diagnostic` + 决策树表达：

- **自由文本走 `metadata.params(source:user)`**，done.summary 用 `{{param}}` 渲染
  （ask 节点是多选分支，不是自由文本采集）。
- **树用来做一个真实的决策**，不是硬凑：变更→风险级→审批链；故障→SEV 级→报告详尽度；
  交接→有无活跃故障→交接要求。分支必须有语义差异（SEV1 报告 ≠ SEV3），否则就是退化节点（违 C2）。
- **无连接器的生成类写 `connectors: [human]`**（schema 要求非空数组；human 表示"输入来自人、
  无外部连接器"）。capability_level 仍是 `read`（它只读人给的上下文）。
- 晋级门槛不变：proc 也要 tests + 可执行 sim 场景（answers 驱动 ask 分支）才 sim_verified。

## D. 领域正确性（校验器挡不住，靠评审）

- **D1 边界即止**：涉及安全策略变更（改 ACL/防火墙）、物理操作（换盘/换线）、
  跨团队资源（对端设备/DB schema）时，Skill 只诊断到边界就 done/escalate，不越权执行。
  正面样例：bgp 的 acl_suspect、fs-readonly 的 hardware_exit。
- **D2 危险操作宁可 human_only**：fsck、DROP、核心设备硬复位——标 `human_only`，只出指导。
- **D3 回滚要诚实**：service_restore 型要说清"不是撤销而是恢复手段"；transaction 型
  （rollout undo / commit confirmed）说清确认窗口；快照型要求"执行前必须已有快照"。
- **D4 cautions 写"坑"不写"废话"**。每条 caution 是一个具体的、会让人踩坑的事实
  （"137=OOM 不是崩溃"、"df 有空间但报 No space 多半是 inode"、"steal 在物理机恒为 0"），
  不是"请小心操作"这种正确的废话。
- **D5 区分症状与根因**：退出码/状态先分类，根因区分放决策树内部，别在 taxonomy 层硬分。

## E. 生成流水线自检清单（生成后、提交前）

1. `python tools/validate.py <skill>` 零 ERROR（含 S1-S11、S6 语法、S9 变量、FACTS）。
2. 通读每个 caution：是否 D4 级"坑"？
3. 每个 action：回滚真能执行吗（非空转）？verify.assert 真能判吗？
4. 每条路径都能到终点吗？escalate 可达吗？
5. 有 action 的，配套写 tests 场景（P-5 起要求可执行 sim）。
6. **引用 `opsaxiom-*` 自研工具的命令，该工具必须已存在或在同轮任务中排期**
   （F-6 agent-deploy→deploy、F-11 slow-node/gpu-util-low→collect，同型两次）。
   生成引用未落地工具的 Skill = 埋一个"真实模式一跑就 command not found"的雷。
   核对：`ls tools/bin/opsaxiom-<name>` 存在，或该工具是本轮 TODO 的条目。

## T. 工具类通则（tools/ 下的 CLI 与引擎，非 Skill 内容）

- **T-1 标识符只能有一个事实来源**（F-14：REPL resume 列出会话用状态文件名的 sid，
  选中后 `_run` 却用 skill_id 重新派生 sid，丢了真实会话）。展示层拿到的 id/路径/sid，
  传给下游时**原样透传**，不许在下游重新推导——推导出的第二个"事实"迟早和第一个不一致。
- **T-2 交接文档里的"已具备/已实现"必须是实测过的**（F-15：HANDOFF 声称 diagnose --json
  已具备，实测不存在）。虚报比缺功能严重——下一个接手者会基于假前提做设计。写下"已 X"前
  先真跑一遍 X。同样适用于 commit message 与 REVIEW-QUEUE 的完成声明。
- **T-3 白名单校验必须先拒 shell 元字符，再看动词——动词白名单挡不住命令链**
  （F-16，八轮裁决"九轮落笔"，十七轮补账：kubectl token 判定挡不住
  `kubectl get pods; rm -rf /` / 反引号 / `$( )`）。只读命令结构性不需要
  `;|&$()<>"` 等元字符，出现即拒。param 值同理且更严：模板可信、param 值不可信
  （sweep/gate 双闸，命令含不安全 param 值即拒自动执行）。
- **T-4 凡出站到外部服务的文本，只能来自结构化字段+脱敏，不许透传自由文本**
  （F-17，八轮裁决"九轮落笔"，十七轮补账：webhook 卡片把告警 description 原样
  回显进钉钉/飞书，自由文本里的密码/连接串/token 被推出墙）。匹配用全文（本地），
  展示/外发只用结构化字段。
- **T-5 错误分类按异常类型做结构化判定，不猜错误文本**（十七轮 Fable 裁定 3：
  fail-fast 靠"错误文本含'连接'"判死目标——远端 stderr 会拼进错误消息
  （rc 分支），中文报错含"连接"二字把 rc 级失败误判成连接级，整轮静默跳过
  贴回丢证据）。弃文本匹配，`gate.err_kind(e)` 按异常类归
  connect/timeout/exec 三类；报告带 err_kind 字段，上层按它聚合。
  边界口径：network 连接器统一归 exec 是【有意保守】——不区分拨不通/执行
  失败，network 无 fail-fast、不静默丢证据。
- **T-6 双份事实必须同源产出、成对更新：改 Skill 探针必须同轮改齐
  【仓库存档】与【~/.opsaxiom/hub/registry】两份**（十六轮真机暴露：inode-exhausted
  上轮漏修 `df -i --output` 互斥 + registry 未同步，修了仓库真机照样坏；registry
  是独立 git 仓库，不会随仓库 commit 自动同步）。同型：白名单清单与远端
  /etc/sudoers.d 也是两份事实——客户端成员判定（gate._wl_member）不是对
  gen_sudoers 的"手写镜像"而是**消费同一 extract_entries 产物**：手写镜像必分叉
  （-u skip 表事故、startswith 宽松匹配与 fnmatch 不等价，均实证过）；
  "互证测试"必须测【对称性】（客户端 True ⟺ sudoers fnmatch 命中，双向断言），
  只测"登记形态自身能放行"拦不住镜像分叉。**近似远端语义的测试助手同样只许
  写 sudoers 手册可考的规则**（近似函数自身也是一张"镜像"——漏掉"裸名条目=
  任意参数放行"就与真语义相反，F-25）。测试内的一切名单（基名/子命令）必须
  从 gen_sudoers 派生，手抄必漂移（F-24）。同类：修探针还须重跑向导刷新远端
  sudoers（条目形态随 Skill 库变化）。

---
（本文件随每轮评审增补。新教训 → 新条目 → 下一轮生成 prompt。）
