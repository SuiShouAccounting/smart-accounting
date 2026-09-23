# -*- coding: utf-8 -*-
"""
time_format.py - 毫秒时间戳 → 可读时间转换（极薄，无业务逻辑）

用途：把网关返回的毫秒时间戳（transaction_time / start_time / end_time）
      转成用户可读的日期时间，供模型在展示前换算。避免模型心算出错。

用法：
    python3 scripts/time_format.py <毫秒时间戳> [时区] [格式]
    例：python3 scripts/time_format.py 1786619026000              # 默认时区，默认格式
        python3 scripts/time_format.py 1786619026000 Asia/Shanghai
        python3 scripts/time_format.py 1786619026000 UTC date     # 仅日期
        python3 scripts/time_format.py 1786619026000 UTC datetime # "YYYY-MM-DD HH:mm"

时区：可选 IANA 时区名（默认取系统/模型环境时区），如 Asia/Shanghai、America/Los_Angeles、UTC。
格式：datetime（默认，YYYY-MM-DD HH:mm:ss）或 date（YYYY-MM-DD）。
依赖：仅 Python 标准库（datetime）。
跨平台：macOS / Linux / Windows 行为一致。
"""
from __future__ import print_function

import datetime
import sys
from datetime import timezone, timedelta

_FALLBACK_OFFSETS = {
    "UTC": 0, "GMT": 0, "Asia/Shanghai": 8, "Hongkong": 8,
    "America/Los_Angeles": -8, "America/New_York": -5,
    "Europe/London": 0, "Europe/Paris": 1, "Asia/Tokyo": 9,
}


def _load_tz(tz_name):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(tz_name)
    except Exception:
        return timezone(timedelta(hours=_FALLBACK_OFFSETS.get(tz_name, 0)))


def main(argv):
    if len(argv) < 2:
        sys.stderr.write("usage: python time_format.py <毫秒时间戳> [时区] [datetime|date]\n")
        return 2

    try:
        ms = int(argv[1])
    except ValueError:
        sys.stderr.write("时间戳解析失败：%s\n" % argv[1])
        return 2

    tz_name = argv[2] if len(argv) >= 3 else "local"
    fmt = argv[3] if len(argv) >= 4 else "datetime"

    seconds = ms / 1000.0
    if tz_name == "local":
        dt = datetime.datetime.fromtimestamp(seconds)
    else:
        dt = datetime.datetime.fromtimestamp(seconds, tz=_load_tz(tz_name))

    if fmt == "date":
        print(dt.strftime("%Y-%m-%d"))
    else:
        print(dt.strftime("%Y-%m-%d %H:%M:%S"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
