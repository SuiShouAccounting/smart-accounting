# 基础资料查询子场景

## 何时读这个文件

当用户需要查询、刷新基础资料（账户/分类/成员/商家/项目）时阅读此文件。包括：
- 用户说"查账户"、"看分类"、"查成员"、"查商家"、"查项目"
- 用户说"刷新基础资料"、"更新标签"
- 用户说"加个账户"、"加个分类"、"改分类名"等（新增/改名，SXY/SSJ 账本均支持）
- 记账过程中需要先获取基础资料

## 前置条件

- 已通过 `references/workflow.md` 的 Phase 1 配置检查
- `config/default.json` 中 `Authorization`、`DefaultBook.id`、`DefaultBook.type` 均已填写

## 基础资料类型

各类型共有字段：`id` / `name` / `act_enable`（true=可记账）/ `hidden`；其余字段按类型不同（不要假设所有类型字段齐全）：

| 类型 | api_name | 层级规则 | act_enable 规则 | 记账必填 | 特有字段 |
|------|----------|---------|----------------|---------|---------|
| 分类 (category) | `list_book_categories` | 二级 | 一级 false，二级 true | 是 | `parent_id`/`parent_name`/`type`(expense/income) |
| 账户 (account) | `list_book_accounts` | 混合 | 叶子节点 true | 是 | `parent_id`/`parent_name`/`type`/`currency`/`counted_out_assets` |
| 成员 (member) | `list_book_members` | 扁平一级 | 全部 true | 否 | 无 parent_*/type |
| 商家 (merchant) | `list_book_merchants` | 扁平一级 | 全部 true | 否 | 无 parent_*/type |
| 项目 (project) | `list_book_projects` | **SXY 有父子层级；SSJ 扁平一级** | **SXY：叶子 true、带子项目的顶层 false；SSJ：全部 true** | 否 | `parent_id`/`parent_name`（SSJ 常为 null） |

> 记账时只能选 `act_enable=true` 的标签。**SXY 账本选项目时注意**：带子项目的顶层项目不可直接记账，要选到其子项目；SSJ 项目没有层级，任意项目都可选。

## 缓存文件结构（权威 schema）

所有基础资料查询结果都写入 `config/sui_base_tag.json`，**全扁平结构**：

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

> **为什么 expense/income 拆在顶层**：记账时记支出只能用支出分类、记收入只能用收入分类，业务上是独立资源。

**重置缓存**：各数组清空、`book_id`/`source_type` 置空（保持键存在）。

## 平台不可写时的缓存降级

若平台**硬性不允许写 `config/`**（经授权仍被拒），则基础资料**不落文件，改为会话内缓存**：把查询结果保存在**当前对话上下文中**（模型记住 `book_id`/`source_type` 与各类型数组），在本次会话内复用；跨会话则丢失，下次重新静默查询。判定规则：

- 能写 `config/sui_base_tag.json` → 正常落盘，跨会话持久
- 不能写 → 会话内缓存，本次会话按"缓存优先"复用；新会话重新查
- 无论哪种，**对外表现一致**：用户无感知，只是写不了时每次会话首次查询稍慢

## 查询流程

### Step 1: 确定查询类型
- 用户具体指定（如"查账户"）→ 直接查对应类型
- 用户说"查基础资料/查标签" → 询问查哪一类
- 记账流程中需要 → 按记账需要自动查对应类型

### Step 2: 检查缓存
- 检查 `config/sui_base_tag.json` 中对应分类是否已有数据
- 有缓存且用户未要求刷新 → 直接用缓存
- 缓存为空、账本已切换、或用户要求刷新 → 执行 Step 3

### Step 3: 调用网关查询（静默）

统一通过**受控脚本 `scripts/manage_cache.py` 的固定子命令**查询——**零参数**，脚本自动从 `config/default.json` 取默认账本的 `biz_type`/`book_id` 拼参数，一次完成"发请求 + `code==0` 时自动把 `data` 写入对应缓存 + 打印完整响应"，config 不可写时静默跳过写缓存、照常输出响应（模型可会话内兜底）。

```bash
# 固定子命令（零参数、命令恒定，推荐——不要用 call/stdin 查这些）
python3 scripts/manage_cache.py ledgers              # 账本列表 → 自动写 books.json
python3 scripts/manage_cache.py accounts             # 账户 → sui_base_tag.json accounts 段
python3 scripts/manage_cache.py expense-categories   # 支出分类 → expense 段
python3 scripts/manage_cache.py income-categories    # 收入分类 → income 段
python3 scripts/manage_cache.py members              # 成员
python3 scripts/manage_cache.py merchants            # 商家
python3 scripts/manage_cache.py projects             # 项目
```

> 兜底：`python3 scripts/manage_cache.py call <api_name> '<参数JSON>'`（特殊参数场景才用；参数过长走 stdin）。日常基础资料查询**一律用上面的固定子命令**，零参数不会卡 stdin、命令串恒定不触发重复授权。

> 账本列表已包含在固定子命令 `ledgers` 中，自动写 `config/books.json`。

**写入缓存**：固定子命令 / `call` 在 `code==0` 时自动把 `data` 写入 `config/sui_base_tag.json` 对应数组（分类写 `expense`/`income` 段），并记录缓存的 `book_id`、`source_type` 为当前账本（`ledgers` 写 `books.json`）。无需模型再手动调 `import-books`。config 不可写时脚本静默跳过写缓存，模型按「会话内缓存」兜底。

**网关返回字段说明**：
- 分类/账户的 `data` 已是扁平列表，含 `parent_id`/`parent_name` 保留层级展示
- SSJ 账户的 `type`/`currency` 等可能为 null，属正常，不影响使用
- 项目 SSJ 的 `parent_id` 可能为 null（无父子关系），同样正常

### Step 4: 使用缓存结果
- 查询后已写入 `config/sui_base_tag.json`，后续直接读缓存
- 展示给用户时显示名称（不显示 ID），让用户选名称

## 记账中自动使用

当记账流程需要基础资料时：
1. 先查 `config/sui_base_tag.json` 缓存
2. 缓存存在 → 让用户从缓存选标签名称
3. 缓存不存在 → 自动执行查询流程并缓存
4. 用 名称 → ID 映射填充记账请求
5. **只展示 `act_enable=true` 的标签供选择**

## 新增/改名基础资料（SXY/SSJ 账本均支持）

本技能支持新增与改名基础资料，**SXY（神象云）与 SSJ（同步账本）账本均可用**（双业务线已实测通过）。

| 操作 | api_name |
|------|----------|
| 新建账户 | `create_book_account`（参数 `name`；传 `parent_id` 在该账户下建子账户） |
| 账户改名 | `update_book_account`（仅改名称，`id`+`name`） |
| 新建分类 | `create_book_category`（参数 `name`+`trade_type`；`parent_id` 建二级） |
| 分类改名 | `update_book_category`（仅改名称） |
| 新建/改名成员/商家 | `create_book_member/merchant`、`update_book_*`（仅改名称） |
| 新建/改名项目 | `create_book_project`（`parent_id` 按下方「项目创建传参规则」传）、`update_book_project`（仅改名称） |

> **新建操作（账户/分类/成员/商家/项目）的前提：用户明确要求**（"帮我加一个 XX""没有就新建一个"）。记账时匹配不到基础资料条目，按记账流程的兜底/询问规则处理（见 `references/accounting.md`），**严禁自行新建**。

**两线差异（按当前账本类型 `DefaultBook.type` 执行）**：
- **项目**：`name` 两线必填；`parent_id` 两线走**同一套**「项目创建传参规则」（按项目列表判断，**不按业务线区分**）。
- **账户**：两线均可建顶层账户与子账户；SSJ 账户列表的 `type`/`currency` 可能为空，展示时不要展示类型列、不影响使用。
- 改名两线语义一致：仅改名称，其它字段不动。

**项目创建传参规则**（先取项目列表再定 `parent_id`；列表取自基础资料缓存，缓存为空先查 `list_book_projects`）。顶层项目 = 列表中 `parent_id` 为 `"0"` 的项（SSJ 项目全是扁平项、`parent_id` 为 null，**不是**顶层父项目）：
1. 用户明确指定父项目 → 传该项目 ID
2. 用户没指定、列表里**有且只有 1 个**顶层项目 → **必传**该顶层项目 ID（单父项目账本不传会归位不准，不要省略）
3. 用户没指定、有**多个**顶层项目 → 问用户建在哪个项目下（表格列候选，只问这一项）
4. 列表里没有顶层项目（列表为空，或 SSJ 扁平列表）→ 不传 `parent_id`，网关自动处理（神象云无一级项目时自动创建"所有"；SSJ 即使传了也会忽略）

> 两线跑同一流程即可，不需要按业务线判断走哪条规则。

> 具体必填字段以网关返回的报错 `msg` 为准（`code==1400` 指明缺失参数）。新建/改名成功后刷新对应缓存段再向用户确认。

### 场景：用户说"加个微信账户"

1. **先答"能不能"**：SXY/SSJ 账本都可以加账户——直接答"可以"并执行。不要先口头答应、事后再核实，更不要说"这类账本不支持"。
2. **执行**：`create_book_account`（参数 `name`，如"微信"）→ 成功后刷新账户缓存段 → 用业务语言告知"已加好"。
3. 若用户只是记账时提到不存在的账户（如"从招行卡…"），按 `examples/record-transaction.md` 的"账户不存在"边界场景处理：告知未找到 + 给出选项（用现有账户；**两类账本都可给"帮您新建"选项**）。

## Common Pitfalls

- ❌ 用缓存里旧账本的数据
- ✅ 查询前检查缓存 `book_id`/`source_type` 是否等于当前账本，不匹配则重新查询

- ❌ 让用户输入/看到标签 ID
- ✅ 展示名称，内部映射 ID

- ❌ 把一级分类（act_enable=false）列入可选
- ✅ 只让用户选 `act_enable=true` 的标签

- ❌ 每次记账都重新查基础资料
- ✅ 缓存优先，缓存空或用户要求刷新才查

- ❌ 把网关地址、凭证、字段名暴露给用户
- ✅ 只用业务语言展示基础资料列表
