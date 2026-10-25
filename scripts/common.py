# -*- coding: utf-8 -*-
"""
common.py - 受控脚本共用能力

供 scripts/manage_cache.py 与 scripts/call_gateway.py 复用：
配置读取、网关请求、原子写入、错误输出、自然语言时间解析。仅依赖 Python 标准库。
"""
from __future__ import print_function

import json
import os
import re
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_DIR = os.path.join(_BASE_DIR, "config")


def _harden_std_streams():
    """强制 stdout/stderr 以 UTF-8 输出（errors=replace）。

    Windows GBK 控制台下，网关响应里出现 U+FFFD 等无法编码字符时 print 会抛
    UnicodeEncodeError 导致脚本崩溃（实测：某 SXY 账本流水含脏字符）。
    统一 UTF-8 输出后模型侧解析也更稳定。
    """
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_harden_std_streams()


class AuthMissingError(ValueError):
    """Authorization 缺失（config 未配置或为空）——本地拦截，不发网络请求。"""

_FILES = {
    "default": "default.json",
    "books": "books.json",
    "base_tag": "sui_base_tag.json",
}


def err(msg):
    sys.stderr.write(msg + "\n")


def read_stdin_bounded(timeout=3.0):
    """读 stdin 全文（有界，绝不无限阻塞）。

    安全设计（某三方 AI 平台把命令的 stdin 留成"打开但无数据"的
    管道，sys.stdin.read() 等 EOF 等不到，进程会一直挂起）：
    - stdin 是交互终端（TTY）→ 不读，直接返回 ""
    - stdin 是管道/文件 → 守护线程读，最多等 `timeout` 秒，超时视为无数据
    返回读到的全文（可能为 ""）；任何情况下都会在有限时间内返回。
    """
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return ""
    except Exception:
        return ""
    box = {}

    def _reader():
        try:
            box["text"] = sys.stdin.read()
        except Exception:
            box["text"] = ""

    try:
        t = threading.Thread(target=_reader)
        t.daemon = True
        t.start()
        t.join(timeout)
    except Exception:
        return ""
    if t.is_alive():
        return ""  # 超时：stdin 是打开的空管道，放弃读取，绝不挂起
    text = box.get("text") or ""
    if text.startswith(u"﻿"):
        text = text.lstrip(u"﻿")
    return text


def path_of(name):
    return os.path.join(_CONFIG_DIR, _FILES[name])


def load_config(name="default"):
    p = path_of(name)
    if not os.path.exists(p):
        return {}
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def write_config(name, data, quiet=False):
    p = path_of(name)
    # 发布安装（npx skills add）可能不带 config/ 目录：确保目录存在，避免首次写入失败
    try:
        os.makedirs(_CONFIG_DIR, exist_ok=True)
    except Exception:
        pass
    tmp = p + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        raise
    if not quiet:
        print("OK")
    return 0


def send_request(api_name, params, cfg=None, body_builder=None):
    """发送网关请求，返回原始响应文本。

    body_builder(params) -> dict：可选，调用方自定义请求体（如补默认账本）；
    缺省按 skill_version + api_name + 平铺 params 构造。
    """
    cfg = cfg if cfg is not None else load_config()
    authorization = (os.environ.get("SUI_AUTHORIZATION") or cfg.get("Authorization", "")).strip()
    if not authorization:
        # 网关对"token 缺失"与"token 失效"统一返回 1100，诊断歧义；
        # 本地拦截并给出明确错误
        raise AuthMissingError("授权信息缺失：请先向用户获取凭证（config 未配置 Authorization）")
    apidomain = cfg.get("APIDomain", "")
    if not apidomain:
        raise ValueError("config 未配置 APIDomain，无法请求网关")
    if not apidomain.startswith("http"):
        apidomain = "http://" + apidomain

    if body_builder is not None:
        body = body_builder(params)
    else:
        body = {"skill_version": cfg.get("SkillVersion", ""), "api_name": api_name}
        body.update(params)
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        apidomain.rstrip("/") + "/agent/gateway",
        data=payload,
        headers={"Authorization": authorization, "Content-Type": "application/json"},
        method="POST",
    )
    # 批量记账为顺序逐条写上游，20 条约需 4~8 秒，超时按条数放宽（每条 ~1s，最少 10s）
    timeout = 10
    if api_name == "create_batch_transactions":
        items = params.get("transactions")
        n = len(items) if isinstance(items, list) else 0
        timeout = max(10, n * 2)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        text = resp.read().decode("utf-8")
    try:
        _r = json.loads(text)
        if isinstance(_r, dict) and _r.get("code") == 0:
            try_report_first_use(cfg)
    except Exception:
        pass
    return text


def default_book():
    return load_config().get("DefaultBook", {}) or {}


# ---- 首次使用上报（first_use 事件，每安装仅成功一次，静默 best-effort）----

# 上报失败后的重试退避窗口（秒）：避免事件通道故障时每次业务请求都白等超时
_USAGE_REPORT_BACKOFF_SEC = 3600


def _detect_platform_slug():
    """检测当前 AI 工作平台（trae / claude-code / dsh / ... 的 slug），失败返回 unknown。"""
    try:
        import detect_platform  # 同目录零依赖模块
        return detect_platform.detect_platform()
    except Exception:
        return "unknown"


def _save_usage_report(data):
    """静默写 UsageReport 到 default.json（任何异常吞掉）。"""
    try:
        cur = load_config("default")
        cur["UsageReport"] = data
        write_config("default", cur, quiet=True)
    except Exception:
        pass


def try_report_first_use(cfg=None, force_backoff=False):
    """尝试上报 first_use 事件。返回 "reported" / "skipped" / "failed"。

    触发条件：UsageReport.reported 未写 + Authorization 非空 + 退避窗口已到
    （首次尝试，或距上次失败 ≥1 小时）。成功写 reported 标记；
    失败写 last_attempt 进入退避。全部静默，绝不抛异常、绝不阻塞业务流程。
    """
    try:
        if cfg is None:
            cfg = load_config("default")
        authorization = (os.environ.get("SUI_AUTHORIZATION") or cfg.get("Authorization", "")).strip()
        if not authorization:
            return "skipped"
        report = cfg.get("UsageReport") or {}
        if report.get("reported"):
            return "skipped"
        now = time.time()
        last = report.get("last_attempt")
        if (not force_backoff and isinstance(last, (int, float))
                and now - float(last) < _USAGE_REPORT_BACKOFF_SEC):
            return "skipped"
        apidomain = (cfg.get("APIDomain") or "").strip()
        if not apidomain:
            return "skipped"
        if not apidomain.startswith("http"):
            apidomain = "http://" + apidomain

        platform = _detect_platform_slug()
        body = {
            "event": "first_use",
            "skill_version": cfg.get("SkillVersion", ""),
            "payload": {"platform": platform},
        }
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            apidomain.rstrip("/") + "/agent/event",
            data=payload,
            headers={"Authorization": authorization, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            text = resp.read().decode("utf-8", "replace")
        try:
            resp_obj = json.loads(text)
        except Exception:
            resp_obj = {}
        if isinstance(resp_obj, dict) and resp_obj.get("code") == 0:
            _save_usage_report({
                "reported": True,
                "platform": platform,
                "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            return "reported"
        _save_usage_report({"reported": False, "last_attempt": now})
        return "failed"
    except Exception:
        try:
            _save_usage_report({"reported": False, "last_attempt": time.time()})
        except Exception:
            pass
        return "failed"


# ---- 时间归一化：口语 → 标准格式 → 毫秒时间戳 ----
#
#   用户的时间表达是开放的（"9月20号上午""大前天""上周末""国庆那几天"…），无法穷举，
#   也不允许静默猜测取值——正确做法是**尽力归一化**：
#
#     口语说法 --normalize_time()--> 标准格式 --parse_time_expr()--> 毫秒时间戳
#
#   归一化是"尽力而为"：常见说法都能转成标准格式（"9月20号上午"→"2026-09-20 10:00"）；
#   实在转不了的才报错（写 params["_time_parse_failed"]），由调用方拒绝提交并在错误信息
#   里回显标准格式，让模型自己换算后重传。模型也可以**直接传标准格式**（跳过归一化），
#   这条路永远可用。
#
#   标准格式只有三种：
#       YYYY-MM-DD                单日
#       YYYY-MM-DD HH:MM          精确时刻
#       YYYY-MM-DD~YYYY-MM-DD      日期区间（含首尾两天）

_STD_DATE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_STD_DATETIME = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?$")
_STD_RANGE = re.compile(r"^(\d{4}-\d{1,2}-\d{1,2})\s*[~至到]\s*(\d{4}-\d{1,2}-\d{1,2})$")

# 时段词 → 代表时刻（把模糊时段统一为一个具体时刻）
_PERIOD_TIME = {
    "凌晨": "03:00", "清晨": "06:00", "早上": "08:00", "上午": "10:00",
    "中午": "12:00", "下午": "15:00", "傍晚": "18:00", "晚上": "20:00",
    "夜里": "22:00", "深夜": "23:00", "半夜": "23:00",
}

# 标准格式说明（错误信息里回显给模型，便于自我纠正）
TIME_FORMAT_HINT = (
    "时间标准格式：\n"
    "  YYYY-MM-DD            单日，如 2026-09-20\n"
    "  YYYY-MM-DD HH:MM      精确时刻，如 2026-09-20 10:00\n"
    "  YYYY-MM-DD~YYYY-MM-DD  日期区间，如 2026-09-01~2026-09-30\n"
    "（常见表达会自动归一化；无法转换的请换算成上述格式后传入）"
)


def _mk_date(y, m, d):
    try:
        return datetime(int(y), int(m), int(d))
    except ValueError:
        return None


def _month_range(year, month):
    """返回该月的 (首日, 末日)。"""
    start = datetime(year, month, 1)
    nxt = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    return start, nxt - timedelta(days=1)


def _norm_date_part(s, now, today):
    """把"日期部分"（已剥离时段）归一化。

    返回 ("day", datetime) 或 ("range", start_dt, end_dt) 或 None。
    """
    # 相对日
    if s in ("今天", "今日"):
        return ("day", today)
    if s in ("昨天", "昨日"):
        return ("day", today - timedelta(days=1))
    if s == "前天":
        return ("day", today - timedelta(days=2))
    if s == "大前天":
        return ("day", today - timedelta(days=3))
    if s in ("明天", "明日"):
        return ("day", today + timedelta(days=1))
    if s == "后天":
        return ("day", today + timedelta(days=2))

    # 相对周
    if s in ("本周", "这周", "这个星期", "本星期"):
        start = today - timedelta(days=now.weekday())
        return ("range", start, start + timedelta(days=6))
    if s in ("上周", "上个星期"):
        start = today - timedelta(days=now.weekday() + 7)
        return ("range", start, start + timedelta(days=6))
    if s == "下周":
        start = today - timedelta(days=now.weekday() - 7)
        return ("range", start, start + timedelta(days=6))

    # 相对月
    if s in ("本月", "这个月", "本月份"):
        return ("range",) + _month_range(now.year, now.month)
    if s in ("上个月", "上月"):
        y, m = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
        return ("range",) + _month_range(y, m)

    # 相对年
    if s in ("今年", "本年"):
        return ("range", datetime(now.year, 1, 1), datetime(now.year, 12, 31))
    if s == "去年":
        return ("range", datetime(now.year - 1, 1, 1), datetime(now.year - 1, 12, 31))

    # 最近 N 天
    if s in ("最近", "近期"):
        return ("range", today - timedelta(days=14), today)
    m = re.match(r"^(?:最近|近|过去)(\d+)\s*天$", s)
    if m:
        n = max(1, int(m.group(1)))
        return ("range", today - timedelta(days=n - 1), today)

    # 完整日期：YYYY-MM-DD
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if m:
        d = _mk_date(*m.groups())
        return ("day", d) if d else None

    # 完整日期：YYYY年M月D日
    m = re.match(r"^(\d{4})年(\d{1,2})月(\d{1,2})[日号]?$", s)
    if m:
        d = _mk_date(*m.groups())
        return ("day", d) if d else None

    # 完整区间：YYYY-MM-DD 到 YYYY-MM-DD
    m = re.match(r"^(\d{4}-\d{1,2}-\d{1,2})\s*[~至到]\s*(\d{4}-\d{1,2}-\d{1,2})$", s)
    if m:
        a = _mk_date(*re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", m.group(1)).groups())
        b = _mk_date(*re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", m.group(2)).groups())
        if a and b and b >= a:
            return ("range", a, b)
        return None

    # 本月X号到Y号
    m = re.match(r"^本月(\d{1,2})[号日]?\s*[~至到]\s*(\d{1,2})[号日]?$", s)
    if m:
        try:
            a = now.replace(day=int(m.group(1)), hour=0, minute=0, second=0, microsecond=0)
            b = now.replace(day=int(m.group(2)), hour=0, minute=0, second=0, microsecond=0)
        except ValueError:
            return None
        return ("range", a, b) if b >= a else None

    # 本月X号
    m = re.match(r"^本月(\d{1,2})[号日]?$", s)
    if m:
        try:
            return ("day", now.replace(day=int(m.group(1)), hour=0, minute=0, second=0, microsecond=0))
        except ValueError:
            return None

    # 无年份月日：9月20号 / 9月20日 / 9月20（按当年；若明显在未来则回退一年）
    m = re.match(r"^(\d{1,2})月(\d{1,2})[号日]?$", s)
    if m:
        d = _mk_date(now.year, m.group(1), m.group(2))
        if not d:
            return None
        if d > today + timedelta(days=1):      # 明显在未来 → 视为去年
            d = _mk_date(now.year - 1, m.group(1), m.group(2))
        return ("day", d) if d else None

    # 无年份月日区间：9月1号到9月20号
    m = re.match(r"^(\d{1,2})月(\d{1,2})[号日]?\s*[~至到]\s*(\d{1,2})月(\d{1,2})[号日]?$", s)
    if m:
        a = _mk_date(now.year, m.group(1), m.group(2))
        b = _mk_date(now.year, m.group(3), m.group(4))
        if not a or not b:
            return None
        if b > today + timedelta(days=1):      # 跨年回退
            a = _mk_date(now.year - 1, m.group(1), m.group(2))
            b = _mk_date(now.year - 1, m.group(3), m.group(4))
        if not a or not b or b < a:
            return None
        return ("range", a, b)

    # 只有日：20号（按当月；若明显在未来则回退一个月）
    m = re.match(r"^(\d{1,2})[号日]$", s)
    if m:
        day_n = int(m.group(1))
        try:
            d = now.replace(day=day_n, hour=0, minute=0, second=0, microsecond=0)
        except ValueError:
            return None
        if d > today + timedelta(days=1):
            y, mo = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
            d = _mk_date(y, mo, day_n)
        return ("day", d) if d else None

    # 年 / 年月 / 月
    m = re.match(r"^(\d{4}|\d{2})年$", s)
    if m:
        year = int(m.group(1))
        if year < 100:
            year += 2000
        return ("range", datetime(year, 1, 1), datetime(year, 12, 31))
    m = re.match(r"^(\d{4}|\d{2})年(\d{1,2})月$", s)
    if m:
        year = int(m.group(1))
        if year < 100:
            year += 2000
        try:
            return ("range",) + _month_range(year, int(m.group(2)))
        except ValueError:
            return None
    m = re.match(r"^(\d{1,2})月$", s)
    if m:
        try:
            return ("range",) + _month_range(now.year, int(m.group(1)))
        except ValueError:
            return None

    return None


def normalize_time(expr, now=None):
    """把口语时间说法**尽力归一化**成标准格式字符串；转不了返回 None。

    例：
      "9月20号上午"     → "2026-09-20 10:00"
      "昨天"            → "2026-10-22"
      "上个月"          → "2026-09-01~2026-09-30"
      "最近7天"         → "2026-10-17~2026-10-23"
      "上午"（无日期）   → "2026-10-23 10:00"（按今天）

    已经是标准格式的直接原样返回（去掉首尾空白）。
    """
    if not expr:
        return None
    s = str(expr).strip()
    if not s:
        return None

    # 已是标准格式 → 原样返回
    if parse_time_expr(s):
        return s

    now = now or datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # 剥离尾部时段词（"9月20号上午" → "9月20号" + "上午"）
    # 注意：纯时段词（"上午"）也要能剥离，剥离后为空则按今天
    period = None
    for p in _PERIOD_TIME:
        if s == p:
            period = p
            s = ""
            break
        if s.endswith(p):
            period = p
            s = s[:-len(p)].strip()
            break

    # 只有时段、没有日期（"上午"）→ 按今天
    if not s and period:
        return "%s %s" % (today.strftime("%Y-%m-%d"), _PERIOD_TIME[period])

    result = _norm_date_part(s, now, today)
    if not result:
        return None

    if result[0] == "day":
        day = result[1]
        if period:
            return "%s %s" % (day.strftime("%Y-%m-%d"), _PERIOD_TIME[period])
        return day.strftime("%Y-%m-%d")

    # 区间：时段无意义，忽略
    start, end = result[1], result[2]
    return "%s~%s" % (start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))


def resolve_time_str(expr, now=None):
    """口语/标准时间 → (start_dt, end_dt)；转不了返回 None。"""
    std = normalize_time(expr, now)
    if not std:
        return None
    return parse_time_expr(std)


def parse_time_expr(expr):
    """解析**标准格式**时间，返回 (start_datetime, end_datetime)；不合法返回 None。

    只接受三种格式：
      YYYY-MM-DD               单日 → 当天 00:00 至次日 00:00
      YYYY-MM-DD HH:MM[:SS]     精确时刻 → 该时刻至次日 00:00
      YYYY-MM-DD~YYYY-MM-DD     区间 → 起始日 00:00 至结束日次日 00:00（含首尾）

    区间分隔符兼容 `~`、`至`、`到`。
    """
    if not expr:
        return None
    expr = str(expr).strip()

    # 区间：YYYY-MM-DD~YYYY-MM-DD
    m = _STD_RANGE.match(expr)
    if m:
        start = _mk_date(*_STD_DATE.match(m.group(1)).groups())
        end = _mk_date(*_STD_DATE.match(m.group(2)).groups())
        if not start or not end or end < start:
            return None
        return start, end + timedelta(days=1)

    # 精确时刻：YYYY-MM-DD HH:MM[:SS]
    m = _STD_DATETIME.match(expr)
    if m:
        y, mo, d, hh, mm, ss = m.groups()
        day = _mk_date(y, mo, d)
        if not day:
            return None
        try:
            start = day.replace(hour=int(hh), minute=int(mm), second=int(ss or 0))
        except ValueError:
            return None
        return start, day + timedelta(days=1)

    # 单日：YYYY-MM-DD
    m = _STD_DATE.match(expr)
    if m:
        day = _mk_date(*m.groups())
        if not day:
            return None
        return day, day + timedelta(days=1)

    return None


def resolve_time(params, api_name):
    """把 params 里的 `time` 归一化后转成毫秒时间戳。

    查询类接口注入区间 start_time/end_time；记账类注入单点 transaction_time。

    设计原则：
      1. **先尽力归一化**（normalize_time）：口语说法自动转标准格式，
         如"9月20号上午"→"2026-09-20 10:00"、"上个月"→"2026-09-01~2026-09-30"
      2. 归一化后仍不合法则报错，**不静默回退为"今天"**（避免历史账记错日期），
         写 params["_time_parse_failed"]，由调用方拒绝提交并回显标准格式要求
    """
    time_expr = params.get("time")
    if not time_expr:
        _resolve_batch_item_times(params, api_name)
        return ""
    params.pop("time", None)

    std = normalize_time(time_expr)
    parsed = parse_time_expr(std) if std else None

    if not parsed:
        params["_time_parse_failed"] = time_expr
        return "时间无法转换为标准格式：%s" % time_expr

    start_dt, end_dt = parsed
    if api_name == "create_transaction":
        params.setdefault("transaction_time", str(int(start_dt.timestamp() * 1000)))
    else:
        params.setdefault("start_time", str(int(start_dt.timestamp() * 1000)))
        params.setdefault("end_time", str(int(end_dt.timestamp() * 1000)))
    _resolve_batch_item_times(params, api_name)
    return ""


def _resolve_batch_item_times(params, api_name):
    """批量记账（create_batch_transactions）：逐条把 `time` 归一化后转毫秒 transaction_time。

    某条 time 归一化失败时不静默取当前时刻（避免历史账记成今天），
    而是收集到 params["_time_parse_failed"]，由调用方拒绝整批并提示用户。
    无 time 字段的条目按当前时刻处理（用户未指定时间）。
    """
    if api_name != "create_batch_transactions":
        return
    items = params.get("transactions")
    if not isinstance(items, list):
        return
    now_ms = str(int(datetime.now().timestamp() * 1000))
    failed = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        time_expr = item.get("time")
        if not time_expr:
            item.setdefault("transaction_time", now_ms)
            continue
        std = normalize_time(time_expr)
        parsed = parse_time_expr(std) if std else None
        if parsed:
            item["transaction_time"] = str(int(parsed[0].timestamp() * 1000))
        else:
            failed.append({"index": idx, "time": time_expr})
        item.pop("time", None)
    if failed:
        params["_time_parse_failed"] = failed
