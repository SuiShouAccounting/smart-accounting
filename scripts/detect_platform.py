#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""detect_platform.py — 通用 AI Agent 平台检测（零依赖，可嵌入任何 SKILL）

检测优先级：
  1. 通用约定变量 AI_AGENT / AGENT（值为已知平台名时，最可信）
  2. 各平台专属标记环境变量（注册表顺序敏感：兼容 shim 在前）
  3. 文件系统等弱信号
  4. 通用变量兜底（任意非空自定义值原样返回，如 codebuddy / trace）
  5. 都没有 → "unknown"

参考资料：
  - Vercel detect-agent (AI_AGENT 提案): https://github.com/vercel/detect-agent
  - agents.md AGENT 提案:               https://github.com/agentsmd/agents.md/issues/136
  - is-ai-agent 检测矩阵:               https://github.com/sdairs/is-ai-agent
"""
import os
import re

# 注册表：(平台名, [标记, ...])，任一标记命中即认定。顺序敏感！
# 标记写法：
#   "VAR"           -> 变量存在即命中
#   "VAR=value"     -> 变量等于该值才命中
#   "VAR~substr"    -> 变量包含该子串才命中
#   "A+B"           -> A 和 B 必须同时命中（allOf）
#   "path:/xxx"     -> 文件/目录存在才命中
_MARKERS = [
    # --- 兼容 shim 优先：这些平台会附带设置别家的标记，先查避免误判 ---
    ("amp",            ["AMP_CURRENT_THREAD_ID"]),               # Amp 会顺带设 CLAUDECODE
    ("qwen-code",      ["QWEN_CODE"]),                           # Gemini CLI 的 fork
    ("kimi-code",      ["KIMI_PLUGIN_ROOT"]),
    ("grok-build",     ["GROK_PLUGIN_ROOT", "GROK_PLUGIN_DATA"]),
    ("cowork",         ["CLAUDE_CODE_IS_COWORK+CLAUDECODE",
                        "CLAUDE_CODE_IS_COWORK+CLAUDE_CODE"]),
    # --- 主流平台专属标记 ---
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
    ("kiro",           ["TERM_PROGRAM~kiro"]),                   # 弱信号：人用 IDE 终端也可能带
    ("amazon-q",       ["AWS_EXECUTION_ENV~AmazonQ-For-CLI"]),
    ("pi",             ["PATH~.pi/agent", "PATH~.pi\\agent"]),
    # --- 自注册（DSH 于 DSH Desktop 2.0.5 实测；TRACE/TRAE/CodeBuddy 欢迎补充实测标记）---
    ("dsh",            ["DSH_HOME", "DSH_SESSION_ID", "DSH_SHELL", "DSH_WEB_URL"]),
]

# 通用约定变量中认可的已知平台名（命中则直接采信 AI_AGENT/AGENT 的值）
_KNOWN_SLUGS = {slug for slug, _ in _MARKERS} | {"devin", "codebuddy", "trace"}

_TRUTHY = {"1", "true", "yes"}


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


def _clean(value: str) -> str:
    """归一化：去版本号后缀（claude-code@2.1 / goose_1.2.3_cli 均可识别）"""
    return re.split(r"[@_]", value.strip(), maxsplit=1)[0].lower() or value.strip().lower()


def detect_platform() -> str:
    env = os.environ
    # 1) 通用约定变量（值为已知平台名时直接采信）
    for generic in ("AI_AGENT", "AGENT"):          # Vercel 提案 / agents.md 提案
        raw = env.get(generic, "").strip()
        if raw and _clean(raw) in _KNOWN_SLUGS:
            return _clean(raw)
    # 2) 平台专属标记（顺序敏感）
    for slug, markers in _MARKERS:
        if any(_hit(m) for m in markers):
            return slug
    # 3) 文件系统弱信号
    if _hit("path:/opt/.devin"):
        return "devin"
    # 4) 通用变量兜底（自定义平台：任意非空命名值）
    for generic in ("AI_AGENT", "AGENT"):
        raw = env.get(generic, "").strip()
        if raw and _clean(raw) not in _TRUTHY:
            return _clean(raw)
    # 5) 未知
    return "unknown"


if __name__ == "__main__":
    print(detect_platform())
