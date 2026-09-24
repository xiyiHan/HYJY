"""配置加载与合并。

配置分三层，优先级由低到高：
  1. config.yaml          可提交版本库的默认配置
  2. config.local.yaml    本地覆盖（含 API Key，已 gitignore）
  3. 环境变量             通过 providers.<name>.api_key_env 指定的变量名读取

这样设计的目的：切换云端平台或转写引擎只需改配置，不需要改动任何代码。
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = "config.yaml"
LOCAL_CONFIG_FILE = "config.local.yaml"

# 需要在占位符解析前先行确定的键
_PRIMARY_PATH_KEYS = ("project_dir", "workspace")


class ConfigError(RuntimeError):
    """配置缺失或非法。"""


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并字典，override 优先。列表整体替换而非拼接。"""
    out = copy.deepcopy(base)
    for key, val in (override or {}).items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = copy.deepcopy(val)
    return out


def _resolve_placeholders(value: Any, ctx: dict[str, str]) -> Any:
    """把字符串中的 {project_dir} / {workspace} 占位符替换为实际路径。

    反复迭代以支持占位符嵌套；遇到无法识别的键时原样返回，避免误伤
    正文里的花括号。
    """
    if isinstance(value, str):
        for _ in range(5):
            try:
                new = value.format(**ctx)
            except (KeyError, IndexError, ValueError):
                return value
            if new == value:
                return value
            value = new
        return value
    if isinstance(value, dict):
        return {k: _resolve_placeholders(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_placeholders(v, ctx) for v in value]
    return value


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name} 的顶层结构必须是字典，当前是 {type(data).__name__}")
    return data


def _read_key_file(path: str) -> str | None:
    """从密钥文件读取密钥。

    容忍常见的存储习惯：带 BOM 的 UTF-8、多行文本、前后空白、末尾换行。
    取第一个非空行作为密钥；读取失败返回 None 而不抛异常，
    以便上层继续尝试其他来源并给出准确的诊断信息。
    """
    try:
        raw = Path(path).read_text(encoding="utf-8-sig", errors="ignore")
    except (OSError, ValueError):
        return None
    for line in raw.splitlines():
        token = line.strip().strip('"').strip("'")
        if token:
            return token
    return None


class Config:
    """已加载并解析完成的配置对象。"""

    def __init__(self, data: dict, project_dir: Path, local_loaded: bool = False):
        self.data = data
        self.project_dir = project_dir
        self.local_loaded = local_loaded

    # ---------- 构造 ----------

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        cfg_path = Path(path).resolve() if path else PROJECT_ROOT / CONFIG_FILE
        if not cfg_path.exists():
            raise ConfigError(f"未找到配置文件：{cfg_path}")

        raw = _read_yaml(cfg_path)
        local_path = cfg_path.parent / LOCAL_CONFIG_FILE
        local = _read_yaml(local_path)
        merged = _deep_merge(raw, local)

        # 第一遍：先定下两个基础路径，其余占位符都依赖它们
        paths = merged.get("paths") or {}
        project_dir = Path(paths.get("project_dir") or cfg_path.parent).resolve()
        ctx = {"project_dir": project_dir.as_posix()}
        workspace = _resolve_placeholders(
            paths.get("workspace") or "{project_dir}/workspace", ctx
        )
        ctx["workspace"] = workspace

        # 第二遍：用完整上下文解析整棵树
        resolved = _resolve_placeholders(merged, ctx)
        return cls(resolved, project_dir, local_loaded=local_path.exists())

    # ---------- 访问 ----------

    def get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self.data
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def path(self, *keys: str) -> Path:
        """取 paths 段下的路径配置并转为 Path。

        注意：这里的 key 是 paths 段内的键名，例如 cfg.path("audio")
        对应 config.yaml 里的 paths.audio。
        """
        value = self.get("paths", *keys)
        if not value:
            raise ConfigError(f"缺少路径配置：paths.{'.'.join(keys)}")
        return Path(str(value))

    def ensure_dirs(self) -> list[Path]:
        """创建全部输出目录，返回创建/确认过的目录列表。"""
        created: list[Path] = []
        for key in ("workspace", "audio", "transcript", "minutes", "model_dir", "cache_dir"):
            value = self.get("paths", key)
            if not value:
                continue
            p = Path(str(value))
            p.mkdir(parents=True, exist_ok=True)
            created.append(p)
        return created

    # ---------- 云端平台 ----------

    def providers(self) -> dict[str, dict]:
        return self.get("providers") or {}

    def provider(self, name: str | None = None) -> tuple[str, dict]:
        """返回 (平台名, 平台配置)。name 为空时取 llm.provider。"""
        providers = self.providers()
        if not providers:
            raise ConfigError("配置中未定义任何云端平台，请检查 providers 段")

        key = name or self.get("llm", "provider") or "deepseek"
        if key not in providers:
            available = "、".join(providers.keys())
            raise ConfigError(f"未知平台 '{key}'。可用平台：{available}")
        return key, providers[key] or {}

    def api_key(self, name: str | None = None) -> str | None:
        """解析 API Key，优先级：环境变量 > 密钥文件 > 内联配置。

        支持密钥文件是为了兼容"把密钥集中存放在专门目录"的使用习惯 ——
        这样密钥不必被复制进项目目录，降低误提交与泄露的风险。
        """
        _, prov = self.provider(name)

        env_name = (prov.get("api_key_env") or "").strip()
        if env_name:
            val = os.environ.get(env_name)
            if val and val.strip():
                return val.strip()

        key_file = (prov.get("api_key_file") or "").strip()
        if key_file:
            token = _read_key_file(key_file)
            if token:
                return token

        inline = (prov.get("api_key") or "").strip()
        return inline or None

    def api_key_source(self, name: str | None = None) -> str:
        """说明密钥来源，供自检命令展示。不返回密钥本身。"""
        key, prov = self.provider(name)

        env_name = (prov.get("api_key_env") or "").strip()
        if env_name and os.environ.get(env_name, "").strip():
            return f"环境变量 {env_name}"

        key_file = (prov.get("api_key_file") or "").strip()
        if key_file:
            if _read_key_file(key_file):
                return f"密钥文件 {Path(key_file).name}"
            return f"密钥文件（读取失败或为空）：{key_file}"

        if (prov.get("api_key") or "").strip():
            return f"config.local.yaml 的 providers.{key}.api_key"

        return "未配置"

    def require_api_key(self, name: str | None = None) -> str:
        key, prov = self.provider(name)
        token = self.api_key(name)
        if token:
            return token
        env_name = prov.get("api_key_env") or "YOUR_API_KEY"
        raise ConfigError(
            f"平台「{prov.get('label', key)}」尚未配置 API Key。\n"
            f"以下三种方式任选其一：\n"
            f"  1) 设置环境变量：set {env_name}=sk-xxxx\n"
            f"  2) 指向密钥文件（推荐，密钥无需放进项目目录）：\n"
            f"     providers:\n"
            f"       {key}:\n"
            f"         api_key_file: \"D:/路径/你的密钥.txt\"\n"
            f"  3) 直接写入密钥：\n"
            f"     providers:\n"
            f"       {key}:\n"
            f"         api_key: \"sk-xxxx\""
        )

    def mask_secret(self, secret: str | None) -> str:
        if not secret:
            return "(未配置)"
        if len(secret) <= 10:
            return secret[:2] + "****"
        return f"{secret[:6]}****{secret[-4:]}"

    # ---------- 转写 ----------

    @property
    def backend_name(self) -> str:
        return (self.get("transcription", "backend") or "funasr").strip().lower()

    def backend_conf(self, name: str | None = None) -> dict:
        key = (name or self.backend_name).strip().lower()
        return self.get("transcription", key) or {}
