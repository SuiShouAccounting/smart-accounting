# -*- coding: utf-8 -*-
"""
manage_cache.py - 缓存写入与基础资料查询的统一受控脚本

一、基础资料查询（固定子命令，零参数；查 + code==0 自动写缓存 + 打印完整响应）：
    ledgers            查账本列表        → config/books.json
    accounts           查账户列表        → sui_base_tag.json → accounts
    expense-categories 查支出分类        → sui_base_tag.json → expense
    income-categories  查收入分类        → sui_base_tag.json → income
    members            查成员列表        → sui_base_tag.json → members
    merchants          查商家列表        → sui_base_tag.json → merchants
    projects           查项目列表        → sui_base_tag.json → projects

二、配置与缓存写入：
    config <点路径键> <值>    写 default.json 单键（如 Authorization）
    switch-book <id> <type>     切换默认账本：一步写 DefaultBook + 清空基础资料缓存（不动凭证）
    reset-book-data          重置当前账本基础资料缓存（清空全部段并复位账本标记）
    clear-user-data          清除全部用户数据：Authorization/DefaultBook/账本列表/基础资料缓存
    import-books             手动兜底写 books.json（stdin 传 JSON）

三、通用查询（兜底，特殊参数场景才用）：
    call <api_name> ['<业务参数JSON>']

写成功输出 OK（退出码 0），写失败输出简短 stderr 错误（退出码非 0，即降级信号）；
查输出网关原始响应（退出码 0）。仅依赖 Python 标准库，跨平台一致。
"""
from __future__ import print_function

import json
import sys
import urllib.error

import common

_EMPTY_BASE_TAG = {
    "book_id": "",
    "source_type": "",
    "expense": [],
    "income": [],
    "accounts": [],
    "members": [],
    "merchants": [],
    "projects": [],
}

_FIXED_CALLS = {
    "ledgers": ("list_ledgers", "", "books", None),
    "accounts": ("list_book_accounts", "", "base_tag", "accounts"),
    "expense-categories": ("list_book_categories", "expense", "base_tag", "expense"),
    "income-categories": ("list_book_categories", "income", "base_tag", "income"),
    "members": ("list_book_members", "", "base_tag", "members"),
    "merchants": ("list_book_merchants", "", "base_tag", "merchants"),
    "projects": ("list_book_projects", "", "base_tag", "projects"),
}

_LIST_API_SEGMENTS = {
    "list_book_accounts": "accounts",
    "list_book_members": "members",
    "list_book_merchants": "merchants",
    "list_book_projects": "projects",
}


class _UsageError(Exception):
    pass


def _as_str(text):
    if isinstance(text, str):
        s = text.strip()
        if len(s) >= 2 and ((s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'")):
            return s[1:-1]
        return s
    return str(text)


def _as_json(text, expected):
    if isinstance(text, str):
        try:
            value = json.loads(text)
        except ValueError:
            raise ValueError("参数 JSON 解析失败")
    else:
        value = text
    exp = expected if isinstance(expected, tuple) else (expected,)
    if not isinstance(value, exp):
        names = "/".join(t.__name__ for t in exp)
        raise ValueError("参数应传 %s 类型" % names)
    return value


def _read_stdin():
    # 交互终端（TTY）要求走命令行参数；管道场景用有界读取
    if sys.stdin.isatty():
        raise _UsageError()
    text = common.read_stdin_bounded()
    if not text.strip():
        raise ValueError("stdin 未读到数据（请传 JSON 参数，或把 JSON 管道进来再运行）")
    return text


def _try_read_stdin():
    if sys.stdin.isatty():
        return ""
    return common.read_stdin_bounded()


def _save_books(books):
    norm = []
    for b in books:
        if not isinstance(b, dict):
            continue
        norm.append({
            "book_id": b.get("book_id", ""),
            "name": b.get("name", ""),
            "type": b.get("type") or b.get("biz_type", ""),
        })
    common.write_config("books", {"books": norm})


def _save_base_tag(segment, items):
    buf = common.load_config("base_tag")
    for k in _EMPTY_BASE_TAG:
        buf.setdefault(k, _EMPTY_BASE_TAG[k])

    bk = common.default_book()
    if bk.get("id"):
        buf["book_id"] = bk["id"]
    if bk.get("type"):
        buf["source_type"] = bk["type"]
    buf[segment] = items

    common.write_config("base_tag", buf)


def _clear_base_tag():
    buf = common.load_config("base_tag")
    for k in _EMPTY_BASE_TAG:
        buf[k] = _EMPTY_BASE_TAG[k]
    common.write_config("base_tag", buf)


def _query_and_cache(api_name, params):
    try:
        raw = common.send_request(api_name, params)
    except urllib.error.HTTPError as e:
        common.err("HTTP 错误：%s %s" % (e.code, e.reason))
        return 1
    except urllib.error.URLError as e:
        common.err("网络错误：%s" % (e.reason,))
        return 1

    try:
        response_data = json.loads(raw)
    except ValueError:
        response_data = {}
    if response_data.get("code") == 0:
        data = response_data.get("data")
        if isinstance(data, list):
            try:
                if api_name == "list_ledgers":
                    _save_books(data)
                elif api_name == "list_book_categories":
                    _save_base_tag("income" if params.get("trade_type") == "income" else "expense", data)
                elif api_name in _LIST_API_SEGMENTS:
                    _save_base_tag(_LIST_API_SEGMENTS[api_name], data)
            except OSError:
                pass

    print(raw)
    return 0


def _query_fixed(cmd):
    api_name, trade_type, _, _ = _FIXED_CALLS[cmd]
    bk = common.default_book()
    params = {"biz_type": bk.get("type", ""), "book_id": bk.get("id", "")}
    if trade_type:
        params["trade_type"] = trade_type
    return _query_and_cache(api_name, params)


def _save_config_key(argv):
    if len(argv) < 4:
        raise _UsageError()
    key = argv[2]
    value = _as_str(argv[3])
    data = common.load_config("default")
    parts = key.split(".")
    cur = data
    for part in parts[:-1]:
        if not isinstance(cur.get(part), dict):
            cur[part] = {}
        cur = cur[part]
    cur[parts[-1]] = value
    return common.write_config("default", data)


def _save_books_manual(argv):
    if len(argv) < 3:
        books = _as_json(_read_stdin(), (list, dict))
    else:
        books = _as_json(argv[2], (list, dict))
    if isinstance(books, dict) and "books" in books:
        books = books["books"]
    if not isinstance(books, list):
        raise ValueError("books 应为数组")
    return common.write_config("books", {"books": books})


def _switch_default_book(argv):
    if len(argv) < 4:
        raise _UsageError()
    book_id = _as_str(argv[2])
    book_type = _as_str(argv[3])

    data = common.load_config("default")
    data.setdefault("DefaultBook", {})
    data["DefaultBook"]["id"] = book_id
    data["DefaultBook"]["type"] = book_type
    common.write_config("default", data)

    _clear_base_tag()
    return 0


def _clear_user_data(argv):
    data = common.load_config("default")
    data["Authorization"] = ""
    data["DefaultBook"] = {"id": "", "type": ""}
    common.write_config("default", data)

    _clear_base_tag()
    common.write_config("books", {"books": []})
    return 0


def _query_any(argv):
    if len(argv) < 3 or argv[2] in ("-h", "--help", "help"):
        raise _UsageError()
    api_name = argv[2]
    params = {}
    if len(argv) >= 4:
        try:
            params = json.loads(argv[3])
        except ValueError:
            raise ValueError("参数 JSON 解析失败")
    else:
        stdin_text = _try_read_stdin().strip()
        if stdin_text:
            try:
                params = json.loads(stdin_text)
            except ValueError:
                raise ValueError("参数 JSON 解析失败")
    return _query_and_cache(api_name, params)


def _report_usage(argv):
    """手动触发首次使用上报（诊断用；正常流程由网关请求自动触发，无需手动）。"""
    result = common.try_report_first_use(force_backoff=True)
    data = common.load_config("default")
    report = data.get("UsageReport") or {}
    if result == "reported":
        print("OK reported=1 platform=%s" % (report.get("platform", ""),))
    elif report.get("reported"):
        print("SKIPPED already-reported at=%s" % (report.get("at", ""),))
    else:
        print("FAILED 上报未成功（不影响记账；失败后 1 小时内自动重试）")
    return 0 if result in ("reported", "skipped") else 1


def _usage():
    common.err(
        "usage:\n"
        "  manage_cache.py ledgers|accounts|expense-categories|income-categories|members|merchants|projects\n"
        "                                                        # 固定子命令：零参数查基础资料并自动写缓存\n"
        "  manage_cache.py config <点路径键> <JSON值>           # 写 default.json 单键\n"
        "  manage_cache.py switch-book <id> <type>                 # 切换默认账本：写 DefaultBook + 清空基础资料缓存（不动凭证）\n"
        "  manage_cache.py reset-book-data                      # 重置当前账本基础资料缓存\n"
        "  manage_cache.py clear-user-data                      # 清除全部用户数据（凭证/默认账本/账本列表/基础资料）\n"
        "  manage_cache.py report-usage                        # 手动触发首次使用上报（诊断用）\n"
        "  manage_cache.py import-books ['<账本数组 JSON>']     # 手动兜底写 books.json（省略参数走 stdin）\n"
        "  manage_cache.py call <api_name> '<参数JSON>'         # 通用查询（兜底）\n"
    )
    return 2


def _run(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help", "help"):
        return _usage()
    cmd = argv[1]
    if cmd == "config":
        return _save_config_key(argv)
    if cmd == "switch-book":
        return _switch_default_book(argv)
    if cmd == "reset-book-data":
        _clear_base_tag()
        return 0
    if cmd == "clear-user-data":
        return _clear_user_data(argv)
    if cmd == "report-usage":
        return _report_usage(argv)
    if cmd == "import-books":
        return _save_books_manual(argv)
    if cmd == "call":
        return _query_any(argv)
    if cmd in _FIXED_CALLS:
        return _query_fixed(cmd)
    return _usage()


def main(argv):
    try:
        return _run(argv)
    except _UsageError:
        return _usage()
    except common.AuthMissingError as e:
        common.err(str(e))
        return 2
    except ValueError as e:
        common.err("参数错误：%s" % (e,))
        return 2
    except OSError as e:
        common.err("写入失败（目录可能不可写）：%s" % (e,))
        return 1
    except Exception as e:
        common.err("写入失败：%s" % (e,))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
