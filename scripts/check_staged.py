r"""提交前安全检查 —— 阻止敏感文件进入版本库。

为什么需要它：这个仓库里同时存在源码和业务数据 ——
workspace/ 下是真实的会议录音与纪要（含单位名称、项目名称、人员姓名），
config.local.yaml 里是密钥文件路径。这两类东西一旦被提交并推送到远程，
即使随后删除，历史里仍然留有记录，等同于泄露。

靠人每次提交前手动核对是靠不住的。这个脚本挂成 git 的 pre-commit 钩子，
在提交真正发生之前拦截。

用法：
    python scripts/check_staged.py          # 检查暂存区
    python scripts/check_staged.py --install  # 安装为 pre-commit 钩子
    python scripts/check_staged.py --force    # 仅打印，不拦截（排查用）
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 禁止入库的路径前缀
FORBIDDEN_DIRS = (
    "workspace/",
    "models/",
    ".venv/",
    "venv/",
    "env/",
    ".cache/",
)

# 禁止入库的具体文件名
FORBIDDEN_FILES = (
    "config.local.yaml",
)

# 禁止入库的扩展名（音频与密钥）
FORBIDDEN_SUFFIXES = (
    ".wav", ".m4a", ".mp3", ".aac", ".flac", ".ogg", ".wma", ".amr",
    ".key", ".pem", ".p12", ".pfx",
)

# 真实密钥的形态：sk- 前缀后跟足够长的随机串。
# 用完整正则而不是简单的字符串查找 —— 后者会把本文件里定义标记的代码
# 也判为可疑（实测踩过这个误报），而安全机制一旦误报就会被绕过。
SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]{20,}")

# 本文件自身不参与内容扫描，否则会把标记定义当成真密钥
SELF = "scripts/check_staged.py"

# 内容扫描的体积上限，超过就不扫（避免卡住）
MAX_SCAN_BYTES = 512 * 1024

# 这些路径允许出现密钥形态的字符串（示例、文档、测试）
ALLOWLIST = (
    "config.local.yaml.example",
    "config.local.example",
    "README",
    "docs/",
    "tests/",
)


def staged_files() -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    if result.returncode != 0:
        print("无法读取暂存区，请确认当前目录是 Git 仓库。")
        sys.exit(2)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def check_paths(files: list[str]) -> list[str]:
    problems: list[str] = []
    for name in files:
        normalized = name.replace("\\", "/")
        low = normalized.lower()

        for prefix in FORBIDDEN_DIRS:
            if normalized.startswith(prefix) or f"/{prefix}" in f"/{normalized}":
                problems.append(f"{normalized}  ← 命中禁止目录 {prefix}")
                break
        else:
            base = Path(normalized).name
            if base in FORBIDDEN_FILES:
                problems.append(f"{normalized}  ← 禁止入库的文件（含密钥路径）")
                continue
            suffix = Path(low).suffix
            if suffix in FORBIDDEN_SUFFIXES:
                problems.append(f"{normalized}  ← 禁止入库的文件类型 {suffix}")
    return problems


def check_contents(files: list[str]) -> list[str]:
    """兜底扫描文件内容，防止密钥被写进了本该提交的配置文件。

    只在源码与配置里找形如 sk-xxxxxxxxxxxxxxxxxxxx 的真实密钥。
    示例文件、文档、测试目录里的占位字符串不参与判定 —— 否则会大量误报，
    而误报会让人习惯性地绕过检查，等于没有检查。
    """
    problems: list[str] = []
    for name in files:
        normalized = name.replace("\\", "/")
        if normalized == SELF:
            continue
        if any(allowed in normalized for allowed in ALLOWLIST):
            continue

        path = ROOT / name
        if not path.is_file():
            continue
        try:
            if path.stat().st_size > MAX_SCAN_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        for match in SECRET_PATTERN.finditer(text):
            line_no = text[: match.start()].count("\n") + 1
            head = match.group(0)[:12]
            problems.append(f"{normalized}  ← 疑似硬编码密钥（第 {line_no} 行）：{head}****")
            break  # 同一文件只报一次

    return problems


def install_hook() -> int:
    hooks_dir = ROOT / ".git" / "hooks"
    if not hooks_dir.parent.exists():
        print("当前目录不是 Git 仓库，无法安装钩子。")
        return 2
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook = hooks_dir / "pre-commit"
    hook.write_text(
        "#!/bin/sh\n"
        "# 由 scripts/check_staged.py --install 生成，勿手工编辑\n"
        'cd "$(git rev-parse --show-toplevel)" || exit 1\n'
        'if [ -x ".venv/Scripts/python.exe" ]; then\n'
        '  .venv/Scripts/python.exe scripts/check_staged.py || exit 1\n'
        "else\n"
        "  python scripts/check_staged.py || exit 1\n"
        "fi\n",
        encoding="utf-8",
        newline="\n",
    )
    try:
        hook.chmod(0o755)
    except Exception:
        pass
    print(f"已安装 pre-commit 钩子：{hook}")
    print("之后每次 git commit 都会先做敏感文件检查。")
    return 0


def main() -> int:
    if "--install" in sys.argv:
        return install_hook()

    force = "--force" in sys.argv
    files = staged_files()
    if not files:
        return 0

    problems = check_paths(files) + check_contents(files)

    if not problems:
        return 0

    print("")
    print("=" * 64)
    print("  提交已被阻止 —— 暂存区中包含不应入库的文件")
    print("=" * 64)
    print("")
    for item in problems:
        print(f"  {item}")
    print("")
    print("  这些文件可能包含会议录音、单位与项目信息，或密钥路径。")
    print("  一旦推送即无法撤回，请务必排除后再提交。")
    print("")
    print("  排除方法：")
    print("    git restore --staged <文件>      # 从暂存区移除")
    print("    并确认 .gitignore 已覆盖该路径")
    print("")
    print("=" * 64)
    return 0 if force else 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass
    raise SystemExit(main())
