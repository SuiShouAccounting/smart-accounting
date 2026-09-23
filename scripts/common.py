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
import time
import urllib.request
from datetime import datetime, timedelta

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_DIR = os.path.join(_BASE_DIR, "config")


def _harden_std_streams():
    """强制 stdout/stderr 以 UTF-8 输出（errors=replace）。

    Windows GBK 控制台下，网关响应里出现 U+FFFD 等无法编码字符时 print 会抛
    UnicodeEncodeError 导致脚本崩溃（实测：某 SXY 账本流水含脏字符，2026-09-20）。
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
        # 本地拦截并给出明确错误（教训：2026-09-20 空 token 被误诊为 token 失效）
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
    # 首次使用上报挂点：业务请求成功后顺带尝试上报 first_use 事件
    # （每个安装只成功上报一次；静默、带退避、任何异常都不影响业务流程）
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


# ---- 自然语言时间解析，产出毫秒时间戳 ----

def _parse_date_token(token):
    token = token.strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", token)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.match(r"^(\d{4})年(\d{1,2})月(\d{1,2})日?$", token)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def parse_time_expr(expr):
    """解析时间表达式，返回 (start_datetime, end_datetime)；无法识别返回 None。

    支持：今天/昨天/前天/本周/上周/本月/上个月/今年/去年/最近/最近N天/近N天/
          YYYY-MM-DD / A到B / A至B / YYYY年 / YYYY年M月 / M月 / 本月X号到Y号
    """
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # 时段后缀：今天晚上 / 昨天早上 等，剥离时段后按基准日 + 时段区间解析
    part = None
    m = re.match(r"^(今天|昨天|前天|今日|昨日)(凌晨|清晨|早上|上午|中午|下午|傍晚|晚上)$", expr)
    if m:
        expr, part = m.group(1), m.group(2)

    if part:
        day_offsets = {"今天": 0, "今日": 0, "昨天": -1, "昨日": -1, "前天": -2}
        day = today_start + timedelta(days=day_offsets.get(expr, 0))
        hour_ranges = {
            "凌晨": (0, 6), "清晨": (5, 8), "早上": (6, 9), "上午": (8, 12),
            "中午": (11, 14), "下午": (13, 18), "傍晚": (17, 20), "晚上": (18, 24),
        }
        h = hour_ranges.get(part)
        if h:
            start_hour, end_hour = h
            end_day = day + timedelta(days=1) if end_hour == 24 else day
            return day.replace(hour=start_hour), end_day.replace(hour=end_hour % 24)

    if expr in ("今天", "今日"):
        return today_start, today_start + timedelta(days=1)
    if expr in ("昨天", "昨日"):
        return today_start - timedelta(days=1), today_start
    if expr == "前天":
        return today_start - timedelta(days=2), today_start - timedelta(days=1)

    if expr in ("本周", "这周", "这个星期"):
        start = today_start - timedelta(days=now.weekday())
        return start, start + timedelta(days=7)
    if expr == "上周":
        start = today_start - timedelta(days=now.weekday() + 7)
        return start, start + timedelta(days=7)

    if expr in ("本月", "这个月"):
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(month=now.month + 1) if now.month < 12 else start.replace(year=now.year + 1, month=1)
        return start, end
    if expr in ("上个月", "上月"):
        start = (now.replace(day=1) - timedelta(days=1)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, end

    if expr in ("今年", "本年"):
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, start.replace(year=now.year + 1)
    if expr == "去年":
        start = now.replace(year=now.year - 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)

    if expr in ("最近", "近期"):
        return today_start - timedelta(days=15), today_start + timedelta(days=1)
    m = re.match(r"^(?:最近|近)(\d+)天$", expr)
    if m:
        n = int(m.group(1))
        return today_start - timedelta(days=n), today_start + timedelta(days=1)

    m = re.match(r"^本月(\d{1,2})号到(\d{1,2})号$", expr)
    if m:
        start = now.replace(day=int(m.group(1)), hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(day=int(m.group(2)), hour=23, minute=59, second=59, microsecond=0)
        return start, end

    m = re.match(r"^(\d{4}-\d{1,2}-\d{1,2})\s*(?:到|至|-)\s*(\d{4}-\d{1,2}-\d{1,2})$", expr)
    if m:
        start = _parse_date_token(m.group(1))
        end_date = _parse_date_token(m.group(2))
        if start and end_date:
            return start, end_date + timedelta(days=1)

    dt = _parse_date_token(expr)
    if dt:
        return dt, dt + timedelta(days=1)

    m = re.match(r"^(\d{4}|\d{2})年$", expr)
    if m:
        year = int(m.group(1))
        if year < 100:
            year += 2000
        return datetime(year, 1, 1), datetime(year + 1, 1, 1)

    m = re.match(r"^(\d{4}|\d{2})年(\d{1,2})月$", expr)
    if m:
        year = int(m.group(1))
        if year < 100:
            year += 2000
        month = int(m.group(2))
        start = datetime(year, month, 1)
        end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
        return start, end
    m = re.match(r"^(\d{1,2})月$", expr)
    if m:
        start = datetime(now.year, int(m.group(1)), 1)
        end = datetime(now.year + 1, 1, 1) if int(m.group(1)) == 12 else datetime(now.year, int(m.group(1)) + 1, 1)
        return start, end

    return None


def resolve_time(params, api_name):
    """若 params 含 time（自然语言），按接口类型转毫秒并注入。

    查询类接口注入区间 start_time/end_time；记账 create_transaction 注入单点 transaction_time。
    无法识别时不报错：查询类按"今天"处理，记账取当前时间。
    """
    time_expr = params.get("time")
    if not time_expr:
        _resolve_batch_item_times(params, api_name)
        return ""
    parsed = parse_time_expr(time_expr)
    start_dt, end_dt = parsed if parsed else (datetime.now(), datetime.now() + timedelta(days=1))
    params.pop("time", None)
    if api_name == "create_transaction":
        ts = start_dt.timestamp() if parsed else datetime.now().timestamp()
        params.setdefault("transaction_time", str(int(ts * 1000)))
    else:
        params.setdefault("start_time", str(int(start_dt.timestamp() * 1000)))
        params.setdefault("end_time", str(int(end_dt.timestamp() * 1000)))
    _resolve_batch_item_times(params, api_name)
    return ""


def _resolve_batch_item_times(params, api_name):
    """批量记账（create_batch_transactions）：逐条把 time（自然语言）转毫秒 transaction_time。

    每条无法识别时取当前时刻；无 time 字段的条目不动。
    """
    if api_name != "create_batch_transactions":
        return
    items = params.get("transactions")
    if not isinstance(items, list):
        return
    now_ms = str(int(datetime.now().timestamp() * 1000))
    for item in items:
        if not isinstance(item, dict):
            continue
        time_expr = item.get("time")
        if not time_expr:
            item.setdefault("transaction_time", now_ms)
            continue
        parsed = parse_time_expr(time_expr)
        if parsed:
            item["transaction_time"] = str(int(parsed[0].timestamp() * 1000))
        else:
            item.setdefault("transaction_time", now_ms)
        item.pop("time", None)
