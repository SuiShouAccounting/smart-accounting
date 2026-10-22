# 随手记/神象云 记账技能（smart-accounting）

[![skills.sh](https://www.skills.sh/b/SuiShouAccounting/smart-accounting)](https://www.skills.sh/suishouaccounting/smart-accounting/smart-accounting)

给 AI 编码/工作助手装上"记账能力"：一句话记账（支出/收入/转账/余额变更）、批量导入账单流水、查询账本基础资料（账户/分类/成员/商家/项目）、财务分析（流水明细/汇总/分组统计）、随手记与神象云产品使用帮助。

## 安装

需要 [Node.js](https://nodejs.org/)（用于 `npx`）：

```bash
npx skills add SuiShouAccounting/smart-accounting -g   # 安装到你当前所在的 AI 工作平台
```

- `-g`：安装到用户目录（全局，所有项目可用）；去掉 `-g` 则安装到当前项目
- 安装时如提示选择助手（agent），选择你正在使用的即可（支持 Claude Code、Cursor、Codex、OpenCode 等 80+ 款）

## 开始使用

安装后直接对你的助手说，例如：

> 记一笔：中午吃饭花了 20 块

首次使用会请你提供一段**授权信息**（以 `Bearer SK-` 开头的字符串，在随手记/神象云 App 的"我的"页面可以找到），并提供后按提示选一个账本即可开始记账。

## 更新 / 卸载

```bash
npx skills update smart-accounting -g   # 更新
npx skills remove smart-accounting -g   # 卸载
```

## 隐私说明

- 您的账本数据仅发送到随手记/神象云**官方网关**（`yun.feidee.net`；主域 feidee.net 的 ICP 备案：[粤ICP备2022107703号-4](https://beian.miit.gov.cn/)，备案主体 深圳市随手科技有限公司），授权信息只保存在您的本地设备
- 首次使用时会匿名上报一次平台信息，用于使用统计

## 许可

Apache License 2.0（详见仓库根目录 `LICENSE.txt`）
