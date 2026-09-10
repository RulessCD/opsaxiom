# OpsAxiom 用户手册（面向运维人员）

> 这份手册给**用它的运维人员**，不是给开发者。四章：装 → 用 → 沉淀 → 交换。
> 一句话理解 OpsAxiom：**它不替你乱按，它像个懂行的老师傅站你旁边，一步步告诉你查什么、
> 注意什么，命令你自己敲；等你信得过某个流程了，再让它多担一点。**

---

## 一页起步卡（可打印）

```
装      ./install.sh            # 完事自动体检；docker run opsaxiom 也行
用      opsaxiom                # ← 就这一个词！进入交互态，敲字说问题即可
          axiom> 磁盘满了但df有空间     # 直接说问题，列出候选
          axiom> 1                      # 输序号，进入逐步排查
          axiom> help / list / info <id> / resume / doctor / quit
沉淀    排查完顺手答一句认证；没走skill就 record；老经验用 skill new
拿/发   axiom> hub search 磁盘   → hub pull <id>   ；发布 hub push <id>
体检    opsaxiom doctor         # 红=必修 黄=可用但受限
```
（子命令 `opsaxiom diagnose/run/...` 仍在，供脚本与自动化用；日常人用一个 `opsaxiom` 就够。）

---

| 形态 | 命令 | 适合 |
|---|---|---|
| 脚本 | `./install.sh`（内网/有源） | 大多数机器 |
| 容器 | `docker run -it opsaxiom` | 标准化环境 |
| 离线 | 解开 `opsaxiom-offline.tar.gz` → `./install.sh --offline` | **气隙内网**（仅 Linux x86_64，Python ≥3.9） |

**离线包（气隙环境）**：

1. 有网机器上打一次包：`./pack-offline.sh`（可选 `--with-model` 捎带内置小模型，+469MB）
   → 产出 `pack-output/opsaxiom-offline-<版本>.tar.gz`（仓库快照 + wheels + Skill 库快照）
2. 摆渡到气隙机器（U 盘/内网文件系统），然后：
   ```bash
   tar xzf opsaxiom-offline-<版本>.tar.gz
   cd stage/opsaxiom
   ./install.sh --offline     # 依赖走包内 wheels，Skill 库用包内快照，全程不出网
   ```
3. 前置自查（目标机）：`python3 --version` 必须 ≥3.9——离线 wheels 按 3.9+ 收集
   （上游编译型依赖已停发 3.8 wheel），不满足时 install.sh 会红停说明。

装完会自动跑一次 **`opsaxiom doctor`**：

- 🟢 全绿 → 直接用。
- 🟡 黄 → 能用，只是某些域的"真实执行"或签名功能受限（例如没装 kubectl，k8s 域的
  `--real` 自动执行用不了，但**导航档指导照常**）。
- 🔴 红 → 必须先修（缺 python 依赖 / 目录没权限），doctor 会写清怎么修。

日后任何"怎么跑不起来"，第一件事都是 `opsaxiom doctor`。

---

## 第二章 · 用（一次完整排查长什么样）

### 唯一需要记的一件事：敲 `opsaxiom`，然后说人话

```
$ opsaxiom
OpsAxiom v0.1 · 73 个 Skill（49 已验证）· 输入你遇到的问题，或 help 看用法
axiom>
```

这个 `axiom>` 提示符就是你和它打交道的地方。**你不用记任何命令**——直接把问题
说出来。它像急诊医生，不像客服问卷：**听你说一遍 → 开一组化验 → 拿化验单给诊断**，
而不是一题一答问你二十遍。

**1) 说问题，它并行考虑几个可能、一次性把该查的只读命令都取证了：**

```
axiom> 磁盘满了但 df 显示还有空间 mount=/data
  假设 3 个（按相关度）：
  1) [🔵已验证] 磁盘空间耗尽排查与处置       host.storage.capacity.disk-full
  2) [🔵已验证] inode 耗尽…
  3) …
  本机可自动执行 5 条只读取证命令（均出自已验证 Skill，绝不含写操作）。
  授权本机自动取证？一次性，记 trust.yaml [y/N]: y
  ▶ 取证中（本机只读自动执行）… 完成
```

- 结尾的 `mount=/data` 是把"哪台/哪个挂载点"这类实体直接告诉它（配了本地模型的话，
  它能自己从你的话里认出来，会显示"（从你的描述预填：mount=/data——回车确认）"）。
- **只读命令自动跑**（本机、需你一次性授权）；远端设备则给你**一整块命令**，
  你在设备上跑一次、把输出整块贴回（不再一条条来回）。

**2) 它给你一份诊断卷宗——证实了什么、排除了什么、还差什么，每条都带证据：**

```
  ── 诊断卷宗 ──────────────────────────────
  ✔ 已证实  磁盘空间耗尽排查与处置 [🔵已验证]
       证据: df -i --output=ipcent /data → rows[0].ipcent = 99
       → 待处置: 哪些文件确认不再需要？（移入隔离区，随时可恢复）
  ✘ 已排除  已删除未释放
       证据: lsof +L1 /data → 无残留句柄
  ──────────────────────────────────────────
  → 处置：run host.storage.capacity.disk-full（进入导航档执行变更）
```

徽章表示验证程度：⚪草稿 · 🔵已验证(仿真) · 🟢实地 · 🟡认证。
判读全是机器按解析器字段算的，不是模型"看一眼觉得"——**证据不足它就明说还差哪条命令，
绝不猜着往下走**。若一个都没证实，输 `report` 导出移交卷宗，转人工/强模型接手。

> 想一步步自己走老式逐节点排查？输候选序号（如 `1`）仍会进入逐步导航档——保留作兜底。

**3) 遇到要改动的步骤（变更），它先给你一份"变更简报"再等你拍板：**

```
━━ [变更] 压缩 7 天前的历史日志 ━━  风险: low
📋 变更简报
  影响面: 只动 /var/log 下 7 天前的 .log
  执行中盯: df 使用率应下降
  什么情况立即中止: 磁盘不降反升
  ── 将要执行的命令（请你亲自执行，Agent 不代执行）──
    $ find /var/log -name '*.log' -mtime +7 -exec gzip {} \;
  ── 回滚方案 ──
    $ gunzip /var/log/*.log.gz
需要审批。你的决定？
  1) 确认，我亲自执行  2) 跳过此步  3) 升级人工  4) 退出会话
```

写操作**永远由你亲手执行**，它不代按。选 4 退出后，在 `axiom>` 提示符输 `resume`
就能从中断处接着来。

**4) 查完，顺手把这次经历沉淀下来（见第三章）。**

### 另一个入口：告警自动触发（可选）
接了 IM（钉钉/飞书）后，告警来了会自动 diagnose，把"候选 Skill + 第一步"推到群里，
你点开就回到上面的流程。

### 信任是逐个 Skill 攒出来的
默认所有 Skill 都走导航档。等你亲手验证过某个 Skill 几次、信得过了，可以对**那一个**
开更省事的档位（只读命令自动跑，写操作仍要你确认）。信任不是一个全局开关，是一格一格加的。

### 可选：接一个模型（让它更"懂"你的话）
**不接也能用**——上面全部功能在没有模型时照样跑（关键词匹配 + 机器判读）。
接了模型后，它只多做三件事：从你的话里认实体（自动预填 `mount=…`）、把判读讲得更顺口、
排查无果时从**库内**推荐下一个 Skill。**模型永远不出命令、不做判读**——那是决策树和
解析器的活。送模型的内容一律先脱敏，凭据永不外泄。

首次裸敲 `opsaxiom` 会有一次性向导；之后随时用命令行切换：

```
opsaxiom model show     # 看当前配置 + 四个后端各自差什么（诚实红黄绿）
opsaxiom model pull --with-deps   # 下载内置小模型（千问 0.5B，≈469MB）+ 装推理依赖
opsaxiom model use builtin        # 用内置小模型（本机离线跑，气隙可用的备用）
opsaxiom model use ollama --model qwen2.5:7b          # 用本地 Ollama
opsaxiom model use remote --endpoint http://x/v1 --model m --api-key k   # 远程 API
opsaxiom model use pi --provider anthropic --model claude-sonnet-4-6     # Pi 网关
opsaxiom model test     # 发一条真实探针验证（"主机 web-01 的 /data 磁盘满了"→抽实体）
opsaxiom model use off  # 关掉，回到纯确定性模式
```

四个后端一句话：**builtin** = 内置千问 0.5B（GGUF 本机推理，离线备用底座）；
**ollama** = 本地 Ollama 服务；**remote** = 任何 OpenAI 兼容 API；
**pi** = Pi Agent Harness 的多 provider 网关（一个配置就能换 OpenAI/Anthropic/Google/
DeepSeek 等，需 node≥22.19 + `npm install @earendil-works/pi-ai`）。
任何后端不可用时自动降级为无模型模式，绝不阻塞排查。

---

## 第三章 · 沉淀（把你的经验变成 Skill）

三条路，按省力程度排：

**A. 排查完顺手认证（最省力）** —— 你刚在 `axiom>` 里查完一个真问题，结尾它会问：
> 要把这次验证沉淀为社区凭据吗？[y/N]

答 y，再补两个不敏感的信息（什么系统、大概多少台机器），30 秒生成一份**带签名**的验证记录。
这份记录是这个 Skill "在真实环境被验证过"的证据——攒够了它的徽章就升级。
（补交：`opsaxiom attest --from-session <会话号>`。）

**B. 排查没用到现成 Skill？边查边录** ——
```
opsaxiom record start 我的排查
opsaxiom record exec "df -h /"      # 代你跑只读命令并留痕（写操作会被拒绝）
opsaxiom record stop
opsaxiom skill from-session rec-我的排查 --id host.xxx.yyy --name "..." --taxonomy host/xxx/yyy
```
它把你这趟排查回放成一个 Skill 草稿：命令、输出样例都在，你只要补上"每步在判断什么、
每种情况怎么办、有什么坑"。

**C. 脑子里的老经验，问答倒出来** —— `opsaxiom skill new`，它一句句问，你一句句答，问完出草稿。

三条路产出的都是**草稿**。补完后跑 `opsaxiom skill lint <草稿>`，它列出还差什么
（哪个判据没填、哪条 caution 是占位、缺不缺回滚）。补齐 → 写个测试场景 → 验证通过就自动升级。
**降低的是写的麻烦，不降质量门槛**——这是别人敢用你 Skill 的底线。

---

## 第四章 · 交换（和社区互通有无）

Skills Hub 就是一个 git 仓库（内网可镜像、气隙可用 bundle 摆渡）。

**拿别人的：**
```
opsaxiom hub init <registry地址>          # 一次性配置
opsaxiom hub search 暴力破解
opsaxiom hub pull sec.access.bruteforce   # 拉下来
```
拉取时会自动过三道关，你会看到结果：
- **门①校验**：在你本地重新校验一遍（不信对方一面之词）；
- **门②验签**：这份 Skill 的实地验证记录签名对不对、签名者你信不信任
  （不在你的信任名单里会标 `TOFU`——能用，但用于自动化前自己掂量）；
- **门③徽章**：草稿默认不给拉（`--allow-draft` 才放开）。

拉来的放在 `skills-community/`，和你自己的 `skills/` 分开，互不污染。

**发自己的：**
```
opsaxiom hub push <你的skill-id>          # 打成 bundle
```
把 bundle 摆渡到 registry 侧导入，或走 git PR。你的 Skill 连同签名的验证记录一起走。

**浏览（网页版）：** 对 registry 跑一遍站点生成器就得到一个内部 Hub 网站——
按域浏览、搜索、点开看决策树和谁在什么环境验证过。私有环境完全离线可用。

---

## 第五章 · 接入你的设备（让它替你去远端取数）

排查到某台远端机器/交换机/集群时，与其你登录上去拷输出回来贴，不如让 OpsAxiom
替你去拿。**它用的是你自己机器上已有的钥匙，不另存密码、不上传任何凭证。**

### 5.1 第一次：把设备告诉它

```bash
opsaxiom target add web-01          # 交互向导：连法(ssh/network/kubectl/http)、主机、端口、VPN 标签
opsaxiom target import-ssh-config   # 或者：直接从你 ~/.ssh/config 批量导入（很多人早配好了）
opsaxiom target doctor              # 体检：每台能不能连、凭证齐不齐、是不是要先连 VPN
```

Linux 机器上，`target add` 会**一站式开通**：你输一次 root 密码（只用这一次、
不保存），它把你的公钥装到远端、建一个只读账号 `opsaxiom-ro`、并写入一份
**只读命令白名单**（从 Skill 库自动生成——库里用什么命令就只放行什么）。之后
它连这台机器永远用密钥，密码已丢。
**清单里没有任何明文密码**。有密码类凭证（网络设备、API token）：
`opsaxiom cred set core-sw-1`（值进系统钥匙串/加密文件，`cred list` 只显示名字）。

### 5.2 双档位：白名单档 → grant 升 root 档

Linux 设备默认进**白名单档**：白名单内的命令（df、ss、journalctl 这些取证命令）
它自动跑（远端 sudoers 物理把关），名单外的转成手工粘贴。想让全部只读命令自动跑：

```bash
opsaxiom target grant web-01        # 升 root 档：管理账号直登全量自动（默认 30 天到期）
opsaxiom target list                # 看每台档位和授权还剩几天
opsaxiom target revoke web-01       # 随时收回 → 退回白名单档（名单内照旧自动）
```

### 5.3 排查时它怎么干

名单内/已授权的目标它自动跑只读命令、结果进卷宗；名单外/没授权/连不上的，它退化
成"粘贴块"让你在目标上贴命令拷回来；自动执行失败的（比如全盘扫描超时）也会转成
手工粘贴——**能自动的都自动，剩下的贴一下**，不会全有或全无。要是整台机器都连
不上，它不会拿一堆命令逐条盘问你，而是一次性提示你先 `target doctor` 体检。
要先连 VPN 的目标，它会说"先连上 VPN 再回车"，连上后接着跑，不用重来。

## 第六章 · 个性化（贴你自己的页面和习惯，又不改坏通用 Skill）

社区 Skill 是通用的，但排查时你总想开"你家"的大盘、按"你家"的习惯来。三个层级：

| 你想要的 | 用什么 | 文件在哪 |
|---|---|---|
| 排查时随手开内部大盘/值班表 | **linkbook** 网页台账 | `~/.opsaxiom/linkbook.yaml` |
| 给某个 Skill 填内部地址、贴一句提醒 | **overlay** 叠加层 | `~/.opsaxiom/overlays/<skill-id>.yaml` |
| 真的改某个 Skill 的流程 | **fork** 派生 | `opsaxiom skill fork <id>` |

这些都只在**你本机**，永远不会被推送到社区——分享报告时 `report --share`
会自动把 📌 注记和内网地址剥掉。

### 6.1 linkbook：网页台账（最常用）

`~/.opsaxiom/linkbook.yaml` 里按类别挂内部链接，排查时自动带出：

```yaml
links:
  middleware/mysql:
    - name: MySQL 慢查询大盘
      url: https://grafana.corp/d/mysql-slow
```

排查 mysql 慢查询时，卷宗末尾会显示"📌 你的相关页面：MySQL 慢查询大盘"。

### 6.2 overlay：填 placeholder、贴注记

`~/.opsaxiom/overlays/middleware.es.disk-watermark.yaml`：

```yaml
base: middleware.es.disk-watermark
params: {es_endpoint: https://es-log.corp:9200}   # 填 Skill 留的内部地址占位符
notes:
  done_disk_full:
    caution: 扩容要先走 CMDB 变更单               # 到这一步时提醒你
```

overlay **只能填参数、贴注记、预设答案**，不能改命令和判断逻辑——改了徽章就不作数了，
所以加载器会拒绝。要改流程 → 用 fork。

### 6.3 体检与派生

```bash
opsaxiom skill doctor               # overlay 引用了失效节点 / fork 落后基线，一目了然
opsaxiom skill fork middleware.es.disk-watermark   # 真的要改流程时派生一个本地版
```

## 常见问题

- **它会不会自己乱改我系统？** 不会。默认导航档下写操作一律你亲手执行，它只给方案和回滚。
- **回滚是不是每步都有？** 是。可回滚是本项目第一准则——任何变更步骤都配回滚方案，没有就不该出这一步。
- **凭据/密码会被上传吗？** 不会。验证记录只含分桶信息（"rhel 8.x / 10-100 台"这种），
  绝不含主机名/IP/密码；记录本地签名，你决定推不推。
- **它能替我登录远端吗？会不会把我密码存起来？** 它复用你本机已有的钥匙（ssh-agent/ssh配置/
  kubeconfig/系统钥匙串），清单里不存任何密码；值只进钥匙串/加密文件与内存，不上传。
- **跑不起来？** `opsaxiom doctor`（系统）+ `opsaxiom target doctor`（设备）+ `opsaxiom skill doctor`（个性化）。

