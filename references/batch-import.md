# 批量导入流水子场景

## 何时读这个文件

当用户要求批量记账、从文件导入流水、批量上传账单（如"帮我把这个 CSV 里的流水都记上""导入这份账单"）时阅读此文件。

## 前置条件

- 已通过 `references/workflow.md` 的 Phase 1 配置检查
- `config/sui_base_tag.json` 基础资料缓存已有数据（账户/分类映射用，缺失先走 `references/basic-data.md`）
- 已读取用户提供文件的内容（Read 工具），明确每条流水的金额/类型/账户/分类/时间

## 网关接口（create_batch_transactions）

批量记账统一走 `create_batch_transactions`（**注意区别于单条 `create_transaction`**）。参数平铺规则与单条一致，业务字段放在 `transactions` 数组里，**每条字段名与单条完全相同**：

```bash
# Write sui_cache.json（覆盖写）：
{"api_name":"create_batch_transactions","params":{"transactions":[
  {"trade_type":"expense","from_amount":"0.22","from_account_id":"<ID>","category_id":"<ID>","transaction_time":"1786417011000","remark":"午饭"},
  {"trade_type":"income","to_amount":"1.5","to_account_id":"<ID>","category_id":"<ID>","transaction_time":"1786417022000"}
]}}
# 然后跑固定命令（命令串恒定，与单条记账完全一致）：
python3 scripts/call_gateway.py run
```

要点：

- `book_id`/`biz_type` 由脚本自动补，**不必传**
- 每条支持 `time`（自然语言，如 `"time":"昨天上午"`），脚本自动逐条转毫秒 `transaction_time`；转换后**绝不向用户回显毫秒值**
- **每批最多 20 条**，超过必须分批（网关会直接拒绝）；文件条数多时按顺序每 20 条一批，**上一批跑完再发下一批**
- **"一次性 N 笔"（无论有无文件）默认一律走本接口**：**严禁**自己编写请求脚本（post_txs.py 之类）；只有批量接口**失败**时（见下方失败处理）才允许降级为逐条 `create_transaction`，**自己写脚本任何情况都严禁**
- 每条 `trade_type` 判断规则与单条记账一致（见 `references/accounting.md`「记账类型判断」）；分类/账户按语义映射（见「标签名称 → ID 映射」），语义无法匹配的条目**先记下，最后统一向用户确认**，不要每条都打断用户

## 响应结构（与单条不同，重要）

整体 `code==0` **不代表全部成功**，逐项成败看 `items[]`：

```json
{"code":0,"msg":"ok","data":{
  "total":20,"success":18,"failed":2,
  "items":[
    {"index":0,"success":true,"transaction_id":"...","transaction":{...}},
    {"index":3,"success":false,"code":2200,"msg":"接口处理失败，请告知用户稍后重试或换个提问方式","transaction":{"trade_type":"expense","from_amount":"0.22","remark":"午饭"}}
  ]
}}
```

- `data.total/success/failed`：总数/成功数/失败数
- `items[].index`：该条在 `transactions` 数组中的下标（0 起），**定位失败项靠 index + `transaction` 里的原始入参**
- 失败项的 `code` 取值域：`1200`（参数格式错误，`msg` 指明具体参数）/ `1400`（缺必填参数）/ `1500`（限流）/ `2100/2200`（上游处理失败）/ `9000`（未知错误）。**不存在 2300**
- 注意：`2200/2100` 的 `msg` 是**固定话术**，不含具体失败原因（原因只进服务端日志）——所以不要假装知道"为什么失败"，用 `transaction` 回显该条内容请用户核对（分类/账户是否写错），能猜就按猜测问（"这笔的分类是不是记错了？"），不能猜就请用户重述该笔

## 失败处理（防重复记账，硬约束）

- **批量接口本身失败**（整批被拒：参数级 1200，或按 `msg` 修正后重试一次仍 1200）：用业务语言告知"批量上传这会儿用不了，我帮您一笔一笔记上"，然后**降级为逐条 `create_transaction`**（仍走受控脚本 `run`，严禁自己写脚本）——**不能让批量接口故障卡住用户的记账**；全部记完后照常表格汇总
- **逐项失败**：只针对失败项修正参数（如先查分类列表拿到正确 `category_id`）后，**单条 `create_transaction` 或重新组一小批**重发；成功项**绝不动**
- **绝不整批重发**：哪怕只有 1 条失败，也不能把整批重新上传（已成功的会重复记账）
- **整批超时/无响应**：结果不确定，先调 `list_transactions`（按时间范围）核对已落地的流水，再只补传缺失的
- **结果展示必须逐项核验**：以响应 `data.total/success/failed` 与 `items[].success` 为准才说"已入账"；绝不以"请求已发出"或脚本退出码宣布成功
- 汇总展示前，把全部批次跑完；不要跑一批展示一批打断用户

## 展示结果

按 `references/output-rules.md` 用表格汇总：

| 状态 | 条数 | 说明 |
| ---- | ---- | ---- |
| 成功 | 18 | 已记入账本 |
| 失败 | 2 | 待确认（附该笔内容） |

失败项回显原始内容请用户核对（`2200` 的固定话术不含原因，**不要编造原因**）——如"第 4 笔：午饭 0.22 元，这笔好像没记上，是不是分类没对上？请告诉我记到哪个分类"，拿到答案后单独重发该条。

## 常见问题

- ❌ 把 100 条流水塞进一次请求
- ✅ 每 20 条一批，顺序分批执行

- ❌ 整批失败后把整批重新上传
- ✅ 只补失败项；超时先查流水列表核对再补传

- ❌ 每条流水都问用户分类/账户
- ✅ 语义匹配自动填入，匹配不到的最后统一确认

- ❌ 向用户展示 index、transaction_time 毫秒值等内部字段
- ✅ 用业务语言汇总（如"20 笔已记好，2 笔需要确认分类"）

- ❌ 自己编写请求脚本提交；或不先试批量接口、默认就拆成 N 次单条
- ✅ 默认 `create_batch_transactions`（每批 ≤20 条），受控脚本 `run` 命令执行，展示前逐项核验；**仅当批量接口失败**才降级逐条 `create_transaction`（业务语言告知后执行）
