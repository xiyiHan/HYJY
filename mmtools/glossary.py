"""术语规范化。

语音识别对专有名词有两类问题，需要不同的手段：

1. **同音误识别** —— 把"监理"听成"建立/兼任"、"竣工"听成"俊工"、
   "承建"听成"城建"。这类问题靠引擎的热词（hotword）缓解，但热词依赖
   具体引擎支持，且效果不可预期。

2. **写法不一致** —— 同一概念在不同段落写法不同（"建设单位"与"甲方"混用）。
   这类问题热词完全无能为力。

本模块在转写完成后做一次确定性的文本替换，对两个转写引擎都生效。
相比热词的优势是：结果可预期、可审计、可随时调整，且不需要重新转写 ——
改完术语表对已有文字稿重跑即可。

术语表在 config.yaml 的 transcription.glossary 中配置。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import replace

from .config import Config, ConfigError


class GlossaryRule:
    """一条已编译的替换规则。"""

    __slots__ = ("pattern", "target", "origin", "is_regex")

    def __init__(self, pattern: re.Pattern, target: str, origin: str, is_regex: bool):
        self.pattern = pattern
        self.target = target
        self.origin = origin
        self.is_regex = is_regex

    @property
    def label(self) -> str:
        return f"{self.origin} → {self.target}"


def build_rules(conf: dict | None) -> list[GlossaryRule]:
    """把配置编译成替换规则。

    普通替换必须**按长度倒序**应用：否则配置了"监理单位"和"监理"两条时，
    短的那条会先把长词的局部替换掉，长词规则就永远匹配不上。
    """
    if not conf:
        return []

    rules: list[GlossaryRule] = []

    literal = conf.get("replacements") or {}
    if isinstance(literal, dict):
        for src, dst in sorted(literal.items(), key=lambda kv: len(str(kv[0])), reverse=True):
            src, dst = str(src).strip(), str(dst)
            if not src:
                continue
            # 普通替换按字面量处理，转义正则元字符
            rules.append(GlossaryRule(re.compile(re.escape(src)), dst, src, False))

    regex_items = conf.get("regex") or []
    if isinstance(regex_items, list):
        for idx, item in enumerate(regex_items):
            if not isinstance(item, dict):
                continue
            pattern = str(item.get("pattern") or "").strip()
            target = str(item.get("replace") or "")
            if not pattern:
                continue
            try:
                compiled = re.compile(pattern)
            except re.error as exc:
                raise ConfigError(
                    f"术语表的正则规则 #{idx + 1} 无法编译：{pattern}\n原因：{exc}"
                ) from exc
            rules.append(GlossaryRule(compiled, target, pattern, True))

    return rules


def apply_to_result(result, cfg: Config) -> tuple[object, dict]:
    """对转写结果应用术语规范化，返回 (新结果, 统计)。

    只改动文本，不触碰时间戳与说话人，因此不影响与录音的对照。
    """
    conf = cfg.get("transcription", "glossary") or {}
    if not conf or not conf.get("enabled", True):
        return result, {"enabled": False, "count": 0, "hits": {}, "rules": 0}

    rules = build_rules(conf)
    if not rules:
        return result, {"enabled": False, "count": 0, "hits": {}, "rules": 0}

    hits: Counter[str] = Counter()
    new_segments = []

    for seg in result.segments:
        text = seg.text
        for rule in rules:
            text, count = rule.pattern.subn(rule.target, text)
            if count:
                hits[rule.label] += count
        new_segments.append(seg if text == seg.text else replace(seg, text=text))

    total = sum(hits.values())
    stats = {
        "enabled": True,
        "count": total,
        "hits": dict(hits),
        "rules": len(rules),
    }

    if total == 0:
        return result, stats

    updated = replace(result, segments=new_segments, meta={**result.meta, "glossary": stats})
    return updated, stats


def describe_stats(stats: dict) -> str:
    """把统计信息整理成一行可读文本。"""
    if not stats.get("enabled"):
        return "未启用"
    if not stats.get("count"):
        return f"已启用（{stats.get('rules', 0)} 条规则，本次未命中）"
    return f"已启用（{stats.get('rules', 0)} 条规则，本次修正 {stats.get('count')} 处）"
