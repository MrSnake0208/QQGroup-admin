"""Keyword rule validation and matching, shared by the WebUI and message path."""
from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass
from typing import Any

import regex

IMAGE_ID = re.compile(r"[a-f0-9]{64}\.(?:png|jpg|gif|webp)")
MAX_IMAGES = 3
MAX_RESPONSES = 20
TEXT_LIMIT = 1000


@dataclass(frozen=True)
class KeywordReply:
    text: str
    images: tuple[str, ...] = ()
    send_mode: str = "combined"


def normalize_rule(item: dict[str, Any]) -> dict[str, Any]:
    """Keep the legacy text shape when no rich responses were supplied."""
    def text(value: Any, label: str, limit: int) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{label}必须是文字")
        value = value.strip()
        if len(value) > limit or any(ord(c) < 32 and c not in "\r\n\t" for c in value):
            raise ValueError(f"{label}过长或包含非法控制字符（上限 {limit} 字）")
        return value

    mode = item.get("match_type", "contains")
    if mode not in {"contains", "exact", "regex"}:
        raise ValueError("匹配方式必须为 contains、exact 或 regex")
    trigger = item.get("trigger", "any")
    if trigger not in {"any", "command", "auto"}:
        raise ValueError("触发方式必须为 any、command 或 auto")
    send_mode = item.get("send_mode", "combined")
    if send_mode not in {"combined", "text_first"}:
        raise ValueError("图文排列必须为 combined 或 text_first")
    sensitive = item.get("case_sensitive", False)
    enabled = item.get("enabled", True)
    if not isinstance(sensitive, bool) or not isinstance(enabled, bool):
        raise ValueError("启用和区分大小写必须是布尔值")
    raw = item.get("keywords", item.get("keyword", ""))
    if isinstance(raw, list):
        raw = "\n".join(text(k, "关键词", 256) for k in raw)
    raw = text(raw, "关键词", 2100)
    if mode == "regex":
        if not raw or len(raw) > 256:
            raise ValueError("正则表达式必须为 1–256 字，只填写一个表达式")
        try:
            regex.compile(raw, 0 if sensitive else regex.IGNORECASE)
        except regex.error as exc:
            raise ValueError(f"正则表达式无效：{exc}") from exc
        keywords = [raw]
    else:
        keywords = list(dict.fromkeys(k.strip() for k in re.split(r"[,，;；\r\n]+", raw) if k.strip()))
        if not keywords:
            raise ValueError("关键词不能为空")
        if len(keywords) > 20 or any(len(k) > 100 for k in keywords):
            raise ValueError("需要 1–20 个关键词，每个最多 100 字")
    logic = item.get("condition_logic", item.get("keyword_logic", item.get("logic", "any")))
    if logic not in {"all", "any"}:
        raise ValueError("关键词组合必须为 all 或 any")
    if mode == "exact" and logic == "all" and len(keywords) > 1:
        raise ValueError("完全匹配不能与多个关键词的全部满足组合使用")
    result = {
        "__template_key": "keyword_reply",
        "name": text(item.get("name", ""), "规则名称", 80),
        "keywords": "\n".join(keywords), "condition_logic": logic,
        "match_type": mode, "enabled": enabled,
        "reply": text(item.get("reply", ""), "回复内容", TEXT_LIMIT),
    }
    if "send_mode" in item:
        result["send_mode"] = send_mode
    if "trigger" in item:
        result["trigger"] = trigger
    if "case_sensitive" in item:
        result["case_sensitive"] = sensitive
    responses = item.get("responses")
    if responses is not None and responses != []:
        if not isinstance(responses, list) or not 1 <= len(responses) <= MAX_RESPONSES:
            raise ValueError(f"每条规则需要 1–{MAX_RESPONSES} 套回复")
        result["responses"] = []
        for response in responses:
            if not isinstance(response, dict):
                raise ValueError("回复格式错误")
            content = text(response.get("text", ""), "回复内容", TEXT_LIMIT)
            images = response.get("images", [])
            if not isinstance(images, list) or len(images) > MAX_IMAGES:
                raise ValueError(f"每套回复最多 {MAX_IMAGES} 张图片")
            if any(not isinstance(i, str) or not IMAGE_ID.fullmatch(i) for i in images):
                raise ValueError("图片标识无效，请通过后台上传图片")
            if not content and not images:
                raise ValueError("每套回复至少填写文字或添加一张图片")
            result["responses"].append({"text": content, "images": list(images)})
        # Preserve the legacy field for display, but responses owns rich content.
        result["reply"] = result["responses"][0]["text"]
    elif not result["reply"]:
        raise ValueError("回复内容不能为空")
    return result


def keyword_reply_for_message(
    text: str, group_openid: str, group_rules: object, global_rules: object,
    *, is_command: bool = False,
) -> KeywordReply | None:
    message = text.strip()
    if not message:
        return None
    # A shared budget prevents many hostile patterns from blocking one event.
    deadline = time.monotonic() + 0.05
    for rules, global_scope in ((group_rules, False), (global_rules, True)):
        if not isinstance(rules, list):
            continue
        for item in rules[:100]:
            if not isinstance(item, dict) or not item.get("enabled", True):
                continue
            if global_scope:
                targets = item.get("group_openids", [])
                if isinstance(targets, str):
                    targets = re.split(r"[\s,，;；]+", targets.strip())
                if isinstance(targets, list) and any(targets) and "*" not in targets and group_openid not in targets:
                    continue
            try:
                rule = normalize_rule(item)
            except (TypeError, ValueError):
                continue
            trigger = rule.get("trigger", "any")
            if (trigger == "command" and not is_command) or (trigger == "auto" and is_command):
                continue
            subject = message.split()[0] if trigger == "command" else message
            capture = None
            sensitive = rule.get("case_sensitive", False)
            if rule["match_type"] == "regex":
                if len(subject) > 4096 or time.monotonic() >= deadline:
                    continue
                try:
                    matcher = regex.fullmatch if trigger == "command" else regex.search
                    match = matcher(rule["keywords"], subject, flags=0 if sensitive else regex.IGNORECASE,
                                    timeout=max(0.001, deadline - time.monotonic()))
                except (TimeoutError, regex.error):
                    continue
                matched = match is not None
                if match and match.re.groups:
                    capture = match.group(1) or ""
            else:
                subject = subject if sensitive else subject.casefold()
                keywords = rule["keywords"].splitlines()
                keywords = keywords if sensitive else [k.casefold() for k in keywords]
                checks = [subject == k if rule["match_type"] == "exact" else k in subject for k in keywords]
                matched = all(checks) if rule["condition_logic"] == "all" else any(checks)
            if matched:
                response = random.choice(rule.get("responses", [{"text": rule["reply"], "images": []}]))
                content = response["text"]
                if capture is not None:
                    content = re.sub(r"\{1\}|XX", lambda _: capture, content)
                if len(content) > TEXT_LIMIT or (not content.strip() and not response["images"]):
                    continue
                return KeywordReply(content, tuple(response["images"]), rule.get("send_mode", "combined"))
    return None
