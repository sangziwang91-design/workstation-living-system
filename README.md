# Workstation Living System（WLS）
### 一个能够长期存在、主动行动、积累经验，并持续改进自身能力的多功能自主智能体

> **项目使命（长期目标，不是完成声明）**
>
> WLS 要成为一个由用户拥有、可独立安装、可长期运行的个人自主行动体。它能够在明确权限和资源预算内，持续**感知环境 → 维持记忆 → 选择目标 → 规划与行动 → 观察真实结果 → 学习与修正 → 改进自身工具和工作方式 → 持久化并继续下一轮**。
>
> **最终成果不是一个会修复代码的脚本，也不是一套非常完善的测试、审批和证据仪表盘。** 编程与自修复是 WLS 的一种能力，也是早期可验证的成长场景；WLS 的目标始终是**多功能、长期连续、自主改进**。即使底层模型参数不变，WLS 也应通过记忆、策略、技能、执行器及改进机制的可验证升级，逐渐完成过去做不到的事情。

**Status / 事实边界（截至 2026-10-08）**：这个 README 固定**方向与续接契约**；**不宣称已经实现无人值守、开放任务的 RSI、通用 AGI、意识或完整自主软件生命**。当前实现状态以正在读取的分支源码、当前提交 CI、运行时证据为准。后续编辑者必须先完成下文的“冷启动续接”，不能凭本 README 的愿景推断功能已经交付。

## 1. WLS 到底是什么，不是什么

**一句话产品定义**：**WLS 是用户拥有的、单一持久主体的本地优先（local-first）长期自主智能体运行时；它组织可替换的模型和工具，在用户给定的边界内持续完成跨领域任务，并通过真实环境反馈改善自身。**

它要解决的具体问题：

- 普通聊天模型随会话切换而丢失任务连续性；WLS 需要在聊天窗口、模型、机器进程和服务商变化后，仍能从持久化状态恢复**同一个任务与目标**。
- 传统 Agent 可以完成一次任务，却常常没有长期主动性；WLS 需要能够在授权范围内感知变化、维护优先级、发起有价值的下一步，并在资源不足或没有收益时安全等待。
- 经验可能只沉淀为日志；WLS 要让过去的成功和失败**实际影响**未来的选题、判断、工具调用、执行成功率和改进成本。
- 智能体往往依赖单一外部模型；WLS 应保有自己的身份、记忆、任务状态和证据，模型是可替换的能力来源，不是唯一持续主体。
- “自进化”经常退化为一次修复或百轮模拟；WLS 要追求**数周至数月**稳定、低成本、可继承、经独立验证的能力提升，并逐步改善**产生下一次提升的机制本身**。

**不是**：第二个通用聊天客户端、代码编辑器、项目管理软件、版本计数器、无限测试工厂、商业化监控平台、模型训练公司的替代品。它可以有 UI、报告和 Coding Agent 接口，但它们都是支撑长期行动的部件。**不以“软件有意识/情感/生命”为工程验收条件。**

### 长期体验：未来一个正常工作周期应该是什么样

1. WLS 从持久状态醒来，知道正在做的任务、上次中断位置、用户授权、预算、环境变化以及过往失败。
2. 在已授权的来源中感知新信息，将噪声与相关变化分开；按可验证收益、风险、紧急性和目标持续性决定**做什么或不做什么**。
3. 选择适当模型、记忆、工具与已有技能；先预测可能的结果，再执行低风险、已授权动作；高风险或不可逆动作进入明确的用户审批。
4. 把实际工具结果、外部效果、失败与代价和行动前预测对齐。**模型声称成功不是实际成功。**
5. 更新世界状态、目标进度、对自身能力的认识和可迁移经验；只有能证明有效的变化才可继承，退化时回滚。
6. 在长期预算内继续下一轮；遇到无收益、模型不可用、额度耗尽或外部环境出错时等待、降级、报警或安全休眠，而非刷循环次数。

这是**目标行为描述**，不是当前所有步骤都已实现的保证。

## 2. 不可替换的系统身份与架构边界

```text
Single durable authority: LivingSystem
  ├─ Perception / sensors: 允许的数据源与真实事件
  ├─ World + memory:     来源、时间、事实、假设、因果与过期机制
  ├─ Goals + attention:  长期目标、优先级、资源预算与自主触发
  ├─ Planner + models:   可替换的推理模型及任务规划
  ├─ Tools + workers:    文件、仓库、浏览器、桌面、MCP、Coding Agent 等受控器官
  ├─ Actions + receipts: 权限、审批、执行、可观察结果、幂等与恢复
  ├─ Learning + skills:  从结果和反证中修正、验证、保留、撤销
  ├─ Evolution / RSI:    改进候选、独立评价、代际继承、改进器对照
  └─ Survival:           SQLite、调度、lease、崩溃恢复、睡眠、预算与安全停止
```

**只有一个 WLS 持久主体**。任何外部模型、Codex、Claude、Qwen、OpenCode、AgentBridge、Notion、GitHub Actions 或 UI 都是**工具、worker、数据源或投影**；不能偷偷建立第二套持久 Brain、Goal、Policy、Memory、Evidence 或 Planner 权威。新增能力必须接入既有 `LivingSystem`、已有状态机与证据路径，不能用 `final/v2/new-core` 重新创建项目。

必须分清四个层次：

| 层 | 解决什么问题 | 不能拿什么冒充 |
|---|---|---|
| **Model** | 推理、预测、生成候选 | 换强模型 ≠ WLS 自身已经学会 |
| **Harness** | 工具、环境、权限、状态、持续性与可观察性 | 工具接口存在 ≠ 实际工作链成立 |
| **Loop** | 触发、执行、外部反馈、独立检验、停止和下一次选择 | 循环100次 ≠ 改进100代 |
| **Graph** | 任务与因果依赖、跨步恢复、协作和证据关系 | 节点图丰富 ≠ 持续自主行动 |

**Runtime authority**：`source/src/wls/runtime.py::LivingSystem`。模型不应控制验证器、密钥、不可逆审批或最终事实；候选代码必须在隔离环境内执行。

## 3. 核心生命循环及七项持续能力

**主循环**：

```text
sense → remember → judge → set/maintain goal → plan → act (under policy)
      → observe external outcome → learn → self-model
      → improve tools/skills/strategy when justified → consolidate/sleep → resume
```

这里的“主动”意味着：用户可以授权目标域、周期、预算和工具范围，之后 WLS 在范围内自行选择低风险下一步，**不是每一步都要用户继续发提示词**；但用户随时可暂停、撤销授权、冻结或回滚。当前代码的具体审批能力未验证前，不得声称已经具备这一体验。

**七项必须逐步变成真实行为的能力**：

1. **持续身份**：同一运行时、持久目标和来源明确的记忆，换会话/模型/进程后可恢复。
2. **生存与调度**：长时间事件循环、有限注意力、预算、心跳、崩溃/断网恢复和安全休眠。
3. **主动感知与目标选择**：只在授权数据源与目标域内感知；维护多个任务，能筛选值得行动的新机会，不强迫每轮有动作。
4. **多功能行动**：能选择受控文件、代码、网页、桌面、信息检索及未来新工具，跨任务复用执行能力，而非只修单一 Python 漏洞。
5. **世界模型与因果记忆**：事实与推断分离；在行动前留预测，在行动后收真实结果，修正对环境、工具及自身能力的判断。
6. **可保留的学习与技能增长**：失败不能仅写入日志；应形成可重用、更有效的技能、策略或模型适配，并能在旧任务上防回退。
7. **长期自我改进（含 bounded RSI）**：自主寻找缺口、提出候选、独立验证、有限范围晋升、继承成果；进一步检验**改进器是否真的比冻结版更会发现和实现改进**。

**优先级原则**：七项共同构成目标。不能因为第7项近期容易形成 GitHub 测试，就让 WLS 永远缩窄为代码修复器；也不能为追求多功能重新堆积几十个未接入生命循环的模块。

## 4. RSI 在 WLS 中的准确含义

**RSI = Recursive Self-Improvement（递归自我改进）**，在这里是**长期行动体的一种成长机制和研究目标**，不是仓库成熟度标签，也不是一次补丁的名字。

严格分为四级，不许混称：

| 层次 | 可接受证据 | 必须避免的误称 |
|---|---|---|
| **A. 自动化执行** | 调度、恢复、CI、委派、回滚可以连续完成 | “自动跑起来”≠ 学习 |
| **B. 有限自修改** | 模型确实写出源码/技能候选，独立测试确认改善，改动得到保存 | 一次修复 ≠ RSI |
| **C. 持续累积改善** | 多次真实任务中由系统主动选择改进方向；继承有效能力；学习开/关对照显示长期优势；成本与回归受控 | 百代脚本或同一道测试的重复通过 ≠ 成长 |
| **D. 改进器自身改善** | 在等模型、等任务、等算力和成本的对照下，可进化改进器持续优于冻结改进器，且泛化到未见任务 | 多修几个漏洞 ≠ 递归能力增长 |

目标是先把 **B → C** 变成稳定、低维护成本的真实过程，再以严格实验研究 **D**。没有足够数据时可以准确称为“持续自动化自修复 / bounded agent improvement”，**不能称已经实现开放式 RSI**。不要求每次都有正收益；`NO_GAIN`、安全等待、回滚和失败保留是正确行为，**只有可复核的净能力增量才进入成长账本**。

**每轮真实改进必须形成可验证轨迹**：

```text
why this gap? (evidence, independent of solution)
 → frozen baseline and budget
 → actual model/worker proposal + immutable source
 → isolated execution and independent acceptance
 → withheld-task / old-task regression + real cost
 → candidate-only receipt
 → approved promotion within permitted scope
 → subsequent real-task reuse + measure retained advantage
 → retain / rollback / NO_GAIN
 → next gap chosen from observed environment and accumulated outcomes
```

必须留下 `state_before`、`prediction_before_action`、`action_digest`、`state_after`、实际耗时/费用、测试结果、负例与停止原因；有效改进不得通过事后修改评价标准来“制造”。对 RSI 的实验应同时测量**单位成本下解决的新任务数、独立完成率、人工介入次数、失败回归、恢复成功率和下一轮改进效率**。连续时间与未见任务的对照，比漂亮的版本号更重要。

## 5. 当前到底做到了哪一步？（时间快照，不是永恒事实）

**日期：2026-10-08。后续编辑者必须检查当前 GitHub `main`、当前 PR HEAD、Actions 结论以及真实运行态；本段可过期，不得把历史快照当实时事实。**

- `main` 以现有 `LivingSystem` 为权威，具备持久 SQLite/证据、受控目标/动作、认知与记忆、技能成长、Patch Mission 等源码与测试基础。具体部署和用户机体验**必须独立验收**。
- `EVOLUTION-TARGET-001/002/003` 有已合并、CI 验证的工程路径；`ET004`、P01–P89 和旧本地运行声明要区分来源、分支和验证环境。更多状态参考 `CURRENT_STATE.yaml`，但不能不经核对就接受其中旧日期的“完成”。
- [WLS RSI · PR #36](https://github.com/sangziwang91-design/workstation-living-system-private/pull/36) 是原仓库正在推进的 RSI 功能分支（截至本快照为 **Draft、未合并**）。有 GitHub 托管环境中真实小型 Qwen Coder 模型参与的**有限目标**源码补丁、独立回归、持久候选和代际证据完整性检查；其性质是 **B 的有界实例，不是 C/D 达成**。有的“100代”是脚本化压力测试，“31代”是谱系完整性验证，均不能当作自主学习代数。
- 已验证样本：`9bcef5820324e01a844f59d73bc5abffbf1246a4` 的 [完整托管 CI](https://github.com/sangziwang91-design/workstation-living-system-private/actions/runs/37760916416) 和 [Linux/Windows RSI CI](https://github.com/sangziwang91-design/workstation-living-system-private/actions/runs/37760916528)。**更晚的分支提交需要重新核验，不能继承旧 PASS。**
- [AgentBridge / `open`](https://github.com/sangziwang91-design/open) 的 [PR #3](https://github.com/sangziwang91-design/open/pull/3) 已合并；证明过协议往返、受控命令与回执校验的 GitHub CI，不等于所有真实桌面/模型环境已完整联通。
- **仍未证明**：能数周持续无人值守地自主选择多领域任务、在用户机长期运行、跨问题实现可归因的净学习优势、改进器进化优于冻结对照、任意模型兼容、任意未知模型生成代码的安全执行。

**无新证据不得把 `main`、候选 PR、Actions 沙盒、安装包、用户电脑或 Notion 的“实现状态”混写在一起。** 原始长期目标见 [Notion EXP-082](https://app.notion.com/p/38940ff6ad6281b6bd69d700c9322d77)。其“工程连续性与证据层”的产品切分是当时的**阶段策略 / 可交付能力范围**，**不替代本 README 的长期多功能主体愿景**。

## 6. 这套系统应该怎样继续长大？

**沿已有单一 `LivingSystem` 主线做端到端纵向增长，不建平行系统。** 合理的依赖顺序是：

1. **让一个真实任务可在无人继续提示的情况下持续**：恢复目标 → 获取许可 → 任务选择 → 受控执行 → 真实反馈 → 再选择；最先用当前 Patch Mission 作为可测场景，但不能止步于编程。
2. **把“长期”做成可观测事实**：GitHub 云端多时段任务与崩溃/取消/重试、跨日复用、成本记录；最终在用户 Windows 主机完成真实持续运行验收。不能把模拟100次称为运行100天。
3. **拓宽任务面，不复制大厂基础组件**：复用 OpenCode/Codex/浏览器/系统工具/MCP 等现成 worker。新增一个器官时要证明它在实际生命循环里完成一个此前做不到的任务。
4. **让真实结果进入因果学习**：记录行动前预测、行动后结果、失败类型、成本及记忆影响；用“学习开启 vs 冻结”和不同环境任务检验能力增长。
5. **使能力增长能够持续、自动、节约资源**：模型自主识别缺口、选择下一次改进、提交真实候选、独立评价、继承、回退、换领域复用。控制重复实验；没有增益时停止本次尝试，不制造假增长。
6. **最后检验递归性**：让可演化改进器与固定改进器在相同预算、相同外部智能的条件下比较，研究它是否获得持续优势。不要以这种研究门槛阻断前面已能产生的实用价值。

这是一条**能力依赖路线，不是机械的版本号清单**。如果某步骤已经由代码和当期 CI 证明，直接越过它；如果它没有改善用户可用能力，不要为了填满步骤而实施。

### 停止与收缩准则

每个候选迭代都回答四个问题：**它消除了什么真实瓶颈？它让下一轮能完成什么原先不能完成的事情？凭什么证据知道这是真的？为了获得增量花了多少用户时间、API/Actions预算和维护成本？**

只增加文件、标题、标签、总模块数、模拟轮次、PASS 数或演示图，而不改变任务成功、连续性、主动性、学习或恢复能力的变更，应拒绝或推迟。优先删除冗余；不得通过不断发明新检查来代替改善 WLS。

## 7. 执行与成本：GitHub-first，用户电脑最后

**默认所有可以在 GitHub 完成的工作就在 GitHub 完成**。用户不应该因为一次普通单元测试、代码审查、静态扫描或模型源码实验就被要求下载一堆半成品到 Windows 本机：

| 环境 | 应执行 | 不应把什么推给用户 |
|---|---|---|
| ChatGPT / 代码 worker / 当前会话沙盒 | 设计、代码审查、提出补丁、审计已有结果、必要的轻量测试 | 提交不了代码就要求用户代为复制几十个文件 |
| GitHub 仓库和托管 Actions | Linux/Windows CI、回归、安全扫描、跨仓库测试、构建、有限时长任务与对照实验 | 用本地人工测试替代免费的/已授权的云端验证 |
| 隔离容器/VM（可托管） | 不可信模型代码、封闭评测、最小权限的候选试验 | 在拥有私有密钥的普通 runner 上直接执行未知代码 |
| 用户 Windows 主机 | **最终**真实安装、桌面交互、设备依赖、长期生活运行和真实使用验收 | 早期重复基础构建、模拟迭代、临时实验 |

**注意**：聊天 App 的免费/订阅对话入口**不是**可以擅自无人值守调用的 API；真正无人值守模型调用需要合法可用的执行接口/本地模型与明确的费用预算。WLS 的连续调度由 runtime/worker/CI 承担，不能虚构“聊天窗口后台永远思考”。

资源策略：廉价模型处理明确、有限任务；强模型在必要时处理深度架构、未知问题或复核；缓存已证实的结果，`NO_GAIN` 不应再消耗模型推理；先检查是否值得运行新实验。计算费与**用户注意力**同为预算。

## 8. 权限、证据与可纠正性不是产品本身，但绝不能丢

不可绕过的底线（详见 [`LIVING_SYSTEM_GENOME.md`](LIVING_SYSTEM_GENOME.md)）：

- 默认安全权限、来源追踪、定额资源、明确定义的授权范围；高风险、不可逆、对外发布、永久删除、生产部署或改变核心信任边界必须取得相应用户授权。允许**授权范围内**低风险可恢复操作自主发生，不能把所有微小动作都推回给用户。
- 模型输出是候选，不是结果。检查源文件、真实测试退出码、执行回执、环境与提交哈希；保护评测器、保留独立 holdout、记录失败与非增益。
- 暂停、紧急停止、恢复、回滚、密钥隔离、候选只读隔离、未知副作用的不自动重试必须成立。失败可以不成功，但不能悄悄篡改状态或伪造成功。
- 权限/风险/evidence/审计是**免疫系统**。保护实际行动链时很重要；不应因自身容易扩张而变成项目的最终主要工作。
- 所有“已经实现”的声明必须带环境、commit、命令或 CI URL；`fixture/shadow/preflight/CI-only/owner-report` 各自保留原标签，绝不自动升级为生产或长期能力证据。

## 9. 唯一代码权威及能直接执行的入口

```text
repository:          sangziwang91-design/workstation-living-system-private
default branch:      main
package root:        source/src/wls
canonical runtime:   source/src/wls/runtime.py::LivingSystem
version authority:   source/src/wls/_version.py::__version__
build metadata:      pyproject.toml                 (root only)
main state snapshot: CURRENT_STATE.yaml             (snapshot; may lag live HEAD)
constitution:        LIVING_SYSTEM_GENOME.md         (security / identity constraints)
short handoff:       docs/WLS_MAINLINE_CONTEXT.md   (historical implementation context)
continuity notes:    docs/LIFE_LOOP_RESET_HANDOFF.md
candidate interface: .evolution/                  (check current files / validators)
tests:               source/tests
scripts:             source/scripts
```

**权威顺序**：当前可运行代码与真实结果 → GitHub 当前 commit、PR/Actions 及原始证据 → 经校验的机器状态文件 → Genome/本 README 的约束与愿景 → Notion 长期语义与归档 → 旧聊天记忆。**理念上本 README 规定最终目的；实现事实由实时证据决定。** Notion 不得覆盖 GitHub 运行真相，GitHub 的临时可交付产品切分不得悄然覆盖用户长期目标。

验证与入口示例（**请在 GitHub Actions 中优先运行，不要求用户先安装**）：

```bash
python -m pip install -e ".[dev]"
python source/scripts/verify_packaging_layout.py
python -m pytest -q source/tests
python source/scripts/verify_evolution_target_001.py
python source/scripts/verify_evolution_target_002.py
python source/scripts/verify_evolution_target_003.py
python -m build .
```

已有本地 WLS CLI 用法（用于最终实例验收，不构成安装成功声明）：

```bash
wls init --home /path/to/wls-home
wls --config /path/to/wls-home/config.json self-check
wls --config /path/to/wls-home/config.json once
wls --config /path/to/wls-home/config.json life-state
wls --config CONFIG patch-mission PATH_TO_REPO "Fix the failing test"
wls --config CONFIG patch-mission-step --mode resume-next
```

完整 CLI、审批、Growth 与 Patch Mission 细节参见现有文档与实际 `--help`；不得因 README 举了命令就推断所有 worker、外部操作或 UI 已在安装版可用。

## 10. 新对话 / 新开发者 / 新模型的强制冷启动续接

**这是最重要的交接规则。任何人或模型接手时先做此事；不要从聊天摘要、过期PR说明或新架构设想开始。**

1. **读本 README**：确认“长期多功能自主行动体 + 长期自我改进”的终极目标，不把阶段性工程连续性产品切分当作终点。
2. **读 `LIVING_SYSTEM_GENOME.md` 与 `CURRENT_STATE.yaml`**：取得不变量、权限和历史状态；记录快照更新时间，**不能直接相信旧完成标签**。
3. **向 GitHub 实时查询** `main` 最新 SHA、开放 PR（尤其 [#36](https://github.com/sangziwang91-design/workstation-living-system-private/pull/36)）、当前正在推进的唯一合法候选分支、最新 HEAD 的 Actions 结果、是否有人并行修改。**绝不把旧 CI 的 PASS 贴到新 HEAD。**
4. **看真实代码与故障证据**：从 `source/src/wls/runtime.py` 出发追到正在运行的循环；确认实际可用 CLI、持久状态、worker、工具、来源证据和中断恢复。遇到文档冲突时以代码和真实执行证据为准，并指出冲突。
5. **继承已有工作而非重建**：检查未完成任务、前代候选/评测器哈希、冲突分支、之前的失败和 `NO_GAIN`。优先在现有分支修复；新分支必须有明确隔离理由；完成后合并/关闭多余支线并标注证据。
6. **只选择一个有现实增量的阻塞点**：首选“让真实生命循环或长期自主成长多走一步”的问题，而不是再次增加 dashboard、抽象门、100次模拟或不必要的环境设置。
7. **闭环交付**：直接在 GitHub 实施 → 托管 Linux/Windows CI → 读失败日志 → 修正 → 重跑**精确 HEAD** → 保留失败证据与真实成功声明；在用户主机不可用时不伪造本机验收。
8. **留下可续接记录**：在当前 PR / issue / 既有状态文件里更新“做了什么、基线、commit、验证、还缺什么、下一步命令”，随后再根据需要同步 Notion 的解释；**不要创建第二份永远无法同步的 CURRENT_* 文件**。

**每次结束必须留下这张最小交接表（放在 PR 描述或评论中，不要不断改写 README）：**

| 字段 | 必填内容 |
|---|---|
| Long-term objective | WLS 多功能、长期自治、可测的累积改进；此次任务如何服务它 |
| Canonical base / HEAD | `main` SHA、工作分支 HEAD、PR URL；是否有并行编辑 |
| Verified gain | 旧行为/指标 → 新行为/指标；同等条件、真实证据或 `NO_GAIN` |
| Exact evidence | 失败与成功的 Actions 链接、测试命令/退出码、独立验证、运行环境 |
| Authority / risk | 权限、预算、用户审批或其缺口；候选与已合并/已部署状态 |
| Known failures | 尚未排除的失败、回归、模型输出不可靠处；不粉饰 |
| One next action | 唯一真正阻塞能力的下一步、可执行入口与停止条件 |

**反漂移规则**：后来模型可以提出更好的路线并用证据挑战这里的设计，但**不能仅凭自己的重述将 WLS 重新定义为纯 Coding Agent、纯证据层、纯治理系统或一个虚拟人格**。需要改变最终使命，必须有用户明确的新决定；需要改变工程身份，必须有可信迁移与测试。

## 11. 完成度的真实判定

下面每一级都必须以**独立现实证据**升级，而不是以文件和版本数升级：

- **Foundation（基础存在）**：可复现安装、单一状态权威、清晰的权限、安全恢复和一个完成的行动链。
- **Autonomous operation（持续行动）**：在授权下跨重启/跨日自行挑选和推进真实任务，有限成本且可停止，用户不必成为测试执行工。
- **Multifunctional agent（多功能主体）**：至少两个不同领域的真实任务能共享相同身份、记忆、目标和工具治理，学习可以迁移。
- **Accumulating improvement（慢性成长）**：经过数周多次任务验证，在成本相当时新版本稳定优于冻结版，包含失败样本与旧任务回归。
- **Recursive improver（递归改进研究）**：改进策略本身经过可归因更新，并在固定/可演化改进器对照、未见任务、等额预算下证明持续优势。

**到此为止的方向要求**：WLS 首先要成为一个长期存在、能够真正做事并逐渐做得更好的自主智能体。RSI 是这个目标中最难、最值得测量的增长部分，但不是替代整个 WLS 的新项目。

---

### English handoff for AI/code contributors

**Mission:** one persistent, owner-controlled, local-first **multifunctional autonomous agent**, with durable goals, perception, action, causal outcome learning, recoverability, and **longitudinal, independently evaluated improvement** of skills, tools, strategies and eventually the improvement process itself. **RSI is a long-horizon capability goal, not a passed checkbox**. A bounded source fix, a 100-generation scripted harness or a successful CI run does not prove recursive intelligence growth.

**Start here:** read this README, `LIVING_SYSTEM_GENOME.md`, `CURRENT_STATE.yaml`; inspect the actual `main` HEAD, active PRs and exact-head Actions evidence; follow `LivingSystem` without creating a parallel brain/runtime. Use GitHub-hosted CI by default; do not send basic tests to the owner's Windows machine. Keep external workers replaceable, canonical state singular, policy/approvals intact, and claimed status strictly evidence-bounded. Optimize for measured long-term agent capability per user-time and compute cost, not documentation or test volume.

**Continuation contract:** every edit must leave a verifiable PR/commit/CI trail, describe the measured capability delta, distinguish source vs candidate vs installed runtime, and name the next executable step without rebuilding the project.
