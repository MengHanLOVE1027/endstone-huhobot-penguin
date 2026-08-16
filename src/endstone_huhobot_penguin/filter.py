"""
过滤：正则（filter-regex）+ 敏感词（内置默认 + sensitive-words/*.txt 首检），
AI 二审走 OpenAI 兼容 /chat/completions（配齐 audit.base-url + audit.api-key 时），失败回退本地结果。
对应 Java 版 Utilities.filterTextByRegex + SensitiveFilter。
"""

import json
import os
import re
import urllib.request
import urllib.error

from .logger import log

DEFAULT_WORDS = ["傻逼", "操你", "色情", "反动", "赌博"]


def load_sensitive_words(folder):
    words = []
    try:
        entries = os.listdir(folder)
    except OSError:
        return words
    for name in entries:
        if not name.endswith(".txt"):
            continue
        try:
            with open(os.path.join(folder, name), "r", encoding="utf-8") as f:
                content = f.read()
        except OSError:
            continue
        for line in content.splitlines():
            word = line.strip()
            if word and not word.startswith("#"):
                words.append(word)
    return words


def filter_text_by_regex(text, patterns):
    out = text
    for pattern in patterns or []:
        try:
            out = re.sub(pattern, "*", out, flags=re.IGNORECASE)
        except re.error:
            # 忽略配置中的非法正则
            pass
    return out


def replace_words(text, words):
    out = text
    for word in words:
        if not word:
            continue
        try:
            out = re.sub(re.escape(word), "*" * len(word), out, flags=re.IGNORECASE)
        except re.error:
            pass
    return out


def _ai_review(value, base_url, api_key, model):
    url = base_url.strip().rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": model or "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "你是敏感词二审工具，只输出替换敏感内容后的完整原文。"},
            {"role": "user", "content": value},
        ],
        "temperature": 0.1,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read().decode("utf-8")
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError("AI 二审请求失败：" + str(e))

    try:
        parsed = json.loads(data)
        content = (parsed.get("choices") or [{}])[0].get("message", {}).get("content")
        return content.strip() if content else value
    except (ValueError, IndexError, AttributeError) as e:
        raise RuntimeError("AI 二审响应解析失败：" + str(e))


def audit(value, cfg, root_dir):
    """整体过滤入口（对齐 Java auditText：正则 → 敏感词首检 → 命中且配齐时 AI 二审全量重写）。"""
    value = str(value or "")
    regex_patterns = cfg.get_list("filter-regex")
    words = list(DEFAULT_WORDS)
    words.extend(load_sensitive_words(os.path.join(root_dir, "sensitive-words")))
    distinct_words = list(dict.fromkeys(words))

    local = replace_words(filter_text_by_regex(value, regex_patterns), distinct_words)
    base_url = cfg.get_string("audit.base-url", "")
    api_key = cfg.get_string("audit.api-key", "")
    if local == value or not base_url or not api_key:
        return local

    try:
        result = _ai_review(value, base_url, api_key, cfg.get_string("audit.model", "gpt-4o-mini"))
        return result if (result and result.strip()) else local
    except Exception as e:
        log.warning("AI 二审失败，回退本地过滤：" + str(e))
        return local
