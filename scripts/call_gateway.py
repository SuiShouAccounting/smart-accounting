# -*- coding: utf-8 -*-
"""
call_gateway.py - 统一网关请求封装（固定子命令 + 瞬态参数文件，命令串恒定，平台无关）

用法（命令只有两条固定形式，可被平台记忆放行）：
    python call_gateway.py query   # 查询类接口（只读：list_*/get_*）
    python call_gateway.py run     # 全部接口（含记账 create_transaction 等操作类）

    参数来源（优先级：瞬态参数文件 sui_cache.json > stdin）：
    瞬态参数文件：模型用 Write 工具把本次参数写入当前工作目录 os.getcwd() 下的
    sui_cache.json（固定文件名，覆盖写、不删除），再跑固定命令——命令文本 100% 恒定，
    参数与命令完全解耦，平台无关。
    stdin 兜底：仅在 Write 不便时使用（受 shell 编码/引号影响）。

    固定 JSON 结构：
    {"api_name": "<接口名>", "params": { "<该接口业务参数>" }}
    例：
    {"api_name":"get_transaction_summary","params":{"time":"本月"}}
    {"api_name":"create_transaction","params":{"trade_type":"expense","from_amount":"12.5",...}}

    params 支持 time 字段（自然语言，common.resolve_time 自动转毫秒）；
    book_id/biz_type 缺省自动取默认账本。

依赖：仅 Python 标准库，零第三方依赖。
跨平台：macOS / Linux / Windows 同一份代码；body 由程序构造，不经过任何 shell 引号解析。
"""
from __future__ import print_function

import json
import os
import sys
import urllib.error

import common

# query 白名单：查询类（只读）接口；create_/update_/delete_ 等操作类永远走 run
_QUERY_API_NAMES = {
    "list_ledgers",
    "list_book_accounts",
    "list_book_categories",
    "list_book_members",
    "list_book_merchants",
    "list_book_projects",
    "list_transactions",
    "get_transaction_summary",
    "get_transaction_group_summary",
}

# query 永远禁止的写操作前缀（双保险）
_FORBIDDEN_PREFIXES = ("create_", "update_", "delete_")


def _build_body(cfg, api_name, params):
    body = {"skill_version": cfg.get("SkillVersion", ""), "api_name": api_name}
    db = cfg.get("DefaultBook", {}) or {}
    body.update(params)
    body.setdefault("biz_type", db.get("type", ""))
    body.setdefault("book_id", db.get("id", ""))
    return body


def _read_stdin_json():
    try:
        text = sys.stdin.read().strip()
    except Exception:
        text = ""
    if not text:
        return {}
    if text.startswith(u"﻿"):
        text = text.lstrip(u"﻿")
    try:
        return json.loads(text)
    except ValueError:
        try:
            return json.loads(text.decode("utf-8-sig"))
        except Exception:
            raise


def _read_cache_json():
    path = os.path.join(os.getcwd(), "sui_cache.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("api_name"):
            return data
    except (OSError, ValueError, AttributeError):
        pass
    return {}


def _dispatch(subcmd, stdin_json):
    api_name = stdin_json.get("api_name", "")
    if not api_name:
        raise ValueError("缺少 api_name")
    if subcmd == "query":
        if api_name.startswith(_FORBIDDEN_PREFIXES):
            raise ValueError("query 禁止操作类接口：%s（操作类请用 run）" % api_name)
        if api_name not in _QUERY_API_NAMES:
            raise ValueError("query 仅允许查询类接口，禁止：%s" % api_name)
    params = dict(stdin_json.get("params") or {})
    err = common.resolve_time(params, api_name)
    if err:
        raise ValueError(err)
    return api_name, params


def _post(cfg, api_name, params):
    body = _build_body(cfg, api_name, params)
    try:
        print(common.send_request(api_name, params, body_builder=lambda p: body))
        return 0, ""
    except ValueError as e:
        return 2, str(e)
    except urllib.error.HTTPError as e:
        return 1, "HTTP 错误：%s %s" % (e.code, e.reason)
    except urllib.error.URLError as e:
        return 1, "网络错误：%s" % e.reason


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help", "help"):
        sys.stderr.write("usage:\n"
                         "  python call_gateway.py query   # 固定命令，查询类接口（参数走 stdin 固定JSON）\n"
                         "  python call_gateway.py run     # 固定命令，全部接口（参数走 stdin 固定JSON）\n"
                         "  # stdin 固定结构：{\"api_name\":\"<接口名>\",\"params\":{...}}\n")
        return 2

    cmd = argv[1]
    if cmd not in ("query", "run"):
        sys.stderr.write("未知命令：%s（只支持 query / run，参数走 stdin）\n" % cmd)
        return 2

    cfg = common.load_config()
    try:
        stdin_json = _read_stdin_json()
        if not stdin_json:
            stdin_json = _read_cache_json()
    except ValueError as e:
        sys.stderr.write("参数 JSON 解析失败：%s\n" % e)
        return 2
    try:
        api_name, params = _dispatch(cmd, stdin_json)
    except ValueError as e:
        sys.stderr.write("参数错误：%s\n" % e)
        return 2
    code, err = _post(cfg, api_name, params)
    if err:
        sys.stderr.write("%s\n" % err)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
