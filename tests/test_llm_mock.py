r"""云端链路集成测试 —— 用本地模拟服务替代真实大模型接口。

运行：
    .venv\Scripts\python.exe tests\test_llm_mock.py

为什么需要它：真实接口需要 API Key 和网络，无法在开发阶段反复验证。
这里起一个本地 HTTP 服务，完整模拟 OpenAI 兼容协议，从而在**不消耗任何
额度、不外发任何数据**的前提下验证：

  - 请求构造是否正确（认证头、模型名、消息结构、温度、max_tokens）
  - 响应解析是否正确（正文提取、token 统计）
  - 长文字稿是否自动触发分段汇总（map-reduce）
  - 各类 HTTP 错误是否给出可操作的中文提示
  - 限流与 5xx 是否按预期退避重试

测试全程数据不出本机。
"""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

# 本地回环不应走系统代理，否则请求会被代理拦下
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)
os.environ["MOCK_API_KEY"] = "sk-mock-key-for-testing"

from mmtools.config import Config, ConfigError  # noqa: E402
from mmtools.summarizer import CloudLLM, LLMError  # noqa: E402
from mmtools.transcribers import Segment, TranscriptResult  # noqa: E402
from mmtools import pipeline as pipe  # noqa: E402

FAKE_MINUTES = """# 会议纪要

## 一、会议基本信息

| 项目 | 内容 |
| --- | --- |
| 会议名称 | 监理例会 |
| 会议时间 | （文字稿未提及） |

## 二、会议议题

1. 上周工程进度汇报

## 四、议定事项

1. **下周三前**将初步验收方案报送监理单位审核

## 五、责任分工与完成时限

| 序号 | 事项 | 责任单位 | 完成时限 |
| --- | --- | --- | --- |
| 1 | 报送初验方案 | 承建单位 | 下周三 |
"""


class _Handler(BaseHTTPRequestHandler):
    """模拟 OpenAI 兼容接口。"""

    requests_seen: list[dict] = []
    mode = "ok"
    fail_times = 0  # mode=flaky 时，前 N 次返回 429

    def log_message(self, *args):  # 静音，避免刷屏
        pass

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            payload = {}

        _Handler.requests_seen.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization", ""),
                "content_type": self.headers.get("Content-Type", ""),
                "payload": payload,
            }
        )

        if _Handler.mode == "unauthorized":
            self._send(401, {"error": {"message": "Invalid API key"}})
            return
        if _Handler.mode == "notfound":
            self._send(404, {"error": {"message": "model not found"}})
            return
        if _Handler.mode == "servererror":
            self._send(500, {"error": {"message": "internal error"}})
            return
        if _Handler.mode == "flaky" and len(_Handler.requests_seen) <= _Handler.fail_times:
            self._send(429, {"error": {"message": "rate limited"}})
            return

        prompt = "".join(
            m.get("content", "") for m in payload.get("messages", []) if isinstance(m, dict)
        )
        self._send(
            200,
            {
                "id": "chatcmpl-mock",
                "object": "chat.completion",
                "model": payload.get("model", "mock"),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": FAKE_MINUTES},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": max(1, len(prompt) // 2),
                    "completion_tokens": max(1, len(FAKE_MINUTES) // 2),
                },
            },
        )


def _start_server() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/v1"


def _mock_config(base_url: str, minutes_dir: Path, **overrides) -> Config:
    """构造指向本地模拟服务的配置，不修改任何磁盘上的配置文件。"""
    cfg = Config.load()
    cfg.data["providers"]["custom"] = {
        "label": "本地模拟服务",
        "base_url": base_url,
        "model": "mock-model",
        "api_key_env": "MOCK_API_KEY",
        "compliance": "仅用于测试，不出本机",
    }
    cfg.data["llm"]["provider"] = "custom"
    cfg.data["paths"]["minutes"] = str(minutes_dir)
    cfg.data["llm"].update(overrides)
    cfg.data["llm"]["retries"] = overrides.get("retries", 3)
    return cfg


PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str):
    def decorator(fn):
        try:
            fn()
            PASSED.append(name)
        except Exception as exc:  # noqa: BLE001
            FAILED.append((name, f"{type(exc).__name__}: {exc}"))
        return fn

    return decorator


def _reset(mode: str = "ok", fail_times: int = 0) -> None:
    _Handler.mode = mode
    _Handler.fail_times = fail_times
    _Handler.requests_seen.clear()


# ---------------------------------------------------------------- 测试


def main() -> int:
    server, base_url = _start_server()
    minutes_dir = Config.load().path("cache_dir") / "mocktest"
    minutes_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 62)
    print("云端链路集成测试（本地模拟服务，数据不出本机）")
    print(f"模拟服务地址：{base_url}")
    print("=" * 62)
    print("")

    try:
        _run_all(base_url, minutes_dir)
    finally:
        server.shutdown()
        server.server_close()
        # 清理测试产物
        for f in minutes_dir.glob("*"):
            f.unlink(missing_ok=True)

    for name in PASSED:
        print(f"  [通过] {name}")
    for name, err in FAILED:
        print(f"  [失败] {name}")
        print(f"         {err}")

    print("")
    print("-" * 62)
    total = len(PASSED) + len(FAILED)
    print(f"合计 {total} 项：通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        return 1
    print("云端调用链路正常。")
    return 0


def _run_all(base_url: str, minutes_dir: Path) -> None:

    @check("请求构造正确（端点、认证头、模型名、参数）")
    def _():
        _reset()
        cfg = _mock_config(base_url, minutes_dir)
        llm = CloudLLM(cfg)
        reply = llm.chat([{"role": "user", "content": "测试"}])

        assert len(_Handler.requests_seen) == 1, "请求次数异常"
        seen = _Handler.requests_seen[0]
        assert seen["path"] == "/v1/chat/completions", f"端点错误：{seen['path']}"
        assert seen["authorization"] == "Bearer sk-mock-key-for-testing", "认证头错误"
        assert "application/json" in seen["content_type"], "Content-Type 错误"

        payload = seen["payload"]
        assert payload["model"] == "mock-model", f"模型名错误：{payload.get('model')}"
        assert payload["stream"] is False, "stream 应为 False"
        assert "temperature" in payload and "max_tokens" in payload
        assert isinstance(payload["messages"], list) and payload["messages"]
        assert reply.content.startswith("# 会议纪要"), "响应正文解析错误"

    @check("token 统计正确回传")
    def _():
        _reset()
        cfg = _mock_config(base_url, minutes_dir)
        llm = CloudLLM(cfg)
        reply = llm.chat([{"role": "user", "content": "测试"}])
        assert reply.prompt_tokens > 0, "未统计输入 token"
        assert reply.completion_tokens > 0, "未统计输出 token"
        assert reply.total_tokens == reply.prompt_tokens + reply.completion_tokens

    @check("连通性探测可用")
    def _():
        _reset()
        cfg = _mock_config(base_url, minutes_dir)
        content = CloudLLM(cfg).probe()
        assert content, "probe 返回为空"

    @check("系统提示词与文字稿均随请求发出")
    def _():
        _reset()
        cfg = _mock_config(base_url, minutes_dir)
        llm = CloudLLM(cfg)
        llm.summarize("说话人1 今天讨论初验方案。", meeting_title="监理例会")

        seen = _Handler.requests_seen[-1]
        messages = seen["payload"]["messages"]
        assert messages[0]["role"] == "system", "第一条消息应为 system"
        assert "议定事项" in messages[0]["content"], "系统提示词未包含纪要模板"
        assert messages[1]["role"] == "user", "第二条消息应为 user"
        assert "初验方案" in messages[1]["content"], "文字稿未随请求发出"
        assert "监理例会" in messages[1]["content"], "会议名称未随请求发出"

    @check("长文字稿自动分段汇总（map-reduce）")
    def _():
        _reset()
        # 把分段阈值调小，触发分段路径
        cfg = _mock_config(base_url, minutes_dir, chunk_threshold=2000)
        llm = CloudLLM(cfg)

        long_text = "\n".join(
            f"[00:{i:02d}]说话人{i % 3 + 1} 这是第 {i} 条发言内容，用于触发分段逻辑。"
            for i in range(300)
        )
        assert len(long_text) > 2000, "造出的测试文本不够长"

        content, stats = llm.summarize(long_text)
        assert stats["chunked"] is True, "未触发分段"
        assert stats["chunks"] > 1, f"分段数异常：{stats['chunks']}"
        # 分段数 + 1 次汇总调用
        assert len(_Handler.requests_seen) == stats["chunks"] + 1, (
            f"调用次数与分段数不匹配：{len(_Handler.requests_seen)} vs {stats['chunks'] + 1}"
        )
        assert content.startswith("# 会议纪要"), "汇总结果解析错误"

    @check("短文字稿不分段")
    def _():
        _reset()
        cfg = _mock_config(base_url, minutes_dir, chunk_threshold=24000)
        llm = CloudLLM(cfg)
        _, stats = llm.summarize("很短的一段文字稿。")
        assert stats["chunked"] is False
        assert stats["chunks"] == 1

    @check("401 认证失败给出密钥排查提示")
    def _():
        _reset(mode="unauthorized")
        cfg = _mock_config(base_url, minutes_dir, retries=1)
        try:
            CloudLLM(cfg).chat([{"role": "user", "content": "x"}])
        except LLMError as exc:
            text = str(exc)
            assert "认证失败" in text, f"缺少关键提示：{text}"
            assert "密钥来源" in text, "未提示密钥来源"
            return
        raise AssertionError("未对 401 抛出 LLMError")

    @check("404 提示核对 base_url 与 model")
    def _():
        _reset(mode="notfound")
        cfg = _mock_config(base_url, minutes_dir, retries=1)
        try:
            CloudLLM(cfg).chat([{"role": "user", "content": "x"}])
        except LLMError as exc:
            text = str(exc)
            assert "base_url" in text and "model" in text, f"提示不足：{text}"
            return
        raise AssertionError("未对 404 抛出 LLMError")

    @check("限流后按退避策略重试并最终成功")
    def _():
        _reset(mode="flaky", fail_times=2)
        cfg = _mock_config(base_url, minutes_dir, retries=4)
        reply = CloudLLM(cfg).chat([{"role": "user", "content": "x"}])
        assert reply.content, "重试后仍无内容"
        assert len(_Handler.requests_seen) == 3, (
            f"重试次数异常，实际请求 {len(_Handler.requests_seen)} 次"
        )

    @check("5xx 重试耗尽后抛出可读错误")
    def _():
        _reset(mode="servererror")
        cfg = _mock_config(base_url, minutes_dir, retries=2)
        try:
            CloudLLM(cfg).chat([{"role": "user", "content": "x"}])
        except LLMError as exc:
            assert "服务端异常" in str(exc), f"提示不足：{exc}"
            return
        raise AssertionError("未对 5xx 抛出 LLMError")

    @check("完整流水线生成纪要文件与 Word 文档")
    def _():
        _reset()
        cfg = _mock_config(base_url, minutes_dir)
        result = TranscriptResult(
            segments=[
                Segment(text="今天讨论初验方案。", start=5, end=10, speaker="说话人1"),
                Segment(text="下周三前报送。", start=12, end=18, speaker="说话人2"),
            ],
            backend="funasr",
            model="paraformer-zh",
            diarized=True,
            elapsed=80.0,
            duration=3600.0,
        )
        target, stats = pipe.step_summarize(
            cfg, result, stem="mocktest", title="监理例会", verbose=False
        )
        assert target.exists(), "纪要文件未生成"
        body = target.read_text(encoding="utf-8")
        assert "# 会议纪要" in body, "纪要正文缺失"
        assert "生成时间" in body, "缺少生成元信息"
        assert "人工复核" in body, "缺少复核提示"

        docx_path = pipe.export_docx(cfg, target, verbose=False)
        if docx_path is not None:
            assert docx_path.exists() and docx_path.stat().st_size > 0, "Word 文档为空"

    @check("预览模式不调用云端")
    def _():
        _reset()
        cfg = _mock_config(base_url, minutes_dir)
        result = TranscriptResult(
            segments=[Segment(text="仅用于预览测试。", start=0, end=2)],
            backend="funasr",
            model="paraformer-zh",
        )
        target, stats = pipe.step_summarize(
            cfg, result, stem="preview", verbose=False, dry_run=True
        )
        assert stats.get("dry_run") is True, "未标记为预览模式"
        assert len(_Handler.requests_seen) == 0, "预览模式竟然发起了网络请求"
        assert target.exists(), "预览文件未生成"
        content = target.read_text(encoding="utf-8")
        assert "仅用于预览测试" in content, "预览未包含文字稿"
        assert "系统提示词" in content, "预览未包含系统提示词"


if __name__ == "__main__":
    raise SystemExit(main())
