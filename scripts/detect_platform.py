#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""detect_platform.py — 通用 AI Agent 平台检测（零依赖，可嵌入任何 SKILL）

判断当前进程运行在哪个 AI 工作平台（如 DSH、Claude Code、Cursor、Codex 等），
返回平台名；无法判断时返回 "unknown"。

用法：
    python3 detect_platform.py      # 直接打印平台名
    import detect_platform          # detect_platform.detect_platform()

说明：
    - 只读取环境变量（及检查 /opt/.devin 是否存在），不联网、不写磁盘
    - 新增平台：优先在 _MARKERS 追加精确标记；环境变量细节不明时，
      在 _SLUGS 追加一行名字即可自动识别

参考资料：
  - Vercel detect-agent (AI_AGENT 提案): https://github.com/vercel/detect-agent
  - agents.md AGENT 提案:               https://github.com/agentsmd/agents.md/issues/136
  - is-ai-agent 检测矩阵:               https://github.com/sdairs/is-ai-agent
"""
import os
import re

# 精确标记表：(平台名, [标记, ...])，首个命中即返回，顺序敏感！
# 标记写法：
#   "VAR"           -> 变量存在即命中
#   "VAR=value"     -> 变量等于该值才命中
#   "VAR~substr"    -> 变量包含该子串才命中
#   "A+B"           -> A 和 B 必须同时命中（allOf）
#   "path:/xxx"     -> 文件/目录存在才命中
_MARKERS = [
    # 部分平台会顺带设置别家的标记，这些需优先判断
    ("amp",            ["AMP_CURRENT_THREAD_ID"]),
    ("qwen-code",      ["QWEN_CODE"]),
    ("kimi-code",      ["KIMI_PLUGIN_ROOT"]),
    ("grok-build",     ["GROK_PLUGIN_ROOT", "GROK_PLUGIN_DATA"]),
    ("cowork",         ["CLAUDE_CODE_IS_COWORK+CLAUDECODE",
                        "CLAUDE_CODE_IS_COWORK+CLAUDE_CODE"]),
    # 主流平台专属标记
    ("claude-code",    ["CLAUDECODE", "CLAUDE_CODE", "CLAUDE_CODE_ENTRYPOINT",
                        "CLAUDE_CODE_SESSION_ID"]),
    ("cursor",         ["CURSOR_TRACE_ID", "CURSOR_AGENT", "CURSOR_SANDBOX",
                        "CURSOR_EXTENSION_HOST_ROLE=agent-exec"]),
    ("gemini-cli",     ["GEMINI_CLI"]),
    ("codex",          ["CODEX_SANDBOX", "CODEX_THREAD_ID", "CODEX_CI",
                        "CODEX_SANDBOX_NETWORK_DISABLED"]),
    ("antigravity",    ["ANTIGRAVITY_AGENT", "ANTIGRAVITY_CLI_ALIAS"]),
    ("augment",        ["AUGMENT_AGENT"]),
    ("cline",          ["CLINE_ACTIVE"]),
    ("roo-code",       ["ROO_ACTIVE"]),
    ("crush",          ["CRUSH"]),
    ("iflow-cli",      ["IFLOW_CLI"]),
    ("opencode",       ["OPENCODE", "OPENCODE_CLIENT", "OPENCODE_PID"]),
    ("trae",           ["TRAE_AI_SHELL_ID", "TRAE_RUNTIME",
                        "TRAE_STATIC_CLIENT_TYPE", "TRAE_CONFIG_CHANNEL",
                        "TRAE_SANDBOX_CLI_PATH"]),
    ("goose",          ["GOOSE_TERMINAL", "GOOSE_PROVIDER"]),
    ("replit",         ["REPL_ID"]),
    ("github-copilot", ["COPILOT_AGENT_SESSION_ID", "COPILOT_MODEL",
                        "COPILOT_ALLOW_ALL"]),
    ("junie",          ["JUNIE_DATA", "JUNIE_SHIM_PATH"]),
    ("openclaw",       ["OPENCLAW_SHELL"]),
    ("kiro",           ["TERM_PROGRAM~kiro"]),
    ("amazon-q",       ["AWS_EXECUTION_ENV~AmazonQ-For-CLI"]),
    ("pi",             ["PATH~.pi/agent", "PATH~.pi\\agent"]),
    ("dsh",            ["DSH_HOME", "DSH_SESSION_ID", "DSH_SHELL", "DSH_WEB_URL"]),
    ("devin",          ["path:/opt/.devin"]),
]

# 平台名单：「slug 归一化形式 -> 展示名」，用于动态识别。
# 新增平台只需在此追加一行名字，无需了解其环境变量细节。
_SLUGS = {
    "claudecode":  "claude-code",
    "cursor":      "cursor",
    "gemini":      "gemini-cli",
    "codex":       "codex",
    "antigravity": "antigravity",
    "augment":     "augment",
    "cline":       "cline",
    "roo":         "roo-code",
    "crush":       "crush",
    "iflow":       "iflow-cli",
    "opencode":    "opencode",
    "trae":        "trae",
    "goose":       "goose",
    "copilot":     "github-copilot",
    "junie":       "junie",
    "openclaw":    "openclaw",
    "kiro":        "kiro",
    "amazonq":     "amazon-q",
    "amp":         "amp",
    "qwen":        "qwen-code",
    "kimi":        "kimi-code",
    "grok":        "grok-build",
    "cowork":      "cowork",
    "dsh":         "dsh",
    "devin":       "devin",
    "codebuddy":   "codebuddy",
    "trace":       "trace",
    "workbuddy":   "workbuddy",
}
_SLUG_ITEMS = list(_SLUGS.items())

# "trace" 为通用词（TRACE_* 变量在各类应用中很常见），不依据变量名识别
_NAME_EXCLUDED = {"trace"}

# AI_AGENT / AGENT 变量中认可的平台名（命中则直接采信其值）
_KNOWN_SLUGS = set(_SLUGS.values()) | {slug for slug, _ in _MARKERS}
_TRUTHY = {"1", "true", "yes"}
_VALUE_VARS = ("TERM_PROGRAM", "AWS_EXECUTION_ENV")  # 取值中常见平台名的描述性变量
# 通用基础设施噪声变量（非平台专属），如 W3C Trace Context 标准头
_NOISE_VARS = {"traceparent", "tracestate"}
# 应用自描述变量名片段（用于区分外层产品与内嵌引擎）
_HOST_SIGNALS = ("applicationname", "productname", "appname", "appversion")

_norm_token = re.compile(r"[^A-Za-z0-9]+")
_strip_nonalnum = re.compile(r"[^a-z0-9]")


def _normalize(name: str) -> str:
    """归一化变量名：仅保留小写字母数字。"""
    return _strip_nonalnum.sub("", name.lower())


def _hit(marker: str) -> bool:
    if marker.startswith("path:"):
        return os.path.exists(marker[5:])
    if "~" in marker:
        name, _, needle = marker.partition("~")
        return needle.lower() in os.environ.get(name, "").lower()
    if "=" in marker:
        name, _, value = marker.partition("=")
        return os.environ.get(name, "") == value
    if "+" in marker:
        return all(_hit(m) for m in marker.split("+"))
    return bool(os.environ.get(marker))


def _prefix_at_boundary(name: str, slug_len: int) -> bool:
    """判断归一化前缀在原始变量名中对应的末端是否落在词边界上。

    例：CLAUDE_CODE_SESSION_ID 的前 10 个字母数字恰为 CLAUDE_CODE，其后是
    "_" -> 边界成立；COWORKING 的前 6 个字母数字是 COWORK，其后是 "I"
    -> 不成立。
    """
    acc = 0
    for i, ch in enumerate(name):
        if ch.isalnum():
            acc += 1
            if acc == slug_len:
                return i + 1 >= len(name) or not name[i + 1].isalnum()
    return False


def _match_slug(name, norm, first_token, token_set, norm_value, allow_value_scan) -> set:
    """对一个环境变量，返回其名称/取值能动态匹配到的全部 slug（归一化形式）。

    取值扫描仅对 _VALUE_VARS 中的描述性变量开启，避免任意变量取值里的
    普通单词（如 "root" 含 "roo"）造成误判。
    """
    if norm in _NOISE_VARS:
        return set()
    hits = set()
    for slug, _display in _SLUG_ITEMS:
        n = len(slug)
        # 描述性变量取值扫描
        if allow_value_scan and n >= 3 and slug in norm_value:
            hits.add(slug)
            continue
        if slug in _NAME_EXCLUDED:
            continue
        # 命名空间前缀：>=6 字符，且前缀末端须为词边界
        if n >= 6 and norm.startswith(slug) and _prefix_at_boundary(name, n):
            hits.add(slug)
        # 首词完全匹配（对短名最可靠）
        elif first_token == slug:
            hits.add(slug)
        # 整词匹配（slug 以完整词形出现在变量名中）
        elif slug in token_set:
            hits.add(slug)
    return hits


def _scan_env():
    """动态扫描全部环境变量，返回各平台的命中统计。"""
    stats = {}
    for name, value in os.environ.items():
        norm = _normalize(name)
        if norm in _NOISE_VARS:
            continue
        tokens = _norm_token.split(name)
        token_set = set()
        first_token = ""
        for t in tokens:
            t = t.lower()
            if t:
                if not first_token:
                    first_token = t
                token_set.add(t)
        host = any(s in norm for s in _HOST_SIGNALS)
        allow_value_scan = name in _VALUE_VARS
        norm_value = _normalize(value) if allow_value_scan else ""
        for slug in _match_slug(name, norm, first_token, token_set, norm_value, allow_value_scan):
            entry = stats.setdefault(slug, [0, False])
            entry[0] += 1
            entry[1] = entry[1] or host
    return stats


def _pick(stats) -> str:
    """按消歧顺序选出最终平台展示名。"""
    best = None  # (非host=0/host=1 升序即host优先, slug长度, 展示名)
    for slug, (_, host) in stats.items():
        key = (0 if host else 1, len(slug), _SLUGS[slug])
        if best is None or key < best:
            best = key
    return best[2] if best else ""


def _clean(value: str) -> str:
    """归一化：去版本号后缀（claude-code@2.1 / goose_1.2.3_cli 均可识别）"""
    return re.split(r"[@_]", value.strip(), maxsplit=1)[0].lower() or value.strip().lower()


def detect_platform() -> str:
    env = os.environ
    # 1) AI_AGENT / AGENT 显式声明了已知平台名时直接采信
    for generic in ("AI_AGENT", "AGENT"):
        raw = env.get(generic, "").strip()
        if raw and _clean(raw) in _KNOWN_SLUGS:
            return _clean(raw)
    # 2) 精确标记表（首个命中即返回）
    for slug, markers in _MARKERS:
        if any(_hit(m) for m in markers):
            return slug
    # 3) 动态识别（新平台兜底，含消歧）
    hit = _pick(_scan_env())
    if hit:
        return hit
    # 4) AI_AGENT / AGENT 的自定义值原样返回
    for generic in ("AI_AGENT", "AGENT"):
        raw = env.get(generic, "").strip()
        if raw and _clean(raw) not in _TRUTHY:
            return _clean(raw)
    # 5) 未知
    return "unknown"


if __name__ == "__main__":
    print(detect_platform())
