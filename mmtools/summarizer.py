"""云端大模型客户端（纪要生成）。

所有平台统一走 OpenAI 兼容协议（/chat/completions），因此：
  - 切换平台 = 改 config.yaml 的 llm.provider 一行
  - 新增平台 = 在 providers 段追加一段配置，无需改动本文件
  - 接入单位内网自建大模型 = 用 custom 平台填内网 base_url 即可

刻意不使用官方 SDK：各家 SDK 版本迭代频繁且协议细节不一致，
直接发 HTTP 请求反而更稳定，也便于在报错时把原始响应暴露出来。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

import requests

from . import prompts
from .config import Config, ConfigError


class LLMError(RuntimeError):
    """云端调用失败，带上可操作的排查建议。"""


@dataclass
class LLMReply:
    content: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    elapsed: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def parse_json_reply(content: str) -> dict:
    """从模型回复中解析 JSON。

    模型有时会裹一层 Markdown 代码围栏，或在 JSON 前后加一句说明，
    这里做容错提取，而不是直接 json.loads 整个回复。
    """
    text = (content or "").strip()

    # 去掉 Markdown 代码围栏
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    # 退一步：截取第一个 { 到最后一个 } 之间的内容
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start:end + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError as exc:
            raise LLMError(
                f"模型返回的内容不是合法 JSON，解析失败：{exc}\n"
                f"原始内容前 300 字符：\n{text[:300]}"
            ) from exc

    raise LLMError(f"模型未返回 JSON 结构。原始内容前 300 字符：\n{text[:300]}")


def _required_form_fields(data: dict) -> list[str]:
    return ["intro", "items", "meeting_type", "topic"]


def normalize_form_data(data: dict) -> dict:
    """补齐缺失字段并做类型规整，避免下游 KeyError。"""
    out = dict(data or {})

    for key in (
        "project", "meeting_no", "meeting_type", "topic", "location",
        "time", "host", "recorder", "publish_date",
        "owner_unit", "builder_unit", "supervisor_unit", "intro",
    ):
        value = out.get(key)
        if value is None:
            out[key] = ""
        elif not isinstance(value, str):
            out[key] = str(value)

    if not isinstance(out.get("items"), list):
        out["items"] = [str(out["items"])] if out.get("items") else []
    out["items"] = [str(x) for x in out["items"] if str(x).strip()]

    if not isinstance(out.get("attendees"), list):
        out["attendees"] = []
    cleaned_attendees = []
    for item in out["attendees"]:
        if isinstance(item, dict):
            people = item.get("people")
            if isinstance(people, str):
                people = [p for p in re.split(r"[,，、\s]+", people) if p]
            cleaned_attendees.append(
                {"unit": str(item.get("unit") or ""), "people": [str(p) for p in (people or [])]}
            )
        elif isinstance(item, str):
            cleaned_attendees.append({"unit": item, "people": []})
    out["attendees"] = cleaned_attendees

    if not isinstance(out.get("signatories"), dict):
        out["signatories"] = {}

    # 会议类型必须是五个选项之一，否则复选框勾不上
    allowed = {"例会", "专题会", "启动会", "评审会", "其他"}
    meeting_type = out["meeting_type"].strip()
    out["meeting_type"] = meeting_type if meeting_type in allowed else (
        "其他" if meeting_type else ""
    )
    return out


class CloudLLM:
    """可配置的云端大模型客户端。"""

    def __init__(self, cfg: Config, provider: str | None = None):
        self.cfg = cfg
        self.provider_key, self.provider_conf = cfg.provider(provider)
        self.base_url = (self.provider_conf.get("base_url") or "").rstrip("/")

        # 允许用配置或环境变量覆盖模型名，方便临时切换
        import os

        env_model = os.environ.get("MM_LLM_MODEL", "").strip()
        self.model = env_model or (self.provider_conf.get("model") or "").strip()

        self.temperature = float(cfg.get("llm", "temperature", default=0.3))
        self.max_tokens = int(cfg.get("llm", "max_tokens", default=8192))
        self.timeout = int(cfg.get("llm", "timeout", default=300))
        self.retries = int(cfg.get("llm", "retries", default=3))
        self.verify_ssl = bool(cfg.get("llm", "verify_ssl", default=True))
        self.extra_headers: dict[str, str] = self.provider_conf.get("extra_headers") or {}

        if not self.base_url:
            raise ConfigError(
                f"平台「{self.provider_conf.get('label', self.provider_key)}」"
                f"未配置 base_url，请在 config.yaml 的 providers.{self.provider_key} 中补全。"
            )
        if not self.model:
            raise ConfigError(
                f"平台「{self.provider_conf.get('label', self.provider_key)}」未配置 model。"
            )

    # ---------- 基础信息 ----------

    @property
    def label(self) -> str:
        return self.provider_conf.get("label", self.provider_key)

    @property
    def compliance(self) -> str:
        return self.provider_conf.get("compliance", "（未注明）")

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def describe(self) -> str:
        return f"{self.label} / {self.model}"

    # ---------- 底层调用 ----------

    def _headers(self, api_key: str) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        headers.update(self.extra_headers)
        return headers

    def chat(self, messages: list[dict[str, str]], max_tokens: int | None = None) -> LLMReply:
        api_key = self.cfg.require_api_key(self.provider_key)
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
            "stream": False,
        }

        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            started = time.time()
            try:
                resp = requests.post(
                    self.endpoint,
                    headers=self._headers(api_key),
                    json=payload,
                    timeout=self.timeout,
                    verify=self.verify_ssl,
                    proxies=_resolve_proxies(),
                )
            except requests.exceptions.Timeout as exc:
                last_error = LLMError(
                    f"调用超时（{self.timeout} 秒）。若文字稿很长，可在 config.yaml 中"
                    f"调大 llm.timeout，或调小 llm.chunk_threshold 以启用分段摘要。"
                )
                _sleep_backoff(attempt, self.retries, reason="超时")
                continue
            except requests.exceptions.SSLError as exc:
                raise LLMError(
                    f"SSL 校验失败：{exc}\n"
                    f"若处于单位内网或使用了抓包代理，可在 config.yaml 中设置 "
                    f"llm.verify_ssl: false 后重试。"
                ) from exc
            except requests.exceptions.ConnectionError as exc:
                raise LLMError(
                    f"无法连接到 {self.endpoint}：{exc}\n"
                    f"请检查网络、DNS 与代理设置。"
                ) from exc

            elapsed = time.time() - started

            if resp.status_code == 200:
                return self._parse(resp, elapsed)

            message = self._explain_http_error(resp)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < self.retries:
                last_error = LLMError(message)
                _sleep_backoff(attempt, self.retries, reason=f"HTTP {resp.status_code}")
                continue
            raise LLMError(message)

        raise last_error or LLMError("调用失败，且未能获取具体原因。")

    def _parse(self, resp: requests.Response, elapsed: float) -> LLMReply:
        try:
            data = resp.json()
        except ValueError as exc:
            raise LLMError(
                f"返回内容不是合法 JSON，可能是被网关或代理拦截。原始响应前 300 字符：\n"
                f"{resp.text[:300]}"
            ) from exc

        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            detail = err.get("message") if isinstance(err, dict) else str(err)
            raise LLMError(f"服务端返回错误：{detail}")

        choices = (data or {}).get("choices") or []
        if not choices:
            raise LLMError(f"响应中没有 choices 字段。原始响应：\n{json.dumps(data, ensure_ascii=False)[:400]}")

        message = choices[0].get("message") or {}
        content = (message.get("content") or "").strip()
        if not content:
            reasoning = (message.get("reasoning_content") or "").strip()
            finish = choices[0].get("finish_reason")
            if finish == "length":
                raise LLMError(
                    "模型输出被 max_tokens 截断且未产出正文。请调大 config.yaml 中的 llm.max_tokens。"
                )
            if reasoning:
                raise LLMError(
                    "模型只返回了思维链，没有返回正文。请确认所选模型名正确"
                    "（例如 DeepSeek 的对话模型应使用 deepseek-chat）。"
                )
            raise LLMError("模型返回了空内容。")

        usage = (data or {}).get("usage") or {}
        return LLMReply(
            content=content,
            model=(data or {}).get("model", self.model),
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
            elapsed=elapsed,
        )

    def _explain_http_error(self, resp: requests.Response) -> str:
        code = resp.status_code
        snippet = resp.text[:400].replace("\n", " ")
        head = f"[HTTP {code}] 调用 {self.label} 失败。"

        if code == 401:
            return (
                f"{head} 认证失败 —— API Key 无效、已过期或未生效。\n"
                f"当前密钥来源：{self.cfg.api_key_source(self.provider_key)}\n"
                f"服务端返回：{snippet}"
            )
        if code == 403:
            return (
                f"{head} 无访问权限 —— 密钥有效但未开通该模型，或账号欠费。\n"
                f"服务端返回：{snippet}"
            )
        if code == 404:
            return (
                f"{head} 接口地址或模型名不存在。\n"
                f"当前 base_url：{self.base_url}\n 当前 model：{self.model}\n"
                f"请核对 providers.{self.provider_key} 中的这两项配置。\n"
                f"服务端返回：{snippet}"
            )
        if code == 429:
            return f"{head} 触发限流或余额不足。稍后重试，或检查账户余额。\n服务端返回：{snippet}"
        if code == 400:
            return (
                f"{head} 请求被拒绝。常见原因：model 名不被该平台识别、"
                f"单次输入超出上下文上限、max_tokens 超过模型限制。\n"
                f"当前 model：{self.model}，max_tokens：{self.max_tokens}\n"
                f"服务端返回：{snippet}"
            )
        if 500 <= code < 600:
            return f"{head} 服务端异常，通常可稍后重试。\n服务端返回：{snippet}"
        return f"{head}\n服务端返回：{snippet}"

    # ---------- 连通性自检 ----------

    def probe(self) -> str:
        """用极小的请求验证密钥与端点是否可用。"""
        reply = self.chat(
            [{"role": "user", "content": "回复两个字：就绪"}],
            max_tokens=16,
        )
        return reply.content

    # ---------- 纪要生成 ----------

    def summarize(
        self,
        transcript_text: str,
        meeting_title: str = "",
        extra_notes: str = "",
    ) -> tuple[str, dict[str, Any]]:
        """生成纪要。超长文字稿自动走分段提炼再汇总。"""
        template = self.cfg.get("output", "template", default="supervision")
        system_prompt = prompts.get_system_prompt(template)
        threshold = int(self.cfg.get("llm", "chunk_threshold", default=24000))

        stats: dict[str, Any] = {
            "provider": self.provider_key,
            "model": self.model,
            "template": template,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "chunked": False,
            "chunks": 1,
        }

        if len(transcript_text) <= threshold:
            user_prompt = prompts.build_user_prompt(
                transcript_text, meeting_title, extra_notes
            )
            reply = self.chat(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ]
            )
            stats["prompt_tokens"] = reply.prompt_tokens
            stats["completion_tokens"] = reply.completion_tokens
            return reply.content, stats

        # 长文字稿：分段提炼 → 汇总成稿
        chunks = prompts.split_for_chunks(transcript_text, threshold)
        stats["chunked"] = True
        stats["chunks"] = len(chunks)

        extracted: list[str] = []
        for idx, chunk in enumerate(chunks, start=1):
            reply = self.chat(
                [
                    {"role": "system", "content": prompts.CHUNK_SYSTEM},
                    {
                        "role": "user",
                        "content": prompts.CHUNK_USER.format(
                            index=idx, total=len(chunks), chunk=chunk
                        ),
                    },
                ],
                max_tokens=min(self.max_tokens, 4096),
            )
            extracted.append(f"### 第 {idx} 段要点\n{reply.content}")
            stats["prompt_tokens"] += reply.prompt_tokens
            stats["completion_tokens"] += reply.completion_tokens

        merged = "\n\n".join(extracted)
        head = prompts.build_user_prompt(merged, meeting_title, extra_notes)
        final = self.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompts.REDUCE_USER + "\n\n" + head},
            ]
        )
        stats["prompt_tokens"] += final.prompt_tokens
        stats["completion_tokens"] += final.completion_tokens
        return final.content, stats

    # ---------- 表单模式（用于填充单位正式模板）----------

    def summarize_form(
        self,
        transcript_text: str,
        known: dict | None = None,
    ) -> tuple[dict, dict]:
        """生成结构化纪要数据，用于填充单位模板。

        与 summarize 的区别：输出是 JSON 而非 Markdown，字段与模板一一对应。
        超过阈值的长文字稿同样走分段提炼，但最终汇总阶段要求输出 JSON。
        """
        system_prompt = prompts.get_system_prompt("supervision_form")
        threshold = int(self.cfg.get("llm", "chunk_threshold", default=24000))

        stats: dict[str, Any] = {
            "provider": self.provider_key,
            "model": self.model,
            "template": "supervision_form",
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "chunked": False,
            "chunks": 1,
        }

        if len(transcript_text) <= threshold:
            reply = self.chat(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompts.build_form_user_prompt(transcript_text, known)},
                ]
            )
            stats["prompt_tokens"] = reply.prompt_tokens
            stats["completion_tokens"] = reply.completion_tokens
            return normalize_form_data(parse_json_reply(reply.content)), stats

        # 长文字稿：先分段提炼要点，再据此生成结构化数据
        chunks = prompts.split_for_chunks(transcript_text, threshold)
        stats["chunked"] = True
        stats["chunks"] = len(chunks)

        extracted: list[str] = []
        for idx, chunk in enumerate(chunks, start=1):
            reply = self.chat(
                [
                    {"role": "system", "content": prompts.CHUNK_SYSTEM},
                    {
                        "role": "user",
                        "content": prompts.CHUNK_USER.format(
                            index=idx, total=len(chunks), chunk=chunk
                        ),
                    },
                ],
                max_tokens=min(self.max_tokens, 4096),
            )
            extracted.append(f"### 第 {idx} 段要点\n{reply.content}")
            stats["prompt_tokens"] += reply.prompt_tokens
            stats["completion_tokens"] += reply.completion_tokens

        merged = "\n\n".join(extracted)
        note = (
            "以下内容是从同一场会议不同片段分别提炼出的要点，按时间顺序排列。"
            "请据此按前述 JSON 结构输出会议纪要数据，不要引入要点中未出现的信息。"
        )
        known_with_note = dict(known or {})
        known_with_note["notes"] = (known_with_note.get("notes") or "") + " " + note
        reply = self.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompts.build_form_user_prompt(merged, known_with_note)},
            ]
        )
        stats["prompt_tokens"] += reply.prompt_tokens
        stats["completion_tokens"] += reply.completion_tokens
        return normalize_form_data(parse_json_reply(reply.content)), stats


def _resolve_proxies() -> dict[str, str] | None:
    """尊重系统代理设置；未配置时返回 None 让 requests 自行判断。"""
    import os

    http = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
    https = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if not http and not https:
        return None
    proxies: dict[str, str] = {}
    if http:
        proxies["http"] = http
    if https:
        proxies["https"] = https
    return proxies


def _sleep_backoff(attempt: int, total: int, reason: str = "") -> None:
    if attempt >= total:
        return
    delay = min(2 ** attempt, 20)
    print(f"  第 {attempt} 次尝试失败（{reason}），{delay} 秒后重试……")
    time.sleep(delay)
