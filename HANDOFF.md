# 模型交接协议（HANDOFF）

> **任何模型接手本仓库的第一件事：读完本文件。** 本文件永远反映当前状态与下一步。
> 每个模型在结束自己的工作阶段前，必须更新本文件的"当前状态"与"交接给谁"。

## 阅读顺序（新接手模型必读，按序）

1. `HANDOFF.md`（本文件）—— 当前状态与你的任务入口
2. `docs/00-golden-rules.md` —— 宪法，全部 12 条，不可违反
3. 与你任务相关的设计文档（见下方任务指引）
4. `skills/host/disk-full/skill.yaml` + `skills/network/bgp-neighbor-down/skill.yaml`
   —— 金标准模板，你产出的一切 Skill 以此为质量基准

## 分工契约

| 模型 | 职责 | 禁区 |
|---|---|---|
| **Fable 5**（架构师/终审） | 顶层设计、schema 演进、金标准、对抗评审、疑难领域 | 不做批量机械工作 |
| **Opus 4.8**（主力工程） | 按 `TODO-opus.md` 执行批量生成与工具开发 | **不得修改** `docs/00-03`、`schema/`；发现设计问题记入 `REVIEW-QUEUE.md` 而非自行修改 |
| 人类（项目发起人） | 方向决策、模型切换、最终审批 | — |

## 工作循环

```
Fable 设计/评审 → 更新 TODO-opus.md → 【人切换到 Opus 4.8】
  → Opus 执行 → 产出 + REVIEW-QUEUE.md → 更新本文件 → 【提示人切换回 Fable】
  → Fable 评审 REVIEW-QUEUE → 合并/打回 → 下一轮
```

规则：
- 每完成一个 TODO 条目即 git commit（小步提交，commit message 带条目编号，如 `[O-3]`）。
- Opus 完成全部条目或遇到阻塞时：更新本文件"当前状态"，然后**在回复末尾明确提醒用户：
  "请切换回 Fable 5 进行评审"**。
- 评审不通过的产出不删除，移入 `attic/` 并在 REVIEW-QUEUE 记录原因（留痕供改进）。
- **本文件里所有"已具备/已实现"表述，写入前必须实测过**（F-15/docs07 T-2）：
  虚报比缺功能严重，下一个接手者会基于假前提做设计。

---

## 当前状态（由最后工作的模型更新）

- **更新时间**：2026-09-09（十七轮真机回归通过 + F-28，两档全验证）
- **更新者**：Opus 4.8（工程实现）
- **阶段**：**真机两档回归通过（高等云肆 223.193.41.38:32147，Ubuntu 实机）**：
  ① v3 白名单重开通 31 条落盘/visudo 通过/写面零在场；② sudoers 全绝对路径、
  systemctl 仅 is-active/show/status 只读形态；③ 对抗探针（systemctl --failed
  restart / mount / sysctl -w / journalctl -u / numactl -H）ro 账号下全部
  rc=1 拒绝，正向 is-active/df rc=0；④ 白名单档批量取证正确分流（mount 贴回=
  F-26 生效）；⑤ grant 后 root 档全自动（audit tier=root/exec_as=root）。
  过程中真机暴露 **F-28**（执行门 _readonly_ok 复用 sim 手写 _ALLOW_LEAD，
  与 registry 名单差 15 命令 → 白名单档 iotop 等被误拒），已修（5704be6，
  gate._runtime_ro_leads = registry ∪ sim 派生，守 T-6），修复后 iotop 探针
  executed、贴回 2→1 条。814 passed / 5 skipped。
- **二轮返工交付（对应 REVIEW-QUEUE"十七轮二轮返工对账"，Fable 复核结论）**：
  - **F-19（P0）白名单 v3**：flag 前缀条目结构性禁止（flag 与命令词正交，
    `--failed *` 挡不住 `--failed restart`）。复合型只认只读子命令白名单
    （_RO_COMPOSITE_SUBCMDS），flag/裸名全部 fail-closed。贴回代价清单
    已向发起人报备并确认。
  - **F-23：numactl 等"策略+任意命令"执行器硬拒；sysctl/smartctl/chronyc/
    coredumpctl/nvidia-smi 裸名写面入黑名单——root shell 口子关闭。
  - **F-18：-u skip 表删除；_wl_member 消费 extract 产物（精确二段判定，
    startswith fallback 删除）；对称性测试双向断言。
  - **F-20 测试牙口**：test_b1 重写（基名+第二段白名单解析，bin_paths+预览
    双形态）+ test_b1_gate_has_teeth 缺陷重建必炸验证。
  - **F-21 repl (target, cmd) 二元组定位 + 测试三件套**（盘问次数/入库
    target/贴回调用清单），恒真断言清除。
  - **F-22 err_kind 死条目清除 + network 保守口径注记**（docstring + T-5）。
  - 测试：**813 passed / 5 skipped**（新增牙口锁定与对称性双向断言）。
- **下一步**：合并 rework-b1-err-kind → main 并 push（回归全对上，条件已满足）。
  待办（发起人）：#12 opsaxiom update 子命令、#13 气隙离线包。
- **十七轮一轮返工交付（已被二轮返工覆盖，存档）**：
  - **B-1（P0）sudoers 前缀闸**：enroll 渲染时 (bin,prefix) 被降成裸名 → 复合型
    二进制（systemctl 等）任意子命令 root 可达。修复四件套：gen_sudoers 复合型
    只发"已登记子命令/flag 前缀"条目（`bin p *` + `bin p` 双形态，裸名仅限单用途
    二进制，复合型裸探针零条目 fail-closed）；enroll/target_cli 传全 entries；
    gate._wl_member 与远端 sudoers 同源判定（互证测试）；写子命令物理不在场
    回归（test_b1_write_subcommand_physically_absent，扫真 registry 渲染断言）。
  - **裁定 3（P1）err_kind 结构化**：错误分类弃"文本含'连接'"改异常类判定
    （gate.err_kind → connect/timeout/exec）；gate/sweep 报告带 err_kind；repl
    fail-fast 按【目标×全部 connect】聚合判死，死目标一次性指引不贴回、活目标
    照常贴回（修复了评审发现的"死目标短路把活目标贴回一并跳过"实现偏差）。
    对抗测试：rc 级 err 含"连接"→ 不短路；双目标死/活分流（贴回证据入库断言）。
  - **发起人裁定（cat/grep 保留）**：白名单语义=写侧焊死/读侧全盘，sudo -n cat
    读 root 文件属接受范围——白名单价值在防写不在防读。
  - **docs/07 T 补账**：T-3（F-16 元字符，八轮欠账）/T-4（F-17 出站文本，八轮欠账）
    /新 T-5（错误分类结构化）/新 T-6（registry×仓库双份事实同步纪律）。
  - **搭车债**：paramiko 入 requirements（带用途注释）+ doctor 可选检查；
    docs/12:128 enroll 输出示例改诚实表述；model_cli check_local_ready 注入
    system 参数（2 条长期环境失败转绿）；P2 _DENY 黑名单补刀 Fable 认可延后。
  - 测试：**813 passed / 4 skipped**（本轮 +8：gate 互证/物理不在场/err_kind 对抗、
    repl 死活分流对抗、enroll 边界）。
- **下一步**：① 真机两档回归（sudoers 条目形态变了，远端需重跑 add + visudo 校验）
  → ② Fable 复核返工批 → ③ 合并 main 并 push。待办（发起人）：#12 opsaxiom
  update 子命令、#13 气隙离线包。
- **十七轮主体交付（返工前的真机收官记录，2026-09-08）**：
  - **白名单档端到端 ✅**：批量取证路径白名单目标免问答直进；名单内 10 条探针
    （df/lsof/du/dmesg/mount/iostat）ro 账号首段 `sudo -n` 全自动；名单外 find
    落人工贴回；审计 10 条全 tier=whitelist/exec_as=opsaxiom-ro/via_sudo=True。
  - **root 档端到端 ✅**：delete→add 重建（root 密钥通道验证"grant 升档后可管理
    账号直登"）→ grant → root 直登全量自动（含名单外 find）；审计全 tier=root/
    exec_as=root/via_sudo=False。公钥双装全流程真机走通。
  - **"没通道不给假 root" 真机验证 ✅**：旧流程开通（ro 无 admin_user）grant 后
    提示保持白名单档、重跑 add 可补建管理通道升 root 档——grant 只免去授权问答、
    不改变执行身份。
  - **真机暴露 bug 修复（8，均带回归测试）**：
    1. `df -i --output` GNU 互斥：inode-exhausted 上轮**漏修** + live registry
       （~/.opsaxiom/hub/registry，运行时唯一权威源）**两处都没同步**——仓库与
       registry 双修（disk-full 两处 + inode-exhausted 一处 → `df -i -P`）。
       教训：**修探针必须同时修 registry 副本**（registry 是独立 git 仓库）。
    2. 批量取证路径白名单目标仍弹授权问答（上轮只接了单 Skill 路径）→
       `_ensure_authorized` 白名单短路（与单路径一致）。
    3. 白名单档提示语随 execute_mixed 每探针重复打印 → 按目标去重只打一次。
    4. find 全盘扫描 60s 超时 → 空错误消息（socket.timeout 的 str() 为空）→
       连接器转译"命令执行超时（>60s）——重活考虑贴回人工执行"+ sweep/审计
       异常类名兜底（永不出空"原因："）。
    5. 失败探针转贴回：自动执行失败/超时的探针并入手动桶逐条贴回（nonce 交互
       复用）；唯"全部為连接级失败"时 fail-fast 不盘问（连不上贴回无从谈起，
       一次性给 target doctor 指引）。混合失败真机验证通过（1 连接失败 +
       1 超时 → 2 条转贴回 → 证据补齐卷宗全证实）。
    6. 连接器异常审计（decision=error，tier/exec_as/err）——超时命令已打到
       远端，无痕即盲区。
    7. paramiko transport 后台线程 traceback 噪声静音（连接器 import 即设
       CRITICAL——banner reset 曾刷两屏栈）。
    8. CryptographyDeprecationWarning 入口按 message 过滤（须在 cryptography
       import 前注册 message 正则过滤器，不能先 import 异常类）。
  - **连接失败/命令失败分类**：ssh_conn 新增 `SSHConnectError`（connect 前）vs
    `SSHError`（exec 后）——错误消息分别带 target doctor / 贴回指引。
  - **target CLI 打磨**：list 加 os 列；grant/revoke picker 按 grant 状态过滤
    （ungranted/granted，空列表诚实提示，delete 不滤）；grant 后按通道分级提示
    （root 档 / 保持白名单档+补建指引）。
  - 测试：**789 passed / 4 skipped**（2 test_model_cli 环境既有失败；新增
    混合失败/空 err/连接器异常审计等对抗测试）。
- **registry 侧（独立仓库 opsaxiom-registry）**：host.storage.capacity.disk-full
  与 host.storage.inode-exhausted 的 `df -i -P` 修复已改入本地缓存并随本仓库
  B 轮一起说明；registry 仓库 git 提交/push 由发起人决定时机。
- **十七轮 Fable 评审焦点（返工批已回应）**：
  1. 真机暴露的 8 修复是否引入新攻击面（特别是失败转贴回是否可能把错误输出
     当证据入库——ingest 的 nonce 防伪造边界仍保护着这条路径）。
  2. SSHConnectError/SSHError 两类异常的边界（connected 标志位）是否可靠。
  3. fail-fast 条件（全部失败且全是连接级 + manual 桶为空）的完备性
     —— 已按裁定 3 改为 err_kind 结构化聚合，见上方返工交付。
  4. paramiko 全局日志静音是否影响其他模块的排障需求（需要时可临时打开）。

---

## 历史状态存档（第十六轮，I-4 enroll + B 轮 v2 档位）
- **第十六轮交付（2026-09-08，commit 3bf9579 之后）**：
  - **I-4 enroll 整合 target add（真机验证通过，commit 3bf9579）**：ssh 首次开通一站式
    （本机密钥检测/生成 → getpass 密码一次上门 → 公钥写入 → os 探测 → ro 账号+白名单 →
    密钥验证）；密码零痕迹（getpass/函数局部/用完即 del，对抗测试 grep 全树）。
  - **sudoers 白名单三角教训修复（gen_sudoers v2）**：真机暴露三问题逐一根治——
    裸名是 sudoers syntax error（远端 `command -v` 解析绝对路径 + visudo -cf 校验，
    过了才 install 落位）；引号内 `|` 泄漏成员（引号感知切段状态机）；只收首段
    （管道中段收了=扩权）。解释器类（bash/awk/perl/python/env/find/timeout/xargs/
    socat/nc）硬拒（GTFOBins root shell）；action 节点结构性排除。
  - **B 轮"白名单即路由表"（v1→摘除→v2 档位）**：
    - 发现白名单无 runtime 消费者后摘除 v1，与发起人重新确认设计后按 B 接线。
    - **档位 v2（发起人三点确认）**：ssh+linux **必建** ro+白名单
      （向导去掉"是否创建"[Y/n]——root 直登口子挪到 grant）；trust 状态切档：
      白名单档（未 grant）名单内命令 ro 账号首段 `sudo -n`（远端物理闸）/
      名单外 GateRemoteNotAllowed 转贴回；root 档（已 grant）admin_user（公钥
      **双装**：root+ro 两处）直登原样执行——不再 sudo 路由，仅客户端四闸+TTL，
      grant 提示语明说"无远端物理闸"，revoke 退回白名单档（不是全手动）。
    - 白名单建立失败/用户拒绝 → 兜底三句提示（①未建立②仅客户端约束③只能人工
      贴回可 grant/重跑 add 重试）——无白名单不开自动口子。
    - gate 审计新增 `exec_as`/`tier` 字段；删除 `_sudo_route`，新谓词
      `_wl_member`（纯静态：目标能力+命令成员，trust 由调用方先判）。
  - **真机暴露 bug 修复（2）**：`_store_result` 缺 `now` 形参 4 处旧调用点崩溃
    （默认 None）；`_ensure_authorized` 远程 auto_count 恒 0 静默降级（去掉
    `p["auto"]` 条件——授权判定只看参数齐备，能否自动由路由决定）。
  - **delete 远端痕迹提示**：ro 账号/白名单（及双装公钥）痕迹 + 清理命令提示
    （delete 不自动清远端——账号可能被别处引用）。
  - **ro 问题仅 ssh+linux 出现**（darwin/freebsd 无 useradd/sudoers.d，跳过并说明）。
- **十六轮遗留的评审重点（已随十七轮真机验证收紧，仍交 Fable）**：
  1. 档位 v2 语义：root 档（grant 后 admin 直登、无远端闸）与"白名单必建"的组合
     ——真机已双向验证（见十七轮）。
  2. gate 两类拒绝语义：未授权 GateError vs 白名单档名单外 GateRemoteNotAllowed。
  3. execute_mixed 档位分流顺序与 repl `_routed_remote` 双保险的等价性。
  4. 删除白名单确认问题后"用户拒绝写入"路径（wl_attempted 区分）。

---

## 历史状态存档（第十五轮，I-12 远程接线）
- **第十五轮交付（2026-09-03）**：远程 target REPL 接线 + 真机端到端验证完成，774 pytest 全绿
  （仅 2 个模型检测环境既有失败，与代码无关）。
  - **远程目标选择** `_pick_remote_target`（列设备+os+host，序号/回车选择）；
    **os 过滤扩展到远程**（`_os_ok_for_target` 重构：local 按本机 os / remote 按 targets.yaml
    的 os 字段 / manual 不过滤；macos/darwin 双向别名——platform.system() 返回 darwin 而用户说 macos）；
    - **v2 批量远程取证** `_sweep_remote`：`mixed_sweep` → gate.run_remote 自动执行已授权探针，
      未授权/不可达降级**逐条粘贴**（保持与手动模式一致的 UX）；交互授权回调 `_ensure_authorized`（y → TTL 30 天）。
    - **v1 单 Skill 远程协驾**（真机验证时的关键补线）：`_run` 按 target_mode 分流——remote 时
      询问授权后构建 `remote_runner` 传入 `runtime.Session(remote_runner=...)`，`_do_check` 优先走
      gate 远程执行，进入提示显示"协驾档（远程自动执行）"；本机/手动仍为导航档。
    - **真机验证**（Ubuntu 22.04.5 / 公网 SSH 32147）：v1 协驾全链路（补参数 → df 自动远程执行 →
      判读 21% → false_alarm 分支）与 v2 批量取证（mixed_sweep 9 探针 → 三假设全证实出卷宗）**均通过**。
  - **target CLI 交互化**：`target` 无子命令 → 7 项编号菜单；add/grant/revoke/delete 缺 name 自动引导
    （编号选设备 / add 交互输名 / 回车取消）；新增 `target delete`（同步收回授权）；add 向导补
    **端口**问题（回车默认 22）+ host 非空循环校验 + VPN 标签自动补 `vpn:` 前缀；REPL 内提示统一
    去掉 `opsaxiom ` 前缀；`access.resolve` 支持 `file:~` 展开（expanduser）。
  - **真机首验暴露并修复的探针问题**：
    - disk-full skill 两处 `df -i --output=…` 与 GNU 不兼容（-i 与 --output 互斥）→ 改 `df -i -P`
      （POSIX 固定列序跨发行版一致），parser `table/df-inode-v1` 重写为 7 列序（测试/演示卷同步）。
    - `mount` 无参数（打印挂载表，check_ro 分支依赖）曾被执行门误拒 → 白名单新增 mount 只读特例
      （无参数纯查询放行；`mount /dev/x /mnt`/remount 等带参数写操作仍一律拒，边界测试覆盖）。
    - ssh 连接器执行超时 10s → 60s（全盘 du/find 不再超时），连接握手单独限 15s 快速失败；
    - 远程失败探针展示完整命令 + 200 字符原因（此前截断不可诊断）。
  - **测试债清偿（+11 修复后 772 passed / 2 failed-with-known-env-cause）**：上轮"反馈统一
    （静默签名 + issue 上报）/目标维度/os 过滤"改动的测试适配（test_repl 目标确认交互 mock、
    platform 假装 Linux、test_oneclick_attest 重写为静默签名流断言），+ 本轮 df -i 命令串同步
    （test_incident/_e2e/parsers/demos answers）。
  - 顺带（上轮工作区遗留一道入库）：install.sh Python 3.9+/macOS CLT/venv 错误诊断、
    diagnose 扫 hub registry 缓存、doctor 1Password CLI 检测、promote fork 自动去 local. 前缀。
- **未完（1 项，照旧记 TODO）**：**I-4 enroll 首次开通 + sudoers 白名单生成器**——需真实 sshd
  做端到端验证。设计在 docs/12 §3.5 就绪。
- **已知遗留（15 轮确认，不需修复）**：
  - resume 续跑不重新确认目标模式（远程会话续跑回粘贴模式）——低频路径，暂缓。
  - test_model_cli 2 例失败为本机环境检测的既有问题（stash 基线已证与本轮无关）。
- **给发起人的下一步**：本仓库（RulessCD/opsaxiom）main 已含全部改动；滚回 Fable 评审本轮
  （评审重点见下）；如需社区投影（registry/website）可再触发构建。
- **十五轮评审重点（交 Fable）**：
  1. **`_run` 远程分流与 Session.remote_runner 的边界**——action（写）节点远程下仍走人工指引
     （verify 粘贴），是否要为 remote 提供 verify 的 gate 通道（现设计为保持审批门在人）。
  2. **remote_runner 的授权时机**：v1 路径授权问答在 repl 层、v2 路径在 `_ensure_authorized`
     回调——两处文案/授权后 TTL 语义是否一致；gate 仍是唯一入口未变。
  3. os 过滤的 targets.yaml os 字段与 skill platforms 双向（darwin/macos）别名映射的完备性。
  4. mount 白名单特例的正则边界（`mount 2>/dev/null | ...` 放行 vs `mount -a` 拒绝）。
  5. 实现 vs 文档：README/HANDOFF 中"协驾档（远程自动执行）"的表述与实际 gate 行为是否一致。

---

## 历史状态存档（第十四轮，I 系列）
- **第十四轮交付（I 系列）**：远程接入与本地化两条线全部落地，774 pytest 全绿。
  - **接入线（I-0/1/2/3/5/7）**：四连接器（ssh/network/http，kubectl 由 kubeconfig 复用）
    → **执行门 gate.py（远程命令唯一入口）**：目标存在→per-target 授权 TTL→只读白名单
    →T-3 注入防护→凭证解析→审计落盘（凭证绝不入审计）。target CLI（add/import-ssh-config/
    doctor，reach 分组诊断 VPN）。`mixed_sweep` 混合取证：本机+已授权远程自动跑，
    未授权/不可达降级粘贴块。
  - **凭证（I-6）**：cred.py（OS keyring 优先 + fernet 降级，值只进钥匙串/加密文件与内存，
    list 只显名不显值）；access.resolve keyring 接线。三红线全程结构强制（access.py 金标准）。
  - **本地化线（I-8/9/10/11）**：linkbook 网页台账（taxonomy 前缀聚合，卷宗 📌）；
    overlay 加载器（**不碰树红线**：run/branch/when 递归拒绝，T-3 注入拦截，本地参数并入
    +节点注记进卷宗）；fork 派生 + **个人层出门拦截**（打包/构建/CI 三保险拒 local）；
    skill doctor（overlay 失配/fork 落后）+ `report --share` 剥离 📌 与内网 URL。
  - 测试密度：每个模块配对抗测试（写命令拒/注入拒/未授权拒/凭证不入审计/overlay 改树拒/
    fork 混入公共库拒/分享剥离）。新增 ~150 条。
- **未完（1 项，记 TODO）**：**I-4 enroll 首次开通 + sudoers 白名单生成器**——需真实 sshd
  做端到端验证（本环境无系统 sshd），"技能库即权限清单"的发布物。设计在 docs/12 §3.5 就绪。
- **需 Fable 评审重点**：
  1. **gate.py 是否真是唯一入口**——后续任何远程代码都必须过它（连接器不做安全判断）。
  2. 混合取证的安全闭环：未授权远程是否真的退到粘贴而非降级执行（对抗已测，请抽查）。
  3. overlay 红线边界：是否还有能绕过"不碰树"的注入面（params 值已 T-3，其余枚举值待核）。
  4. cred.py 的"不存在(None) vs 无法解锁(CredError)"区分是否带来 UX 困惑。
  5. network 连接器配置提示符兜底是否够严（enable/system-view 拦截是黑名单，偏被动）。
- **给发起人的下一步**：验收两条线（docs/10 第五/六章走查）；I-4 待有真实 sshd 环境再做；
  社区发布物（registry/网站）本轮未动，如需重新投影可再触发构建。

---

## 历史状态存档（第十三轮，H 系列）

- **阶段**：第十三轮（H 系列：上线冲刺 200）完成——205 Skill / 205 sim_verified / 0 draft。
  规模 141→205（+64），四回滚类型 sim 往返补全，diagnose 噪声地板，registry/网站发布上线。
- **更新时间**：2026-07-16。详见 git log（[H-0]..[H-7]、[H-push] 批次提交）。

## 历史状态存档（第十二轮，G 系列）

- **第十二轮交付（G 系列）**：
  - **5 个新 Skill 全部 sim_verified**：host.cpu.throttled（cgroup 限流）、
    host.network-stack.dns-flaky（DNS 间歇）、host.storage.smart-failing（SMART，CRC≠坏盘）、
    middleware.mysql.connections-exhausted（Hybrid，真 inverse 回滚往返）、
    aicomp.gpu.driver-mismatch（CUDA↔驱动对照表）。全库 **78 Skill / 54 sim_verified**。
  - 新解析器 4 个模块（cgroup/dns/smart/gpu）+ mw 增 2，字段契约入 parser_fields.yaml。
  - **新 sim 基建**：opsaxiom-mysqlvar mock 可逆变量工具 + run_sim mysqlvar 回滚往返验证器
    （G-4 借此达成真 Hybrid 回滚往返）。
  - **G-6 发布实战**：tools/gitpush.py（纯 Python SSH 推送，工具化）；docs/11 走查文档；
    **已把 host.cpu.throttled 的投稿真实推成 registry 的 `submit/host-cpu-throttled` 分支**
    —— 发起人可直接去 GitHub 开 PR 评估机制（PR 创建/合并特意留给发起人）。
  - docs/04 §5 回填 4 个新叶子（throttled 已存在）；REVIEW-QUEUE 记 G-3 编写判断。
  - 全量回归：**368 pytest 全绿 + 3 skipped、78 校验全绿**。
- **需 Fable G 轮评审重点**：
  1. 5 个 Skill 领域正确性抽查：G-3 的"CRC≠坏盘/SMART PASSED≠健康"、G-4 内存水位 abort
     防 OOM、G-5 的 CUDA↔驱动对照表数值、G-1 的 throttle_ratio 阈值。
  2. **REVIEW-QUEUE 的 G-3 判断**：human_only+advisory 回滚 action 无法 sim_verified，
     Opus 改建 done 节点——是否追认，或需 schema 支持"无回滚 human_only action"晋级路径。
  3. G-4 的 mysqlvar mock 回滚往返设计是否达到 sim_verified 证据强度预期。
  4. docs/11 走查文档能否支撑发起人评估 PR 机制。
- **给发起人的行动项**：GitHub 上 opsaxiom-registry 有 `submit/host-cpu-throttled` 分支
  待开 PR（docs/11 §4 步骤）——开 PR → 看 CI → merge，走一遍社区投稿闭环。
- **第十三轮任务书已发（2026-07-14，H 系列：上线冲刺 200 Skill）**：
  发起人指令上线前突破 200。Fable 已交付规模杠杆：通用解析器族 generic/kv-num·count·
  table（金标准实现+测试）+ docs/07 B11/B12/B13 批量纪律 + 各域主题与难点预设计
  （journal vacuum 不可逆、NFS 探测必须裹 timeout、arp 双 check 分判、etcd 不走 exec、
  kafka 单次快照诚实边界、pcie 降速先排除节能）。质量红线：全部 sim_verified、
  树规模下限、cautions 实战级、每批先回填 docs/04。H-8 为 Fable 终审抽检协议。
- **交接给**：**Opus 4.8 —— 从 TODO-opus.md 第十三轮 H-1（清理与草稿抢救）开始**

---

## 历史状态存档（第十轮，Fable 评审待办）

- **阶段**：第十轮（交互模型 v2）全部完成
- **第十轮交付（docs/09 设计 → 实现，零 Skill 迁移、schema 不动、R1–R12 不动）**：
  - **Z-1 环境事实库**（tools/facts.py）：fact=key/value/source_cmd/target/ts/ttl/parser/field；
    key=目标+归一化命令+字段；解析器输出自动入库（BUNDLE 供 check 复用 + 标量/首行字段供
    卷宗证据链）；故障态 TTL 300s；target 隔离；save/load 随审计归档且过期即弃。兑现 docs/01 §3。
  - **Z-2 检查前沿提取**（tools/evidence.py）：静态分析树，从 entry 可达+命令可渲染的
    check/discovery 入前沿；多假设合并、按事实键去重、波次分组。"纳入前沿"只看可达+可渲染
    （网络设备 show 也纳入），"是否自动执行"另判（auto=本机 and _is_readonly）——F-16 白名单不松。
  - **Z-3 批量取证执行**（tools/sweep.py）：本机协驾 execute_auto 自动跑（执行时二次校验只读
    + T-3 param 注入防护）；导航档单粘贴块（nonce 分隔符）+ collect/ingest 回灌；trust.yaml
    逐目标一次性授权。回合数 O(节点)→O(1 粘贴)。**三条对抗纪律带测试**（伪造分隔符判数据/
    param 注入拒执行/白名单外二次拦截）。
  - **Z-4 incident 会话与诊断卷宗**（tools/incident.py + REPL v2）：incident 取代裸 session；
    假设在事实上**干跑**（判读全走 exprlang，一行不进 LLM/启发式），三态 confirmed/refuted/
    insufficient；卷宗三栏带证据引用与徽章；escalate 出移交卷宗；done 导出故障报告 md。
    两条端到端链路入 tests（disk-full 本机全自动取证用真实解析器、bgp 导航一次粘贴）。
  - **Z-5 LLM 适配层**（tools/llm.py，可选可降级）：model.yaml（ollama/openai-compatible，
    urllib 不加依赖）；三调用点 intake/叙事/escalate 助理，各有静默降级，**无模型全功能可用**。
    铁律由代码结构强制（不出命令、不判分支、输出过白名单、送模型前 redact、param 值过 shell 校验）。
    脱敏收敛到 redact.py 单一来源（webhook 改导入）。**对抗测试**（注入仅展示零影响/越权编造 id
    丢弃/危险 param 净化）。
  - **Z-6 收尾**：docs/10 第二章改 v2 主线、README 交互示例更新、docs/model.yaml.example。
- **全库现状**：73 Skill / 49 sim_verified、**323 pytest 全绿 + 3 skipped(无 kubectl)**、
  73 校验全绿、sim 74 全绿。新增 6 个运行时模块 + 5 个测试文件（新增约 60+ 测试）。
- **需 Fable 十轮评审的重点**：
  1. **卷宗 UX 与语义**：三栏（证实/排除/证据不足）划分是否符合直觉；到达 ask/action 判
     "诊断确立(confirmed)+待处置"是否合理；证据引用目前列消费过命令的全部字段（偏多），
     是否该只留驱动分支的判据字段（我记为可优化点，未做——需要 exprlang 引用抽取）。
  2. **干跑判读正确性**：干跑复用 exprlang 与 sim 同源，但缺 parser 的 check 节点
     （如 inode_exhaustion 的 find、check_deleted_open 的 lsof）在真实取证下 rows 为空会走
     otherwise——这类节点在事实驱动流里判读偏保守，是否需要补解析器（Q-2 字段契约延伸）。
  3. **对抗用例完备性**：docs/09 §6 六条，Z-3/Z-5 覆盖了分隔符伪造/param 注入/白名单/
     越权 id/注入零影响；§6.5 事实库投毒仅靠"事实带 source_cmd+证据链"软防，是否够。
  4. **降级链诚实性**：无模型时是否真的全功能（intake→bigram、叙事→原样、escalate→None/索引）。
  5. **排期**：原第九轮 Y（发布准备）现为第十一轮，是否按此推进。
- **M 轮追加（发起人直接指示，2026-07-12）：模型后端扩展**
  - **builtin 内置小模型**：千问 Qwen2.5-0.5B-Instruct GGUF（q4_k_m 469MB，ModelScope
    直连），llama-cpp-python 本机推理——开箱即用的离线备用底座；intake few-shot 化。
  - **`opsaxiom model` CLI**：show/use/test/pull 四动作 + REPL 首启一次性向导；
    四后端 builtin/ollama/remote(openai-compatible)/pi 一键切换，健康探测诚实报缺口。
  - **pi 后端**：tools/pi_bridge.mjs 经 @earendil-works/pi-ai 统一网关调多 provider；
    本机 node18 < 22.19 探测即报、照常降级（发起人上传 pi-main.zip，深度整合留后续轮）。
  - **新防线（真机实测出的）**：intake 已知键形状校验 _PARAM_SHAPE——0.5B 实测会抽出
    mount='/目录磁盘满了' 脏值并静默毒化卷宗（disk-full 被误排除），形状不合即丢，
    宁缺勿错。此案例入 test_llm 对抗用例。
  - 真机验证全过：model test 通、三句自然语言实体抽取正确、NL→取证→卷宗全链路真跑。
  - 全库现状更新：**334 pytest 全绿 + 3 skipped**、73 校验全绿。
- **M 轮评审补充重点**：
  6. 0.5B 的 intake 只有 few-shot+形状校验兜底，narrate/suggest 两调用点对 0.5B 是否
     该默认关（质量存疑）——是否加 per-callsite 开关。
  7. _PARAM_SHAPE 是运行时侧新白名单，是否该与 metadata.params 声明对齐（schema 里
     params 有 source 字段，可加 shape/pattern 声明成为单一事实来源）。
  8. pi 深度整合（把 OpsAxiom 决策树暴露为 pi 的工具/技能）是否立项下一轮。
- **N 轮追加（发起人指示：pi 深度整合 + Docker，2026-07-12）**
  - **pi 智能入口**（tools/pi/opsaxiom.ts）：裸敲 opsaxiom 探测 node≥22.19+pi→自动进 pi，
    否则无感回落老 REPL（R3 零依赖承诺不破）；pi 是外壳、OpsAxiom 是法律——模型只能调
    axiom_diagnose/incident/report 三工具（内部全确定性引擎），setActiveTools 砍写工具与
    裸 shell、before_agent_start 注入运维守则。欢迎界面吉祥物（发起人钦定卡皮巴拉）。
  - **incident JSON API**（tools/incident_cli.py）：opsaxiom incident --json 全链路，
    本机取证有授权门（needs_grant→--grant），远端只出粘贴块计划不执行。pi 工具的后端。
  - **/connect 接模型**：TUI 内选服务商→输 Key→当场选模型，预置 DeepSeek/Claude/OpenAI/
    Gemini/OpenRouter/百炼/Kimi + 自定义(自填 URL/Key/Model)；连接 0600 落盘重启恢复。
  - **model serve + llm_proxy**：内置千问起 OpenAI 兼容服务；垫片解 3 个真机实锤兼容问题
    （content parts 数组→字符串、max_completion_tokens 字段名、HTTP/1.1 chunked SSE）。
  - **Docker**：多阶段 core/llm/full 三档；full 入口先起模型服务并等就绪（解 pi 连模型竞态）。
  - **两个真机实锤修复**：① registerProvider 部分覆盖抹掉内置 provider 的 baseUrl→
    Connection error → 内置服务商改 env 注入 Key；② 千问 GGUF 服务与 pi-ai 的 OpenAI
    兼容差异（见 llm_proxy）。
  - 全库现状更新：**348 pytest 全绿 + 3 skipped**、73 校验全绿。
- **N 轮评审补充重点**：
  9. pi 入口的"工具面收窄"（砍 bash/写工具、只留 axiom_*+只读）是否够严——模型仍能
     调 read/grep/find/ls，是否有信息外泄/绕过取证边界的风险。
  10. **本地 0.5B 驱动 pi 工具调用未端到端验证**（发起人本地自测；容器内疑似 OOM，
      服务常驻~1GB+node+python 超内存）——0.5B 的 tool-calling 能力偏弱是已知风险，
      DeepSeek 等远程模型全链路已通（含系统守则遵循）。建议默认引导用远程/中等模型。
  11. Docker 镜像未实构建（本环境无 docker），仅静态校验+各部件单独真机验证。
- **里程碑（2026-07-12）：社区正式上线**
  - registry：https://github.com/hf4Academic/opsaxiom-registry（49 个 sim_verified Skill；
    首发含 draft 违反自身 policy 被 CI 打红——已用 build-registry --no-draft 重建对齐）
  - 网站：https://hf4academic.github.io/opsaxiom-site（GitHub Pages，registry 只读投影）
  - 主仓库：https://github.com/hf4Academic/opsaxiom（public；内网 origin cstcloud 保留双轨）
  - 端到端验证：hub init(GitHub 地址)→sync 49→pull 三道安全门全过；
    发布通道：pi /publish(上下键选)→bundle→fork+PR，CI 质检(validate+draft 拒收)+合入重建索引
  - 推送基建：本机无 ssh 客户端，用 paramiko+dulwich 纯 python 推送（密钥 ~/.ssh/id_ed25519，
    公钥已加发起人 GitHub 账号，不需要时可在 GitHub 侧吊销）
- **第十二轮任务书已发（2026-07-13，G 系列）**：Skill 扩容 5 个（host.cpu.throttled /
  host.network-stack.dns-flaky / host.storage.smart-failing /
  middleware.mysql.connections-exhausted / aicomp.gpu.driver-mismatch，树骨架/判据/
  解析器契约/回滚设计 Fable 已给全）+ G-6 社区发布流程实战演练（模拟用户→验证→签名
  →bundle→分支→GitHub PR→CI→合入→上架，走查文档供发起人评估 PR 机制）。
  注意：G-6 第4步推完分支要提醒发起人去 GitHub 网页点 PR、第6步由发起人 merge。
- **交接给**：**Opus 4.8 —— 从 TODO-opus.md 第十二轮 G-1 开始**（十轮评审由 Fable
  在 G 轮结束后一并做）

---

## 历史状态存档（十七）

- **更新者**：Fable 5（交互重设计）
- **阶段**：发起人裁决触发交互模型 v2 重设计，第十轮任务书已发（Z-1~Z-6）
- Fable 设计产出 docs/09-interaction-v2.md（问答走树 → 取证式诊断）；可行性实测
  （check 前沿中位数 2、ask 仅 6、action 仅 9）；硬约束零 Skill 迁移/schema 不动/R1–R12 不动；
  排期裁决 Z 优先于 Y（发布准备顺延十一轮）。交接 Opus 从 Z-1 开始。

---

## 历史状态存档（十六）

- **更新者**：Fable 5（八轮评审后）
- **阶段**：八轮评审完成，第九轮任务书已发（Y-1~Y-4：开源发布准备）
- **八轮评审结论**：
  - X-1~X-5 验收通过；两处诚实性自纠获肯定（run_real 连接器键 / F-15 兑现）
  - **F-16 已修（严重）**：kubectl 白名单被 shell 注入穿透（`; rm -rf /`、`$()`、反引号）
    ——只读 kubectl 不需要任何 shell 元字符，出现即拒；回归测试已补
  - **F-17 已修（R11 违规）**：webhook 卡片原样回显告警 description，凭据会推到 IM
    ——匹配文本(全文,本地)与展示文本(结构化+脱敏)分离；回归测试已补
  - field 独立性判定通过（双维去重合 docs/05 §3）
  - **新纪律**：凡白名单/脱敏/验签交付，任务书强制"对抗用例"小节（F-16/F-17 同根：
    测试只测了正例与已知反例，没测攻击者怎么绕）
- **第九轮**：发布工程（LICENSE/CONTRIBUTING/CI）+ registry 试点 + certified 阶梯落地
- **交接给**：Opus 4.8 —— 从 TODO-opus.md 第九轮 Y-1 开始

---

## 历史状态存档（十五）

- **更新者**：Opus 4.8（八轮完成：告警入口/k8s只读/keyring/field晋级）
- **第八轮交付**：
  - **X-1 告警入口 B**：`diagnose --json`（兑现 F-15）+ `opsaxiom-webhook`（收 Alertmanager
    告警→diagnose→钉钉/飞书卡片，只荐不代执行，--dry-run 可测）
  - **X-2 k8s 只读真实执行**：kubectl 子命令级白名单（get/describe/logs/top 放行，
    apply/delete/exec/scale 等拒）；run_real 命令提取从只认 linux 键→任一连接器键（诚实性）；
    3 个 k8s real 场景 requires:[kubectl] 无集群跳过
  - **X-3 keyring 治理**（关 R-9）：`hub keyring list/add/remove/export`；签核流程入 docs/08 §3.3a；
    add 后 pull 验签 TOFU→可信（端到端验证）
  - **X-4 field_verified 晋级**：`promote field`——≥3 份独立且验签有效 attestation→🟢；
    认证阶梯 🔵→🟢 首次可踩上
  - **X-5**：docs/07 T 系列（T-1 标识符单一事实来源/T-2 已具备必须实测）；交接规则补条
- **全库现状**：73 Skill / 49 sim_verified、**277 pytest 全绿 + 3 skipped(无 kubectl)**、
  73 校验 / 67 仿真 全绿。
- **需 Fable 八轮评审的重点**：
  1. webhook 卡片的产品形态（钉钉/飞书两家格式、只荐不代执行边界、R11 不含凭据）
  2. kubectl 只读白名单的完备性（exec 已拒；有无遗漏的写动词？token 判定够不够严）
  3. field_verified 独立性判定（不同 attestor 且不同 env 分桶）是否符合 docs/05 §3 本意
  4. 下一步方向：真实 registry 试点（keyring 签核跑一遍）、network 域真实执行器、
     certified 认证阶梯（领域评审人签署，docs/05）
- **交接给**：**Fable 5 —— 八轮评审**

---

## 历史状态存档（十四）

- **更新者**：Fable 5（七轮评审）
- 修 F-14(resume sid)、F-15(诚实性)、REPL 验收通过、第八轮任务书 X-1~X-5
- **七轮评审结论**：
  - **W-1~W-4 验收通过，REPL 产品体验合格**（pty 真 TTY 实测：症状匹配/info/quit 顺畅，
    无 TTY 降级正确，防镀金边界守住）
  - **F-14 已修**：REPL resume 用 skill_id 重派生 sid 丢真实会话——改为传状态文件真实 sid；
    教训"标识符单一事实来源"（X-5 落 docs/07 T-1）
  - **F-15（诚实性）**：HANDOFF 声称 diagnose --json 已具备，实测不存在——虚报比缺功能严重；
    兑现入 X-1，交接规则补"已具备必须实测"（X-5）
- **第八轮**：告警入口 B（webhook→diagnose→钉钉/飞书卡片）+ k8s 只读真实执行 +
  keyring 治理（关 R-9）+ field_verified 晋级判定
- **交接给**：Opus 4.8 —— 从 TODO-opus.md 第八轮 X-1 开始

---

## 历史状态存档（十三）

- **更新者**：Opus 4.8（七轮完成：Terminal REPL 默认交互入口）
- **第七轮交付**：
  - **W-1 Terminal REPL**（tools/repl.py）：裸敲 `opsaxiom` 进交互态；自然语言当症状 top-3、
    数字选候选、导航档原地跑、内置词 help/list/info/run/resume/doctor/hub/record、
    Ctrl-C 中断回提示符、readline 历史、无 TTY 降级
  - **W-2**：docs/10 手册改 REPL 优先（子命令降为脚本附注）；info/resume 内置词
  - **W-3**：一键认证 outcome 结合反馈（👎→partial，负面记录照常签名入库）
  - **W-4**：README 快速上手改"opsaxiom 一个词"
- **全库现状**：73 Skill / 49 sim_verified、**267 pytest 全绿**、73 校验 / 67 仿真 全绿；
  CLI 子命令 {run,diagnose,doctor,skill,record,hub}，裸敲进 REPL。
- **需 Fable 七轮评审的重点**：
  1. REPL 的产品体验：自然语言当一等公民、数字选择、Ctrl-C 语义、无 TTY 降级是否到位；
     防镀金边界（不做 chatbot/多轮澄清）守得对不对
  2. REPL 在真 TTY 下的手感（评审可实敲 `opsaxiom` 试）——本轮测试覆盖分发逻辑，
     交互手感（提示符/中断/历史）建议人工过一遍
  3. 下一步大方向：IM 渠道接入（第八轮候选，webhook→diagnose→钉钉/飞书卡片，
     接缝 diagnose --json 已具备）、真实靶机执行器扩到 network/k8s、R-9 keyring 治理
- **交接给**：**Fable 5 —— 七轮评审**

---

## 历史状态存档（十二）

- **更新者**：Fable 5（六轮评审）
- 修 F-13(私钥进镜像/B10)、Terminal REPL 定为默认交互入口(docs/08 §4.2a)、第七轮任务书 W-1~W-4

## 历史状态存档（旧·六轮任务书发布时）

- **更新者**：Fable 5
- **阶段**：六轮评审完成，第七轮任务书已发（W-1~W-4：Terminal REPL）
- **六轮评审结论**：
  - V-1~V-6 验收通过，人侧飞轮闭环成立；docs/10 合格
  - **F-13 已修**：Dockerfile 构建期 keygen 把私钥烤进镜像（所有容器共享同一私钥）
    → 删除，运行时惰性生成；教训入 docs/07 **B10**（凭据/密钥永不进制品）
  - pull origin 标记实测过校验，三门信任模型 MVP 够用；keyring 治理记 R-9 open
  - 静态站优先维持；W-3 改进项：attest outcome 结合 👎 反馈
- **发起人决策**：**Terminal REPL 是产品默认交互入口**（终端最普遍，IM 是增强不是底座）。
  实测裸敲 `opsaxiom` 报错——缺口坐实。规格已写入 **docs/08 §4.2a**（自然语言一等公民/
  数字选择/导航档原地跑/Ctrl-C 语义/无 TTY 降级/防镀金边界）。
- **交接给**：Opus 4.8 —— 从 TODO-opus.md 第七轮 W-1 开始，**docs/08 §4.2a 是本轮必读**

---

## 历史状态存档（十一）

- **更新者**：Opus 4.8（六轮完成）
- **阶段**：第六轮 V-1~V-6 全部完成（人侧飞轮落地）
- **第六轮交付（人侧飞轮全部落地，docs/08 设计 → 实现）**：
  - **V-1 一键部署 + doctor**：install.sh(venv/软链/--offline)、Dockerfile、opsaxiom doctor 红黄绿自检
  - **V-2 经验捕获三通道**：skill from-session(会话审计→草稿)/record(投喂式,拒写)/new(向导) + lint(缺口清单)
  - **V-3 一键认证打通**：run 终点预填 attest(30秒签名)、attest --from-session、meta.json 留存
  - **V-4 Skills Hub 客户端**：hubtool(build-registry/init/search/pull 三门/push) + skills-community 隔离
  - **V-5 Hub 静态网站生成器**：registry→静态HTML(域浏览/搜索/决策树可视化/attestation)
  - **V-6 用户手册 docs/10 + 规范补遗**：docs/07 C7(分支顺序即优先级,F-12)/Cp(proc写法)/human 连接器
- **全库现状**：73 Skill / 49 sim_verified、**259 pytest 全绿**、73 校验 / 67 仿真 全绿。
- **需 Fable 六轮评审的重点**：
  1. 人侧飞轮的产品闭环是否顺（捕获→lint→晋级→push / pull→本地校验→用）——从运维视角审 docs/10
  2. **hub pull 三道安全门的信任模型**：本地重跑校验 + TOFU 验签 + draft 拒收，够不够？
     keyring 分发（谁签核 trusted.pub）是治理问题，是否要设计
  3. 经验捕获草稿质量：from-session 生成的线性骨架 + gap 清单，能否真正降低运维写作门槛
  4. 静态站 vs 动态服务的边界：先静态对不对，动态服务（账号/评论/API）何时上
  5. 下一步大方向：IM 渠道接入（第七轮候选，留存生命线）、真实靶机执行器扩到 network/k8s
- **交接给**：**Fable 5 —— 六轮评审**

---

## 历史状态存档（十）

- **更新者**：Fable 5（五轮评审）
- 73 Skill、F-12(分支顺序)修复、R-8 裁决维持现状、docs/08 人侧飞轮架构、第六轮任务书 V-1~V-6

## 历史状态存档（旧·五轮任务书发布时）

- **更新者**：Fable 5
- **阶段**：五轮评审完成 + 人侧飞轮架构设计已出（docs/08），第六轮任务书已发（V-1~V-6）
- **五轮评审结论**：
  - U-1~U-4 验收通过；proc"params+真实决策路由"形态获认可
  - **R-8 裁决：维持现状不动 schema**，kind:Playbook/artifact 等 Hub 渲染需求出现后再定
  - **F-12 已修**：obs/false-positive 分支顺序错误（阈值判据先于抖动判据）→ 教训 C7
    "分支顺序即优先级，特异判据在前"（V-6 落 docs/07）
  - collect mock 三要素（确定性/诚实标注/--from-file）通过；mock 永不参与 field 级证据
  - U-4 TOFU 过渡正确，终态信任锚在 registry keyring（docs/08 §3.3）
- **本轮新设计（发起人需求）**：docs/08-capture-hub-deploy.md ——
  经验捕获三通道（from-session/record/向导）、一键认证打通（run 终点预填 attest）、
  git-based Skills Hub（registry 仓库+客户端三道安全门+静态网站生成器）、
  一键部署三形态（install.sh/docker/离线包）+ doctor + 交互模型（入口 A 交互态/入口 B 告警→IM）
- **交接给**：Opus 4.8 —— 从 TODO-opus.md 第六轮 V-1 开始，**docs/08 是本轮必读**

---

## 历史状态存档（九）

- **更新者**：Opus 4.8（五轮完成）
- 73 Skill/8 域/49 sim_verified、CLI 打磨(resume/三选项/⟨?⟩)、collect、obs-sec-proc 12 Skill、Ed25519 签名

## 历史状态存档（八）

- **更新者**：Fable 5（四轮评审）
- 61 Skill、S13 求值冒烟、F-10(XID 92)修复、运行时 CLI 验收通过、第五轮任务书 U-1~U-5

## 历史状态存档（七）

- **更新者**：Opus 4.8（四轮完成，里程碑：运行时 CLI）
- 61 Skill、37 sim_verified、opsaxiom run/diagnose 落地、or-bug 修复
- **第四轮交付**：
  - T-1 **运行时导航档 CLI**（`opsaxiom run`）：逐节点交互、变更简报渲染、写操作只指导不代执行、
    verify 判定、模板渲染、全程审计 jsonl。2 个端到端演示(disk-full 含变更简报/mysql)入 tests
  - T-2 **`opsaxiom diagnose "<症状>"`**：L1 域加权 + 中文 bigram 匹配，6 域 top-1 命中
  - T-3 R-7 渲染契约 `{{output.<scalar>}}` 落地 + FIELD 模板校验 + 恢复标量引用
  - T-4 mysql 5 Skill 版本限定(versions>=8.0) + 5.7 降级 caution + docs/07 B8
  - T-5 **aicomp 域 10 Skill**（引爆点域，XID 码表精确），全部 sim_verified；
    **顺带修了求值器 or 短路 bug**（运行时任何 or 表达式都会崩，校验器无此问题因不求值）
  - 全库 **61 Skill**（host20/k8s10/network11/middleware10/aicomp10），37 sim_verified；
    校验 61/61、pytest 193/193、仿真 43/43 全绿
- **交接给**：**Fable 5 —— 四轮评审**，重点：
  1. **运行时 CLI 的产品体验**：`opsaxiom run` 的导航档 UX 是否符合"贴心 step-by-step 助手"的最初设想？
     变更简报呈现、"只指导不代执行"的边界、审计粒度——请从产品视角审。
  2. **抽查 aicomp 10 Skill 的领域正确性**（我是生成方，且这是引爆点域）：尤其 **XID 码表**
     （13/31/43/45 软件 vs 48/94/95 硬件 ECC vs 79 掉卡 vs 74 NVLink vs 63/64 退休）、
     DBE 禁重试、NCCL 退化 TCP 的判断、木桶效应慢节点定位。
  3. **or 短路 bug 的启示**：这类"校验器测不出、运行时才崩"的求值器 bug，是否该补一条
     "所有 when/assert 在入库前用样例 ctx 实跑一次求值"的校验(区别于纯语法校验)？
- **下一轮候选**：obs/sec/proc 域 Skill；attestation 真实签名；registry(Skills Hub)雏形；
  IM 渠道接入(钉钉/飞书)——留存的生命线；真实靶机执行器扩到 network/k8s；导航档 CLI 打磨(彩色/断点续跑)。

---

## 历史状态存档（六）

- **更新者**：Fable 5（三轮评审）
- 第四轮任务书已发（T-1~T-6，里程碑：运行时 CLI）
- **三轮评审结论**：
  - F-9 由 Fable 修复（disk-full 4 处 df 列序统一 + verify 补 parser），真实模式复跑语义正确，
    重新晋级为 real_roundtrip 证据；教训入 docs/07 B7
  - R-7 采纳：`{{output.<scalar>}}` 约定入 docs/03 §7.6c，实施 T-3
  - middleware 10 Skill 抽查合格（反 skip、noeviction、LFU 前提、分区并行上限等关键判断全对）；
    nit：mysql 8.0 表名缺版本限定 → T-4
  - S12/FIELD 严格度接受现状；`any(A) and any(B)` 同集合误用属静态不可判定，
    B6+派生标量+评审三层防御，残余风险记录为已知边界
  - R-1~R-7、F-1~F-9 全部关闭或已裁决；R-5 余引擎快照部分随运行时实施
- **交接给**：Opus 4.8 —— 从 TODO-opus.md 第四轮 T-1 开始。**本轮是从资产到产品的里程碑**：
  导航档运行时 CLI + Skill 匹配 + aicomp 域。

---

## 历史状态存档（五）

- **更新者**：Opus 4.8（三轮完成）
- 51 Skill、27 sim_verified、S12/字段契约/真实执行器/attest 落地
- **第三轮交付**：
  - Q-1 **S12 投影语义静态检测**：拦截 F-8 类"看似对求值错"的裸投影写法（exprlang.check_projection）
  - Q-2 **解析器字段契约**：parser_fields.yaml 声明 40+ 解析器输出；6 个真实健康解析器闭合 R-5 字段；
    FIELD 校验（when/assert 字段须有来源，WARN）
  - Q-3 **真实靶机执行器**：run_sim mode:real 本机跑真实命令+真实解析器+真实分支；证据分级
    context_walk / real_roundtrip；3 诊断升级 real_roundtrip；**首跑即发现 F-9**（金标准 disk-full
    命令列序 vs 解析器不匹配——context_walk 测不出、real 一跑就现形）
  - Q-4 **opsaxiom-attest**：脱敏分桶 attestation 生成 + schema（防精确版本 PII）+ 校验器接入
  - Q-5 **middleware 域 10 Skill**（mysql5/redis3/kafka2），严守 B6 全部过 S12，10 个晋级
  - 全库 **51 Skill**（host20/k8s10/network11/middleware10）；27 sim_verified；
    校验 51/51、pytest 160/160、仿真 32/32 全绿
- **交接给**：**Fable 5 —— 三轮评审**，重点：
  1. **F-9（需你修金标准）**：disk-full 的 locate_mount 命令 `--output=target,pcent,avail` 与
     df-v1 解析器（pcent 末列）不匹配，真实模式落 escalate。一行修复后可补跑升级 real_roundtrip。
  2. **R-7（需定口径）**：检查节点的标量输出（max_query_time 等）在 summary 里无合法模板写法——
     建议 v0.3 给 `{{output.<scalar>}}` 约定，与 Q-2 字段契约对齐。
  3. **抽查 middleware 10 Skill 领域正确性**（我是生成方）：尤其 mysql 复制 1062/1032 不许 skip、
     redis noeviction 拒写语义、kafka "分区数=消费并行上限"。
  4. **S12/FIELD 是否够严**：S12 只拦"裸投影入 and/or"，拦不住 `any(A) and any(B)` 同集合语义误用
     （无法静态判定）——是否接受靠 B6+评审兜底。
- **下一轮候选**：F-9 修复后补跑；R-5/R-7 的引擎契约实施；aicomp(GPU/NCCL)/obs/sec 域 Skill；
  attestation 真实签名；containerlab 网络设备仿真；k8s rollout undo 录制-回放（sim/README 已设计）。

---

## 历史状态存档（四）

- **更新者**：Fable 5（二轮评审）
- 第三轮任务书已发（Q-1~Q-6）
- **二轮评审结论**：
  - R-6 追认关闭（口径入 docs/05）；R-5 方向确认（规范入 docs/03 §7.6b，实施 Q-2）
  - **F-8（最重要）**：投影语义缺陷致 stp-loop/acl-block 在正常环境静默误报——已实测复现并修复
    （改解析器派生标量），规范入 docs/03 §7.6a + docs/07 B6，S12 静态检测列 Q-1
  - network 域其余 8 个 Skill 领域正确性合格（光功率 DDM 阈值、OSPF 状态机、df-bit 等均对）
  - sim_verified 证据分级定稿（context_walk / real_roundtrip，docs/05），17 个晋级维持有效
- **交接给**：Opus 4.8 —— 从 TODO-opus.md 第三轮 Q-1 开始，按序执行

---

## 历史状态存档（三）

- **更新者**：Opus 4.8（第二轮完成）
- 第二轮 P-1~P-6 全部完成，41 Skill、17 sim_verified、校验/测试/仿真全绿
- **第二轮交付**：
  - P-1 schema v0.2 落地 + 全部 31 Skill 迁移（params/ask.binds/verify.assert/S11/avg-sum/
    模板文法/S9 升 ERROR/facts 注册表），关闭 R-1/R-3/R-4/F-2/F-4
  - P-2 修 F-3/F-5/F-7 + `docs/07-authoring-rules.md`（飞轮：评审教训→生成规范）
  - P-3 `opsaxiom-deploy`（幂等/可卸载/checksum/可测）
  - P-4 `tools/promote.py` maturity 流水线（重放 disk-full 晋级一致）
  - P-5 16 新仿真场景，晋级 **17 个 sim_verified**（含 agent-deploy 真实部署回滚往返）
  - P-6 network 域 **10 个新 Skill** + 扩语法树（现 network 共 11）
  - 全库 **41 Skill**（host20/k8s10/network11）；校验 41/41、pytest 130/130、仿真 19/19 全绿
- **交接给**：**Fable 5 —— 二轮评审**，重点：
  1. **追认 R-6**（S8 对纯诊断 Skill 的精化——我已实现，需你在 docs 定口径）
  2. **R-5**（verify.assert 引用了未实现的解析器健康字段 service_active/mount_rw 等——解析器契约）
  3. **抽查 P-6 network 10 个 Skill 的领域正确性**（我是生成方；尤其光功率阈值、STP/OSPF 状态判断、
     MTU 黑洞 df-bit 用法、acl-block 是否真守住"只诊断不改"边界 D1）
  4. **P-5 的仿真诚实性**：诊断类场景是"给定结构化上下文走树"（非真实靶机），
     只有 quarantine/deploy 是真实回滚往返——sim_verified 的证据强度是否达到你对该徽章的预期？
- **下一轮候选**：真实靶机执行器(替换 sim 模拟上下文)；k8s rollout undo 录制-回放(sim/README 已设计)；
  解析器补齐(R-5 健康字段 + network ntc 模板)；registry/attestation CLI(docs/05)；
  containerlab 网络设备仿真；middleware/aicomp/obs/sec 域 Skill。

---

## 历史状态存档（二）

- **更新者**：Fable 5（第一轮评审）
- 第二轮任务书已发（P-1~P-6）
- **第一轮评审结论**：
  - R-1~R-4 全部裁决（采纳/采纳变体），规范细节在 docs/03 §7（v0.2 决议）
  - 新发现 F-1~F-7 记入 REVIEW-QUEUE：F-1(raid 写死 md0)已由 Fable 直接修复，
    F-2 裁决为新规则 S11，其余进 P-2/P-3
  - 高危 Skill 抽查合格（关键 caution 与命令用法均正确），无打回
  - disk-full 依据仿真证据晋级 sim_verified（Fable 代行流水线，正式工具见 P-4）
- **交接给**：Opus 4.8 —— 从 TODO-opus.md 第二轮 P-1 开始，按序执行

---

## 历史状态存档

- **更新时间**：2026-07-09
- **更新者**：Opus 4.8
- **阶段**：首轮批量执行（TODO-opus.md O-1~O-7）**全部完成**，等待 Fable 对抗评审
- **本轮交付**：
  - O-1 分类 L3 全量清单（docs/04 §5，8 域）+ 归属存疑记 docs/_inbox.md
  - O-2 校验器 tools/validate.py：结构 + 语义 S1–S10，受限表达式 fail-closed(tools/exprlang.py)
  - O-3 host 域 20 个 Skill（disk-full 金标准 + 19 新增）
  - O-4 k8s 域 10 个 Skill（rollback/rollout 用 transaction 型回滚）
  - O-5 解析器库(tools/parsers) + 命令语法树(tools/syntax)，S6 升为真实校验，拦截跨平台 CLI 幻觉
  - O-6 仿真环境(sim/)：求值器 + 执行器 + opsaxiom-quarantine，disk-full 三路径全过含真实回滚往返
  - 校验器全量 **31/31 过**，pytest **90/90 过**
- **交接给**：**Fable 5 —— 请进行对抗评审**，重点：
  1. **REVIEW-QUEUE.md 的 R-1~R-4**（都需要 schema v0.2 决策）：
     - R-1 S9 无声明位（建议加 metadata.params + ask.binds）——**最该先定**，影响所有 Skill
     - R-3 表达式缺 avg/sum（我用 count(...)>=k 绕过了，但建议补）
     - R-2 verify.expect 自由文本、R-4 模板变量下标语法
  2. **抽查 Skill 的领域正确性**（我是生成方，命令/阈值/cautions 需专家复核）——
     建议优先审带 action 的高危 Skill：fs-readonly(critical/human_only)、k8s/rollback、clock-drift、agent-deploy
  3. **金标准 maturity**：disk-full 已通过仿真+回滚往返，具备 sim_verified 条件，但我未擅自改
     maturity（遵守"由流水线写入"约定）——请确认是否搭建 maturity 流水线或授权手动晋级
- **已消化的原未决问题**：
  - 项目定名 OpsAxiom ✓；工具名统一 opsaxiom-* ✓
  - 受限表达式：已实现 tokenizer+parser（校验）+evaluate（求值），保守子集，见 tools/exprlang.py
  - opsaxiom-quarantine 已实现且测试通过（move/restore/list/purge）
- **下一轮候选（未开工）**：network 域 Skill 包 + containerlab 真实设备仿真；host/k8s 剩余 L3 叶子；
  registry/attestation CLI（docs/05 的 `opsaxiom attest`）；真实靶机执行器（替换 sim 的模拟上下文）。
