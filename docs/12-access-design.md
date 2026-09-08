# 远程接入与凭证管理设计（Access & Credentials）

> 状态：设计稿（Fable 5，2026-07-18）。尚未实现——现状是协驾档只支持本机，
> 一切远程目标走导航档人工粘贴。本文回答发起人的问题：
> "让人自己登录机器拷输出回来，还是给我们权限、我们自动去拿？"
> 结论：**两条都保留，第二条（自动去拿）是本设计的主体**；
> 导航档永远是零凭证的兜底（气隙/合规禁区/临时救急）。

---

## 0. 一句话架构

```
targets.yaml(设备清单，只存凭证引用) 
   → connector(按设备类型拨号：ssh / 网络设备 / kubectl / http)
   → 凭证解析器(引用 → 凭证，优先复用你已有的：ssh-agent / ~/.ssh/config / kubeconfig)
   → 执行门(只读白名单 + 注入防护 + 逐目标授权 TTL)
   → 输出进事实库/卷宗，命令与结果全量留审计
```

**凭证永远只在操作者自己的机器上，OpsAxiom 不上传、不代管、不建中心凭证库。**

---

## 1. 为什么这么设计（三个根本决定）

### 决定一：凭证不出用户已有的信任边界

"给我们权限、我们自动去拿"最容易走歪的方向是：建一个中心服务替用户保管所有
设备密码。那会把 OpsAxiom 自己变成全网最值钱的攻击目标，也会让用户第一天就
面临"要不要把家底交给一个开源工具"的信任决断——大多数人会合理地拒绝。

所以本设计的第一原则：**OpsAxiom 只是"用你已经有的钥匙，替你跑腿"**。
你的 SSH 私钥本来就在 ~/.ssh，kubeconfig 本来就在 ~/.kube——OpsAxiom 直接
复用它们，不复制、不搬家、不上云。这和 gitpush.py 用本机密钥"推完即弃"是
同一条红线的延伸。

### 决定二：诊断只要只读，只读只要低权账号

205 个 Skill 里绝大多数是 Diagnostic——只需要**只读**。这给了安全设计一个
巨大的红利：给 OpsAxiom 用的账号可以是**没有破坏能力的低权账号**：

- Linux：普通用户 + 精确到命令的 sudo 白名单（见 §5.1 的 opsaxiom-ro 方案）
- 网络设备：厂商自带的只读级别（Cisco privilege 1 / 华为 monitor level / RBAC 只读角色）
- k8s：view ClusterRole（或更窄的自定义只读 Role）
- 中间件 HTTP 接口：只读端点本就无需凭证或用只读 token

**即使凭证泄露，攻击者拿到的也只是"能看"**。这是比任何加密方案都硬的兜底。
写操作（action 节点）不走这套自动通道——保持现有设计：变更简报 + 人工确认 +
已验证回滚，用人的高权限会话执行。

### 决定三：授权是"人给的、按目标、会过期"

现有 trust.yaml 的"本机首次授权"模式直接推广到远程：每个目标首次自动执行前，
明确问一次"允许 OpsAxiom 在 web-01 上自动执行只读命令吗？"，授权带 TTL
（默认 30 天，可配），到期重新确认。用户随时 `opsaxiom target revoke <目标>` 收回。
没有"一键授权全部"——批量授权必须逐台列出让人看见。

---

## 2. 设备清单（targets.yaml）：只存引用，不存凭证

`~/.opsaxiom/targets.yaml`——用户可手编，也可用向导生成：

```yaml
targets:
  web-01:
    connector: ssh
    host: 10.0.1.11
    user: opsaxiom-ro
    auth: agent                  # 凭证引用：用 ssh-agent 里的密钥
  web-02:
    connector: ssh
    host: 10.0.1.12
    auth: ssh_config             # 全权委托 ~/.ssh/config（ProxyJump/IdentityFile 都生效）
  core-sw-1:
    connector: network           # 网络设备（ssh 行协议）
    platform: cisco_ios
    host: 10.255.0.1
    user: netops-ro
    auth: keyring:core-sw-1      # 凭证引用：本机系统钥匙串里的条目
  prod-cluster:
    connector: kubectl
    auth: kubeconfig             # 直接复用 ~/.kube/config 当前上下文
    context: prod                # 可选：锁定 context，防误连
  es-log:
    connector: http
    base: https://10.0.2.5:9200
    auth: keyring:es-log-token   # 只读 token
```

要点：

- **`auth` 字段永远是引用**（agent / ssh_config / kubeconfig / keyring:名字 /
  file:路径），文件里出现明文密码 → 校验直接报错拒绝加载。
- 这个文件本身没有秘密，可以进团队 git 仓库共享设备清单——每个人的凭证
  各自在各自机器上解析。**清单可共享、钥匙不共享**，这就是团队协作的形态。
- `opsaxiom target import-ssh-config` 一键从 ~/.ssh/config 生成初稿——
  大多数运维的设备清单其实早就写在那里了。

## 3. 凭证解析器：分级，从"零新概念"到"团队级"

按用户规模分三级，**低级永远可用，高级是可选升级**：

| 级 | 适用 | 机制 | 用户要做什么 |
|---|---|---|---|
| **P0 复用现成** | 个人/绝大多数 | ssh-agent、~/.ssh/config、~/.kube/config、环境变量 | 什么都不用做——你能 ssh 上去，OpsAxiom 就能 |
| **P1 本机钥匙串** | 有密码类凭证的（网络设备/API token） | OS keyring（Linux SecretService/macOS Keychain），无桌面环境退化为 age 加密文件 + 主口令 | `opsaxiom cred set core-sw-1`（交互输入，不进 shell 历史） |
| **P2 团队短时凭证** | 团队/企业 | ① SSH CA 短期证书（推荐终态）② 堡垒机代跳 ③ Vault 类外部凭证库 | 接入既有企业设施，OpsAxiom 只做消费端 |

### 3.5 从零开始：一台新机器的首次开通（enroll）

发起人问题："第一次需要我做什么？是我本机生成 Key 配到远端吗？OpsAxiom 能帮忙吗？"
——是这个模型（本机生成密钥 → 公钥配到远端），而且 OpsAxiom 把它包成一条命令：

```
$ opsaxiom target enroll web-01 --host 10.0.1.11
① 检查本机密钥：~/.ssh/id_ed25519 不存在 → 生成一把（ed25519，可设口令）
   （已有密钥/ssh-agent 里有 → 跳过，用现成的）
② 首次上门需要临时用一次密码：
   请输入 root@10.0.1.11 的密码（只用这一次，不保存）: ****
③ 远端开通（等价于 ssh-copy-id + 低权账号剧本）：
   - 创建 opsaxiom-ro 用户 + 写入 sudoers 只读白名单（从 Skill 库命令集生成）
   - 把你的公钥写进 opsaxiom-ro 的 authorized_keys
④ 验证：改用密钥以 opsaxiom-ro 登录 → 跑 3 条只读探针 → 🟢
✔ 已写入 targets.yaml（auth: agent）。今后免密自动采集，密码已丢弃。
```

要点：

- **密码只在内存里用一次**（paramiko 交互认证），用完即弃，不进任何文件——
  这是"推完即弃"红线的又一次复用。
- 不想给 root？`--no-create-ro` 退化成纯 ssh-copy-id（用你现有账号），
  低权账号以后再补；`target doctor` 会持续黄牌提醒"这台用的是高权账号"。
- 已经有整套密钥/跳板体系的人**根本不需要 enroll**——`import-ssh-config`
  即用（P0 的承诺：你能 ssh 通，OpsAxiom 就能）。
- 批量开通：`enroll --from hosts.txt`，逐台问密码或用同一密码（内存一次性）。
- 网络设备/HTTP token 不走 enroll（多数不支持密钥），走 P1：
  `opsaxiom cred set core-sw-1` 存进本机钥匙串。

P2 三条路线说明（都只做**适配**，不自建）：

- **SSH CA 短期证书**（金标准）：团队维护一个签发端，运维每天/每次拿到
  几小时时效的证书（含 principal，落审计）。没有私钥分发、离职自动失效、
  每条审计能对到人。OpsAxiom 侧只需支持 `auth: agent`——证书本来就在 agent 里。
  **我们后续可以出一个 opsaxiom-ca 参考实现，但它是独立组件，不与主体绑死。**
- **堡垒机**：国内企业现实。`auth: ssh_config` + ProxyJump 天然支持；
  堡垒机要求的审计/录像不受影响——OpsAxiom 的连接就是一次普通 ssh。
- **Vault/凭证库**：`auth: vault:secret/ops/core-sw-1` 形式的引用，
  首版不做，接口留出（凭证解析器是插件点）。

## 4. 拉通各类设备：连接器矩阵

复用 Skill schema 已有的 connector 声明，执行层补齐四个连接器：

| connector | 覆盖 | 实现 | 只读闸门 |
|---|---|---|---|
| `ssh` | Linux/Unix 主机 | paramiko（gitpush 已引入，无新依赖） | `_is_readonly` 白名单（已有）+ 无 tty、禁转发 |
| `network` | 交换机/路由/防火墙 | paramiko 行协议 + 平台提示符适配（首版 cisco_ios/huawei_vrp，与语法库同源） | **语法库前缀白名单**（S6 已有的那套，运行时再查一遍）；只发 show/display |
| `kubectl` | k8s 集群 | 本机 kubectl + kubeconfig | kubectl 只读动词白名单 + 注入防护（F-16 已有） |
| `http` | ES/Prometheus/RabbitMQ 管理接口等 | urllib（零依赖） | 仅 GET + 每 Skill 声明的路径前缀白名单 |

关键点：**四个连接器共用同一个执行门**——白名单校验、param 注入防护（T-3）、
授权检查、审计落盘，都在门里做一次，连接器只负责"拨通"。新增设备类型 =
新增一个连接器插件 + 对应语法库，安全逻辑零重复。

采集优先级（自动模式下逐目标）：能自动 → 自动跑并入事实库；拨不通/没授权/
没凭证 → **该目标自动降级为导航档粘贴块**，混合出现在同一份取证计划里。
用户体验是"能自动的都自动了，剩下三台贴一下"——而不是全有或全无。

## 4.5 现实网络拓扑：VPN / 跳板 / 网页控制台（发起人场景走查）

真实环境里目标很少裸露可达——前面常挡着 VPN、跳板机、甚至只有网页入口。
三种到达方式分别处理：

| 到达方式 | 处理 | 机制 |
|---|---|---|
| VPN 后直连 | 用户自己连 VPN（照常），OpsAxiom 负责"提醒 + 探测 + 续跑" | `reach:` 标签（见下） |
| SSH 跳板/堡垒机 | `auth: ssh_config` 全权委托 ~/.ssh/config，ProxyJump 多级跳原样生效；主流堡垒机（JumpServer 等）的 SSH 通道与审计录像不受影响 | 现有 P0 路线，零新机制 |
| 纯网页控制台（无 SSH 通道） | **不硬做**——该目标自动降级为导航档粘贴块，用户在网页终端里贴 | §4 混合计划 |

**`reach:` 网络前置标签**（targets.yaml 可选字段）：

```yaml
prod-cluster:
  connector: kubectl
  auth: kubeconfig
  context: prod
  reach: vpn:office-vpn        # 标签 + 可选探测，不存任何 VPN 凭证
  reach_check: ping -c1 -W2 10.0.0.1   # 可选：自定义可达探针
```

它兑现三件事：

1. **拨不通时说人话**：不甩 timeout，提示"此目标标了 office-vpn——先连 VPN，
   连上按回车重试，或 skip 转粘贴模式"；用户连上后**断点续跑**，不重开排查。
2. **分组诊断**：`target doctor` 发现同一 reach 标签下的目标集体不通时，
   直接判"网络前置未就绪（VPN 没连）"，而不是逐台报错。
3. **红线不破**：VPN 代拨明确不做（客户端形态千差万别，代拨=替用户存 VPN
   凭证，违反 §1 决定一）。OpsAxiom 只探测与提醒，拨号永远是人的动作。

## 5. 安全设计汇总（每条都有已存在的先例）

1. **凭证不出本机**：解析后的凭证只在进程内存在，不写日志、不进事实库、
   不进卷宗、送模型前 redact（redact.py 已有，扩充凭证 pattern）。
2. **最小权限账号**（§1 决定二）：文档提供三平台的低权账号开通剧本；
   `opsaxiom target doctor` 会**主动检测**当前账号权限是否超额并黄牌提醒
   （"web-01 用的是 root——建议换 opsaxiom-ro，两分钟剧本见 docs"）。
3. **双白名单**：命令生成端白名单（Skill 树里只有验证过的命令）+ 执行端
   白名单（`_is_readonly`/语法前缀/GET-only 再查一遍）。模型永远不产命令
   的红线不变，因此不存在"模型骗执行器"的攻击面。
4. **逐目标授权 + TTL**（§1 决定三）：trust.yaml 扩展为 per-target 记录
   `{target, granted_at, ttl, scope: readonly}`。
5. **全量审计**：每条远程命令 + 原始输出 + 目标 + 时刻，随 incident 卷宗
   归档（facts.py 已做了一半，补 target 维度即可）。
6. **Linux 侧 opsaxiom-ro 剧本**（发布为脚本 + 文档）：
   ```
   useradd -m -s /bin/bash opsaxiom-ro
   # sudo 白名单精确到命令（示例节选，与 Skill 库实际用到的命令集同步生成）
   echo 'opsaxiom-ro ALL=(root) NOPASSWD: /usr/bin/dmesg, /usr/sbin/smartctl -H *, ...' \
        > /etc/sudoers.d/opsaxiom-ro
   ```
   这个 sudoers 白名单可以**从 205 个 Skill 的命令集自动生成**——库里用什么
   就放行什么，一条不多。Skill 库更新时白名单剧本同步再生成。这是"技能库
   即权限清单"，是本产品独有的红利。

   > **⚠ 现状标注（2026-09，真机验证结论）**：sudoers 白名单位于远端（用户账号
   > 侧），但当前 runtime 执行链（gate → ssh_conn）从不发出 `sudo <命令>`——
   > connector 以登录用户原样执行 skill 命令。因此白名单授权此刻**没有消费者**，
   > 向导（target add）v1 已摘除写入步骤；低权账号 opsaxiom-ro 只建账号+公钥，
   > 需 root 的探针会失败（属预期受限）。生成器（tools/authoring/gen_sudoers.py）
   > 保留作为接线后的地基。接线方案（gate 识别 permission-denied → 自动
   > `sudo -n` 回退重试一次 → visudo -c 校验后才落盘）见 B 轮计划
   > `.claude/plans/remote-sudo-wiring.md`。教训已入库：**裸名二进制导致 sudoers
   > 语法错误（须绝对路径）；引号内 `|` 会泄漏进切段结果；写入前必须 visudo -c，
   > 坏白名单比没有白名单糟**。

## 6. 易用性设计（把"配置"变成"确认"）

- **首次向导**：`opsaxiom target add web-01` → 交互问 connector/host/怎么登录，
  能 ssh 通就自动试探（agent 里有几把钥匙、config 里有没有条目），用户多数时候
  只需要按回车确认。
- **批量导入**：`import-ssh-config`（§2）；k8s 直接读 kubeconfig 的 context 列表让选。
- **体检**：`opsaxiom target doctor` 逐目标测连通/测凭证/测权限级别，
  🟢🟡🔴 一屏看完。排查中途拨不通不再是玄学——先 doctor。
- **incident 里零感知**：用户描述故障 → 假设涉及 web-01/core-sw-1 →
  已授权的自动取证、未授权的当场问一次（授权后立即执行）、给不了权限的
  出粘贴块。用户不需要理解"连接器/凭证解析器"这些词。

## 7. 边界与不做清单（诚实边界）

- **不做中心凭证托管服务**（§1 决定一）。团队要集中管理 → 走 P2 适配既有设施。
- **写操作不进自动通道**。action 永远是：变更简报 → 人确认 → 高权会话执行 →
  验证 + 回滚待命。自动通道的账号根本没有写权限，这是结构性保证。
- **Windows 主机、SNMP、IPMI/BMC 首版不做**，连接器插件点留好。
- **密码明文兜底不提供**：连 `auth: password:xxx` 这种字段都不存在，
  逼着用户至少走 keyring——易用性向安全让步的唯一一处。

## 8. 落地路线（供排期参考）

| 阶段 | 内容 | 依赖 |
|---|---|---|
| A-1 | targets.yaml + 凭证解析 P0（agent/ssh_config/kubeconfig）+ ssh 连接器接入执行门 | paramiko 已有 |
| A-2 | trust.yaml 扩 per-target TTL + 审计补 target 维度 + target doctor | A-1 |
| A-3 | network 连接器（cisco/huawei，语法库白名单复用）+ http 连接器 | A-1 |
| A-4 | P1 keyring/age + `opsaxiom cred set` | A-1 |
| A-5 | opsaxiom-ro sudoers 自动生成器（从 Skill 库命令集）+ 三平台低权账号剧本 | 可并行 |
| A-6 | 混合取证计划（自动+粘贴块同屏）接入 incident 流 | A-1..A-3 |
| B 期 | SSH CA 参考实现 / Vault 适配 / 更多连接器 | 按需求 |
