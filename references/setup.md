# 配置与初始化

## 何时读这个文件

- 首次使用时（Phase 1 配置检查发现 `default.json` 不完整）
- 用户主动切换账本时
- 缓存数据与当前账本不匹配，需要重置时
- 排查配置问题时

## 配置文件总览

所有配置保存在 `config/` 目录下，**对用户只问一次**。

### `config/default.json` — 核心配置

| 字段 | 说明 |
|------|------|
| `Authorization` | 访问凭证（`Bearer ...`），用户首次提供。**属于敏感凭证，请勿分享此配置文件给他人，也绝不展示给用户** |
| `APIDomain` | 网关入口地址。发布部署时由维护者配置为正式地址 |
| `SkillVersion` | 技能版本号，随每次请求发给网关 |
| `DefaultBook.id` | 默认账本 ID |
| `DefaultBook.type` | 默认账本类型（`SXY` 或 `SSJ`），决定 `biz_type` |

### `config/books.json` — 账本列表缓存

由 `list_ledgers` 网关调用后写入（模型读取返回的 `data` 并保存此处），用户不感知。

**结构定义（单一权威 schema）**：

```json
{
  "books": [
    { "book_id": "账本ID", "name": "账本名", "type": "SXY|SSJ" }
  ]
}
```

> **字段映射**：网关 `list_ledgers` 返回的 `data` 每条含 `book_id` / `name` / `biz_type` / `last_use_time`（最近使用时间，毫秒时间戳，**可能为 null**；最近使用的账本排在前面）。写入 `books.json` 时**直接沿用** `book_id` / `name`，仅把 `biz_type` 记为 `type`；**`last_use_time` 不入缓存**（缓存 schema 不变），它只用于列表展示的（最近使用）标注，取数来源见「账本列表展示规则」。
> **保持幂等**：每次 `list_ledgers` 调用成功后**整体覆写** `books` 数组（以最新返回为准），不要追加或去重。

### `config/sui_base_tag.json` — 基础资料缓存

**结构定义（全扁平，单一权威 schema）**：

```json
{
  "book_id": "string  // 缓存数据归属的账本 ID",
  "source_type": "SXY | SSJ  // 缓存数据归属的账本类型",
  "expense":    [ /* 支出分类 */ ],
  "income":     [ /* 收入分类 */ ],
  "accounts":   [ /* 账户 */ ],
  "members":    [ /* 成员 */ ],
  "merchants":  [ /* 商家 */ ],
  "projects":   [ /* 项目 */ ]
}
```

各类型共有字段：`id` / `name` / `act_enable`（true=可记账）/ `hidden`；其余字段按类型不同（分类/账户有 `parent_id`/`parent_name`，账户另有 `type`/`currency`，项目有 `parent_id`/`parent_name` 但 SSJ 常为 null；成员/商家无 parent_*/type）——不要假设所有条目字段齐全，缺失按 null 处理。

各资料的 `act_enable` 规则：分类（一级 false，二级 true）、账户（叶子节点 true）、成员/商家（扁平一级，全 true）、项目（**SXY 有父子层级：叶子 true、带子项目的顶层 false；SSJ 扁平一级，全 true**）。

> 重置缓存：将各数组清空、`book_id`/`source_type` 置空即可（保持键存在）。

## 账本切换

当用户要切换默认账本时：

1. **静默调 `scripts/manage_cache.py ledgers`（查 + 自动写 `config/books.json`），展示用这次查询的最新结果**——账本可能新增/删除，且（最近使用）标注依赖响应里的 `last_use_time`（缓存里没有），**不要从 `config/books.json` 直接读来展示**（缓存只用于 switch-book 时查 book_id/type）；config 不可写则走会话内
2. 用户选择后，调 `manage_cache.py switch-book <book_id> <type>` 一步完成：写 `config/default.json` 的 `DefaultBook.id/type`（type 从选中账本的 type 字段取）+ 清空 `config/sui_base_tag.json`（不同账本基础资料不同，避免串账本）

> **账本列表展示规则（首次配置与之后看账本都适用）**：展示一律用**最新一次 `ledgers` 查询结果**，按 output-rules.md「账本列表」节渲染——3 列表格（序号/账本名称/账本类型：SXY=神象云账本、SSJ=同步账本），10 个一组；（最近使用）标注：未选账本时只标第 1 行（其 last_use_time 非空时），已选账本时只标所选账本行。

## 首次配置流程

> **写入 config/ 静默进行（不向用户做权限预告）**：skill 需要把 token、账本列表、基础资料写到本地 `config/` 目录，而不同平台对该目录的可写性不同（有的可写、有的需授权、有的硬性拒绝）。**首次配置时不向用户做任何权限预告或授权说明，直接引导用户提供凭证，写 `config/` 的动作静默完成**。
>
> **分平台策略**：
> - 平台可写 `config/` → 正常落盘，无需额外操作
> - 平台弹窗需授权 → 引导用户点"允许/信任此文件夹"，通常一次即可
> - 平台硬性拒绝且无法授权 → 走**降级**：token 用环境变量/会话内提供，缓存退化为"会话内可用、跨会话重新拉取"，不因写不了文件而拒绝服务（静默降级，用户无感知）

> **写 config 被拦截时的硬规则（重要）**：
> - 写 `config/` 一旦被平台拦截/报权限错，**立即静默切换到降级**：凭证发环境变量 `SUI_AUTHORIZATION`、缓存退化为会话内维护，**继续用脚本正常调用，不停下来问用户**（降级静默进行，用户无感知）。
> - 写入 config 的唯一受控通道是 **`scripts/manage_cache.py`**；除此之外不尝试任何其它命令/工具（包括但不限于 `Set-Content`/`Out-File` 等），被拦截说明当前路径不可写，直接降级，**不绕路、不硬试**（脚本写失败会返回非 0 + 错误信息，即降级信号）。
> - **禁止**把完整凭证（如 `Bearer SK-...`）、环境变量赋值命令、config 路径写进任何日志、文档或向用户回显。
> - 降级后 `call_gateway.py` 已支持：config 不可读时自动返回空配置 + 凭证从环境变量 `SUI_AUTHORIZATION` 获取；`manage_cache.py` 写失败时返回非 0 退出码作为降级信号。**所有网关请求仍走受控脚本（操作型 `call_gateway.py`、基础资料查询 `manage_cache.py call`），不走 curl 或任何其它命令**。业务照常进行，用户无感知。

首次使用时由统一工作流的 Phase 1 自动引导，按以下顺序逐项处理：

> **重要**：基础资料/账本缓存（`books.json`、`sui_base_tag.json`）主流程由 `scripts/manage_cache.py` 固定子命令查询时自动写入，无需模型手动写；单键配置写 `default.json` 用 `manage_cache.py` 的 `config`。切换账本用 `switch-book` 一步完成；`import-books`/`reset-book-data` 保留为**手动/降级兜底**。命令只是内部动作，**不展示给用户**。
> 模型必须在向用户输出"已配置"/"已切换"之前，**真实完成配置持久化**（写入 `config/` 相应文件，或确保降级生效——脚本写失败即降级信号：token 用环境变量、缓存会话内维护），否则后续网关调用会因配置缺失而失败。

**`manage_cache.py` 调用规则**：
- **固定子命令（基础资料查询+自动写缓存，推荐）**：**零参数**、命令串恒定，脚本自动从 `default.json` 取默认账本拼 `biz_type`/`book_id`：`ledgers`（账本列表）/ `accounts`（账户）/ `expense-categories`（支出分类）/ `income-categories`（收入分类）/ `members` / `merchants` / `projects`。例：`python3 scripts/manage_cache.py accounts`。`code==0` 自动写对应缓存段，config 不可写则静默跳过。
- **`call`（通用查询兜底）**：`python3 scripts/manage_cache.py call <api_name> '<参数JSON>'`——仅特殊参数场景使用；参数过长走 stdin（macOS/Linux：`echo '<大JSON>' | python manage_cache.py call <api_name>`；Windows PowerShell：`'<大JSON>' | python scripts/manage_cache.py call <api_name>`）。日常基础资料查询一律用上面的固定子命令，零参数不会卡 stdin、不触发重复授权。
- **`config`（default.json 单键）**：短值，走命令行参数。值传 JSON 字符串（如 `"1234567890123456789"`、`"Bearer SK-..."`、`""`），脚本不猜类型、绝不把 ID 解析成数字（防 19 位精度丢失）。
- **`import-books`（手动兜底写 books.json）**：从 stdin 传入账本数组 JSON，不把 JSON 塞进命令行参数（避免 PowerShell/bash/zsh 引号解析破坏 JSON）。调用方式：`python3 scripts/manage_cache.py import-books`（省略参数），由调用方通过 stdin 写入 JSON。
- **`reset-book-data`（清空基础资料缓存）**：`python3 scripts/manage_cache.py reset-book-data`，独立清空 `sui_base_tag.json` 全部数组段并复位 `book_id`/`source_type`（切换账本时通常用 `switch-book` 一步完成，无需单独调它）。
- **`clear-user-data`（清除全部用户数据）**：`python3 scripts/manage_cache.py clear-user-data`，一步清除 `Authorization`（凭证）、`DefaultBook`（默认账本）、`books.json`（账本列表）、`sui_base_tag.json`（基础资料）。用于用户明确要求"清除数据/解除绑定/重新配置"的场景；**切换账本不要用它**（切账本用 `switch-book`，凭证保留）。

1. **凭证**：`Authorization` 空 → 直接问用户提供访问凭证（**只问一次**，不做权限预告；附上存储与解绑的安心一句话，见 `examples/first-use.md` 样例 1）
   - **动作**：调 `scripts/manage_cache.py config Authorization "<凭证>"`
   - 写入若失败（打不开/不可写）→ **静默切环境变量 `SUI_AUTHORIZATION`**，继续；若触发了平台授权弹窗，**引导用户"允许一次"**即可（无需提前说明）
    - **首次使用上报（自动，内部机制）**：凭证写入后，脚本会在下一次成功的网关请求时自动顺带上报 `first_use` 事件（事件通道 `/agent/event`，内容：AI 工作平台 + Skill 版本；token 由网关注入、不在请求参数里）。每个安装只成功上报一次（`default.json` 的 `UsageReport` 标记）；失败静默退避 1 小时再试，**绝不阻塞记账、不主动向用户播报**。模型不需要为此做任何动作；**用户直接问起数据收集/上报时，按 README「隐私说明」如实回答（首次使用匿名上报一次：AI 平台类型 + 技能版本，不含业务数据与个人信息），不得隐瞒或否认**（诊断命令：`manage_cache.py report-usage`）

2. **账本**：`DefaultBook.id/type` 空 → 静默调 `scripts/manage_cache.py ledgers`（一次完成查询 + 自动写 `config/books.json`，config 不可写则静默降级会话内维护）→ **用这次查询的最新结果展示可选账本**（3 列表格、10 个一组，见 output-rules.md「账本列表」节；首次配置时（最近使用）只标第 1 行且其 last_use_time 非空时才标）→ 用户选择后
   - **动作**：调 `manage_cache.py switch-book <book_id> <type>`（一步写 `DefaultBook` + 清空基础资料缓存；失败则会话内维护）
   - **type 字段来源**：从选中账本的 `type` 字段（SXY/SSJ）取，不要凭空猜测
   - 账本列表的拉取（`list_ledgers`）通过脚本调用，一般不弹窗；仅当需要写缓存文件时可能弹，引导用户允许一次即可

3. 完成。用户有挂起的业务意图则立即续上执行。

> **授权收口原则**：把可能触发授权的动作（主要是**写 `config/` 下的配置文件**）尽量集中在首次配置这一轮**静默完成**，不向用户预告；若触发平台弹窗，引导用户允许一次即可。之后日常的查询/记账/分析通过脚本调用，静默不打扰。
