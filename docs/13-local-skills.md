# 本地化 Skill 设计：通用库 + 个人层（Overlay & Fork）

> 状态：设计稿（Fable 5，2026-07-19）。回答发起人的问题：
> "有些 Skill 是个人特有的——整体流程和通用款一样，但关键 placeholder 要填
> 自己的内容（比如排查用的内部网页），这套配置只能本地维护，不能推到远端。
> 这种派生关系怎么处理？"

---

## 0. 问题的本质与结论

社区 Skill 必须通用才能被验证、被共享；但真实排查永远带着"我家"的东西：
内部 Grafana 大盘、CMDB 链接、值班群、特殊阈值、内网镜像地址。这两者的
张力不能靠"复制一份自己改"解决——那会立刻失去上游更新，还会诱使人把内网
信息 push 出去。

**结论：三层模型。通用层只读可升级，个人层贴在上面、永不出门。**

```
层 0  通用 Skill（hub 拉取，只读，可升级，徽章有效）
层 1  Overlay 叠加层（本地：填参数、贴链接、预设答案——不改树，徽章仍有效）
层 2  Fork 派生（本地新 id：真的要改流程时才用——徽章清零，本地重新验证）
```

外加一个比 Overlay 更轻的第 0.5 层：**linkbook（个人网页台账）**——
发起人说的"有一个地方管理排查用的网页"，八成用它就够了。

---

## 1. linkbook：个人排查网页台账（最简单的 80% 场景）

`~/.opsaxiom/linkbook.yaml`——按 taxonomy 前缀挂内部网页，**不涉及任何 Skill 文件**：

```yaml
links:
  middleware/mysql:                    # 命中该前缀的所有 Skill / incident
    - name: MySQL 慢查询大盘
      url: https://grafana.corp/d/mysql-slow
    - name: 数据库值班表
      url: https://wiki.corp/db-oncall
  k8s:
    - name: 集群总览
      url: https://grafana.corp/d/k8s-overview
  "*":                                 # 全局：任何排查都显示
    - name: 事件通报群指引
      url: https://wiki.corp/incident-howto
```

体验：排查 mysql 慢查询时，卷宗/交互界面自动带出——

```
📌 你的相关页面：MySQL 慢查询大盘 · 数据库值班表
```

规则：按 taxonomy 最长前缀匹配聚合；纯展示层，不进决策树、不进命令。
这一层解决"我要在排查时随手打开我家大盘"，成本几乎为零。

## 2. Overlay 叠加层：填 placeholder、贴节点注记

当个性化和**具体 Skill 的具体节点**绑定时，用 overlay。
`~/.opsaxiom/overlays/<skill-id>.yaml`：

```yaml
overlay: skill-overlay/v0.1
base: middleware.es.disk-watermark          # 叠加在哪个通用 Skill 上
base_version: ">=0.1"                       # 兼容声明，base 大版本跳变时告警

params:                                     # ① 填 Skill 声明的本地参数
  es_endpoint: https://es-log.corp:9200     #    （见 §2.1 params source: local）

notes:                                      # ② 给节点贴个人注记（按 node id）
  check_disk:
    links:
      - name: ES 磁盘水位大盘
        url: https://grafana.corp/d/es-disk
    caution: 我们家 log-es-3 盘最小，历来先爆它
  done_disk_full:
    caution: 扩容要先走 CMDB 变更单 → https://cmdb.corp/change/new

answers:                                    # ③ ask 节点预设答案（跳过重复选择）
  ask_cleanup_strategy: delete_old_indices  #    每次都选这个，直接预填
```

### 2.1 Overlay 能改什么、不能改什么（红线）

| 能 | 不能 |
|---|---|
| 填 `params`（Skill 声明 `source: local` 的参数） | 改 `run` 命令模板本身 |
| 给任意节点贴 `links` / `caution` 注记 | 改 `branch` / `when` / `otherwise`（判读逻辑） |
| 预设 `ask` 节点答案 | 增删节点、改树结构 |
| — | 改 metadata / 徽章 |

理由：🔵 sim_verified 徽章验证的是**那棵树**。overlay 不碰树，所以叠加后
徽章依然诚实有效；想改树 → 那是 fork（§3），徽章清零重来。
加载器**强制**这条红线：overlay 里出现 run/branch/when 字段直接拒绝加载。

params 是唯一能影响命令的通道，但只是**值替换**（`{{es_endpoint}}` 这类
Skill 作者预留的占位符），且沿用 T-3 纪律：渲染前过语法树 + shell 元字符
即拒。schema 侧只需给 `metadata.params.source` 加一个枚举值 `local`
（现有 alert|user|derived），表示"值由本地 overlay/配置提供"。

### 2.2 展示与合并

- 加载时合并：REPL/卷宗里通用内容照常，本地注入的内容一律带 📌 前缀
  （"📌 我们家 log-es-3 盘最小…"），一眼可分辨哪些是社区共识、哪些是自己的话。
- base 升级后 overlay 引用的 node id 不存在了 → `opsaxiom skill doctor`
  黄牌："overlay 引用了已消失的节点 check_disk，请核对新版本"。
  匹配不上的注记跳过不加载，绝不阻塞排查。

## 3. Fork 派生：真的要改流程时

流程本身就和通用款不一样（多一步内部审批、少一个不适用的分支、命令要走
内部代理），overlay 装不下 → 正经 fork：

```yaml
metadata:
  id: local.mysql.slow-query-storm-corp     # 新 id，local. 前缀
  derived_from: middleware.mysql.slow-query-storm@0.1.0   # 派生血缘
  visibility: local                          # 出门红线（见 §4）
  maturity: draft                            # 徽章清零——改过的树没验证过
```

- `opsaxiom skill fork <base-id>` 一键生成：拷树、写 derived_from、
  置 draft、放进 `~/.opsaxiom/skills-local/`（不在仓库 skills/ 里）。
- 本地照常走完整流水线：validate → 写场景 → 本地 promote 到 sim_verified
  ——**本地徽章只在本地有效**，工具链完全复用。
- 上游更新时：`skill doctor` 对比 derived_from 版本，提示"base 已出 0.2，
  你的 fork 落后了"，给出 base 的 diff 供人工合并（不自动合并——改过的
  树只有作者知道哪里是故意的）。
- 如果某天觉得自己的改法有普适价值：去掉内网内容、改回通用 id、正常走
  hub 投稿——fork 是私有起点，不是死路。

## 4. 隐私边界：个人层结构性出不了门

"个性化配置不能推到远端"不能靠自觉，靠结构：

1. **目录隔离**：overlays/、linkbook.yaml、skills-local/ 全在 `~/.opsaxiom/`
   下，不在仓库里——`hub push` 打包器根本扫不到它们。
2. **打包器双保险**：即便有人把 fork 拷进仓库，打包器遇到
   `visibility: local` 或 id 带 `local.` 前缀直接拒绝打包，CI 同规则再拦一道。
3. **导出剥离**：卷宗/故障报告导出默认含 📌 本地注记（给自己看方便）；
   `report` 命令会先问是否脱敏（答 y）——剥离全部 📌 内容与 linkbook 链接——
   分享出去的报告不带内网 URL。redact.py 同时兜底。

## 5. 和已有机制的关系（零新概念复用）

| 已有机制 | 在本设计中的角色 |
|---|---|
| `metadata.params`（docs/03） | 加 `source: local` 枚举，overlay 填值 |
| T-3 param 注入防护 | local 参数渲染进命令时原样生效 |
| validate / promote / run_sim | fork 在本地完整复用，本地徽章本地管 |
| targets.yaml"清单可共享、钥匙不共享" | overlay 同理：团队可以共享一份 overlay 仓库（团队内网信息在团队内共享是合理的），但它和公共 hub 是两个世界 |
| redact.py | --share 导出剥离的兜底 |

## 6. 落地路线

| 阶段 | 内容 | 说明 |
|---|---|---|
| L-1 | linkbook + 卷宗/REPL 📌 展示 | 最小可用，独立于其他一切 |
| L-2 | overlay 加载器（红线校验 + 合并 + 📌）+ params source: local | 核心 |
| L-3 | `skill fork` + skills-local/ 本地流水线 + 打包器/CI 拒收 | 派生 |
| L-4 | `skill doctor`（overlay 失配 / fork 落后上游检测）+ --share 剥离导出 | 治理 |
