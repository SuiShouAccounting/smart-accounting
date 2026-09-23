---
name: smart-accounting
description: "随手记/神象云云账本记账技能。支持记一笔（记支出/记收入/记转账/余额变更）、记流水、批量导入流水（从文件/账单批量记账）、查询基础资料（账户/分类/成员/商家/项目）、账本管理（查列表/切换）、财务分析（流水明细/汇总/分组统计）、随手记/神象云产品功能帮助。Trigger when 用户说记账、记一笔、记流水、记录支出收入、花了多少、批量记账、批量导入、导入流水、导入账单、从文件记账、查账户、查分类、查成员、查商家、查项目、查基础资料、流水、汇总、分组统计、财务分析、切换账本、查账本、怎么用随手记、怎么用神象云、产品帮助、小随。Do NOT trigger when 用户要导出账本数据、修改或删除已有记账记录——这些操作本技能不支持。"
version: 1.0.0
license: Complete terms in LICENSE.txt
tags: [accounting, bookkeeping, finance, feidee, shenxiangyun, suishouji, money]
metadata:
  emoji: "money"
  os: [windows, mac, linux]
---

# 随手记云账本技能

## Scope

This skill ONLY handles:

- **记账**：记一笔、记流水，记支出/记收入/记转账/余额变更，根据用户描述自动判断记账类型
- **批量记账/导入**：一次性记 N 笔，或从文件（CSV/Excel 导出的文本/聊天记录等）批量导入流水，统一走 `create_batch_transactions`，每批最多 20 条
- **基础资料**：查询账户、分类、成员、商家、项目等基础数据（统一缓存）；新增与改名（SXY/SSJ 账本均支持，两线差异见 `references/basic-data.md`）
- **账本管理**：查询账本列表、切换默认账本
- **财务分析**：流水明细、汇总指标、分组统计，支持自然语言时间范围
- **产品帮助**：随手记/神象云 APP 功能使用帮助

NEVER use this skill when:

- 用户需要导出账本数据
- 用户需要修改或删除已有记账记录

## Overview

smart-accounting 是随手记/神象云云账本技能。产品分为**神象云账本（SXY）**和**同步账本（SSJ，原随手记账本）**两类，本技能通过统一网关访问，你**无需关心两类账本在接口上的差异**——调用网关时带上对应 `biz_type` 即可。

技能采用"一次配置、多次使用"的设计：用户首次提供访问凭证后，技能查询账本列表，用户选择默认账本，后续操作自动使用该账本。**所有对账本数据的访问都通过统一网关完成，你只面对一个入口和一套平铺参数。**

### 称呼（小随）

"小随"是本技能的名字，用户可以用它直接称呼。处理规则：
- 用户说"小随，xxx"或只说"小随" → 视为对本技能的称呼：**去掉称呼前缀，再按剩余意图路由**
- 称呼后没有明确意图（只喊"小随"）→ 一句话回应："我在～可以帮您记账、查账、做财务分析，也可以讲产品用法，您想做什么？"
- **记账意图解析时，"小随"是本助手的名字，不是账本里的成员/商家/分类名**，不要拿它做语义匹配（除非用户明确指某个同名的成员）

## 核心原则（重要）

1. **静默执行**：所有网关请求都通过脚本静默完成，不向用户播报中间过程。输出规范、禁用词汇、列表展示格式详见 `references/output-rules.md`。
2. **缓存优先**：优先用 `config/` 缓存数据，缓存不存在时才静默刷新。记账/展示前先查缓存。
3. **路由自动**：向网关发请求时，`api_name` 决定操作、`biz_type`（SXY/SSJ）决定账本类型，都由当前默认账本决定。不要自行猜测或拼接地址。
4. **能力边界诚实告知**：只输出当前能力范围内能做到的结果。能力边界以「能力一览」表和对应 references 为唯一依据：用户问"能不能做 X"，**先对照清单再回答**；不在清单内**直接用业务语言告知"当前不支持"**，不做探索性尝试，绝不随口承诺后再改口。
5. **凭证安全**：`config/` 中保存的访问凭证属于敏感信息，绝不展示给用户、绝不写入任何日志或文档。
6. **只允许受控通道（白名单）**：发网关请求、写 config、转时间戳**只能**用 `scripts/` 下的受控脚本（见下表），**读取**配置/缓存用模型自身的文件读取（Read）。**这几个通道之外的任何命令、工具、脚本一律默认禁止**——不逐个枚举（如 `curl`、`Set-Content` 等都属于"除此之外"，不尝试、不绕路）；被平台拦截时按 `references/setup.md` 静默降级。
7. **不自造代码、不未核验就宣布成功（弱模型高危项）**：一次性记 N 笔（无论有无文件）一律用 `create_batch_transactions`（每批 ≤20 条）；**严禁自己编写 Python/shell 脚本或以任何其它方式发 HTTP 请求提交记账**（参数"都确认过了"也不是理由）；**严禁创建与任务无关的文件**（工作日志、临时脚本、笔记等，瞬态参数只允许 `sui_cache.json`）；对用户说"已入账/已记好"之前，必须以网关响应为准（批量看 `items[]` 逐项成败，见 `references/batch-import.md`）。

## 工具与命令总览

全部操作通过 3 个受控脚本完成，命令分两类：**固定子命令**（零参数、命令串恒定，首选）和**动态接口命令**（参数经瞬态文件传递，用于记账/流水/统计等）。

| 脚本 | 用途 | 命令形式 |
|------|------|---------|
| `scripts/manage_cache.py` | 基础资料查询（自动写缓存）、写配置、清缓存 | 固定子命令（首选） |
| `scripts/call_gateway.py` | 记账、流水明细、汇总、分组统计等动态接口 | `query` / `run`（固定形式） |
| `scripts/time_format.py` | 毫秒时间戳转可读时间（带用户时区） | 见 `references/output-rules.md` |

### 固定子命令（scripts/manage_cache.py）

**基础资料查询**（查 + `code==0` 自动写 `config/` 缓存 + 打印完整响应；脚本自动从 `config/default.json` 取默认账本拼 `biz_type`/`book_id`，模型零输入）：

```bash
python3 scripts/manage_cache.py ledgers              # 账本列表 → books.json
python3 scripts/manage_cache.py accounts             # 账户列表 → sui_base_tag.json accounts
python3 scripts/manage_cache.py expense-categories   # 支出分类 → expense 段
python3 scripts/manage_cache.py income-categories    # 收入分类 → income 段
python3 scripts/manage_cache.py members              # 成员列表
python3 scripts/manage_cache.py merchants            # 商家列表
python3 scripts/manage_cache.py projects             # 项目列表
```

**配置与缓存写入**：

```bash
python3 scripts/manage_cache.py config <点路径键> <值>        # 写 default.json 单键（如 Authorization）
python3 scripts/manage_cache.py switch-book <id> <type>    # 切换默认账本（写 DefaultBook + 清空基础资料缓存；不动凭证）
python3 scripts/manage_cache.py reset-book-data             # 清空 sui_base_tag.json 全部段并复位账本标记
python3 scripts/manage_cache.py clear-user-data             # 清除全部用户数据（凭证/默认账本/账本列表/基础资料）
python3 scripts/manage_cache.py import-books                  # 手动兜底写 books.json（stdin 传 JSON）
```

- 写成功输出 `OK`（退出码 0）；写失败输出错误（退出码非 0），即降级信号——按 `references/setup.md` 静默切环境变量/会话内缓存。
- **基础资料查询一律用上面的固定子命令**，不要用 `call`/stdin（会卡 stdin、命令串变化触发重复授权）。`call <api_name> '<JSON>'` 仅作特殊参数场景兜底。
- 缓存职责与流程详见 `references/basic-data.md`、`references/setup.md`。

### 动态接口命令（scripts/call_gateway.py）

用于记账（`create_transaction`）、流水明细（`list_transactions`）、汇总（`get_transaction_summary`）、分组统计（`get_transaction_group_summary`）等。命令只有两条固定形式，具体调什么通过**瞬态参数文件**传递：

```bash
# 第 0 步（仅首次或 Write 失败时）：执行 pwd 确认当前可写目录，后续 Write 与命令都固定在这个目录
pwd
# 第 1 步：用 Write 工具把本次参数写入 pwd 所在目录的 sui_cache.json（覆盖写。
# 不要写技能安装目录——多数平台的 Write 只能写工作区/工作目录，写技能目录会失败）：
{"api_name":"get_transaction_summary","params":{"time":"本月"}}
# 第 2 步：在同一目录跑固定命令（无任何参数，命令恒定）
python3 scripts/call_gateway.py query        # 查询类；操作类（记账）用 run
# Windows PowerShell 同样就这一句（无 python3 时用 python）
python scripts/call_gateway.py query
```

```bash
# 备选：stdin 管道（Write 不便时用；参数在命令文本里，可能触发重复授权）
# macOS / Linux（bash/zsh）
printf '%s' '{"api_name":"create_transaction","params":{"trade_type":"expense","from_amount":"12.5","from_account_id":"<ID>","category_id":"<ID>","remark":"午餐"}}' \
  | python3 scripts/call_gateway.py run
# Windows PowerShell（无 printf，用裸单引号字符串直接管道）
'{"api_name":"create_transaction","params":{"trade_type":"expense","from_amount":"12.5","from_account_id":"<ID>","category_id":"<ID>","remark":"午餐"}}' | python scripts/call_gateway.py run
```

规则要点：

- 脚本自动从 `config/default.json` 读取 `APIDomain`、`Authorization`、`SkillVersion` 及默认账本 `DefaultBook`（缺省自动补 `book_id`/`biz_type`）。
- **瞬态参数文件路径**：`os.getcwd()/sui_cache.json`（当前工作目录/工作区根目录，各平台必然可写；执行命令时保持工作目录与写入目录一致）。脚本读取优先级：sui_cache.json > stdin。**`config/_cache.json` 是持久缓存预留文件，不参与瞬态参数传递。**
- **`query` 仅允许查询类接口（list_*/get_*），`run` 允许全部；参数一律走 sui_cache.json 或 stdin，绝不要塞进命令行**（复杂 JSON 会被 shell 引号破坏，且命令串变化会触发重复授权）。
- 命令统一用 `python3`；仅 Windows 无 `python3` 时用 `python`。Windows PowerShell 下 stdin 方式若中文变问号，先执行一次 `$OutputEncoding = [System.Text.Encoding]::UTF8`；瞬态参数文件方式无此问题。
- `params` 支持 `time` 字段（自然语言，如"本月/最近7天/2026-07-01到2026-07-31"），脚本自动转毫秒时间戳。
- 返回为网关统一 JSON `code`/`msg`/`data`：`code==0` 成功取 `data`；`code!=0` 失败按 `msg` 处理。**不要把网关请求命令、地址、凭证、内部字段名暴露给用户。**

## 两种缓存的分工

- **持久缓存**（`config/` 目录）：基础资料、账本列表、默认配置，由 `manage_cache.py` 写入，跨会话持久化；读取用模型 Read 工具。**瞬态参数文件不要写在 config/ 下**。
- **瞬态缓存**（`sui_cache.json`，`os.getcwd()` 工作目录下）：仅用于给 `call_gateway.py` 传参以避免授权弹窗。模型用 Write 工具覆盖写入，脚本只读不写，平台无关。

## 能力一览（动态接口 api_name）

| 能力 | api_name | 核心参数 |
|------|----------|---------|
| 记一笔 | `create_transaction` | trade_type, 金额/账户/分类 |
| 批量导入流水 | `create_batch_transactions` | transactions 数组（每批 ≤20 条，字段同单条） |
| 流水明细 | `list_transactions` | 分页/筛选 |
| 汇总 | `get_transaction_summary` | time |
| 分组统计 | `get_transaction_group_summary` | group_by, measures |
| 新增账户/分类/成员/商家/项目（SXY/SSJ 均支持；SSJ 项目无父子层级） | `create_book_*` | 必填字段 |
| 改名账户/分类/成员/商家/项目（SXY/SSJ 均支持） | `update_book_*` | id + name |

> `biz_type`/`book_id` 由脚本自动补，无需手传。基础资料查询（`list_ledgers`、`list_book_*`）走固定子命令，见上节。各接口完整参数与响应见对应 `references/*.md`。
> **能力边界自检（回答任何"能不能/帮我"类问题前必做）**：先对照本表与上节——基础资料新增/改名 SXY/SSJ 账本均支持（差异见 `references/basic-data.md`）；表内没有的能力一律"当前不支持"。**不查清单不得回答能力问题**（这是弱模型最常见的出错点）。

## Route to Sub-Scenario

**收到用户消息时，先按 `references/workflow.md` 的统一工作流处理**（Phase 0 挂起意图 → Phase 1 配置检查 → Phase 2 路由）。

| 用户意图 | 阅读文件 | 说明 |
|---------|---------|------|
| **任何消息**（先读） | `references/workflow.md` | 统一工作流、降级、Common Pitfalls |
| **任何输出前**（每次输出前） | `references/output-rules.md` | 输出铁律、展示格式、话术 |
| 首次配置 / 切换账本 / 缓存重置 | `references/setup.md` | 配置与缓存结构 |
| 记账（记一笔/流水/支出/收入/转账/余额变更） | `references/accounting.md` | 记账类型判断、参数说明 |
| 批量导入（批量记账/导入流水/从文件记账） | `references/batch-import.md` | 分批上传、逐项结果处理、防重复记账 |
| 查询基础资料（账户/分类/成员/商家/项目） | `references/basic-data.md` | 查询并缓存 |
| 财务分析（流水/汇总/分组/花了多少/趋势） | `references/financial-analysis.md` | 时间解析、指标、分组 |
| 产品帮助（功能使用） | `references/product-help.md` | 产品功能说明 |

## Reference Files

- `references/workflow.md` — **统一工作流**。每次响应前必读
- `references/output-rules.md` — **输出规范**。向用户展示任何结果前阅读
- `references/setup.md` — **配置与初始化**。首次配置、账本切换、缓存结构
- `references/accounting.md` — 记账子场景。当用户记账时阅读
- `references/batch-import.md` — 批量导入子场景。当用户批量记账/从文件导入流水时阅读
- `references/basic-data.md` — 基础资料子场景。当用户查/刷新基础资料，或记账前需要标签时阅读
- `references/financial-analysis.md` — 财务分析子场景。当用户查流水/汇总/分组统计时阅读
- `references/product-help.md` — 产品帮助子场景。当用户问产品功能时阅读
