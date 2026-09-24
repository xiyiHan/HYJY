r"""把仓库中的业务信息替换为通用占位。

背景：本项目是通用工具，但开发和验证过程中把真实的单位名、项目名、
地名写进了配置示例、测试夹具和文档里。仓库若公开，这些内容会暴露
单位承接的政府项目信息，必须在推送前清理。

【重要设计】替换规则**不写在本文件里**。

第一版把真实业务名明文列在这里，结果脚本本身成了泄露源 —— 推到公开
仓库后，读脚本就能看到全部原始名称。规则改由 scripts/sanitize_terms.local.json
提供，该文件已在 .gitignore 中，不进版本库。

用法：
    python scripts/sanitize_business_terms.py --check    # 只扫描，不修改
    python scripts/sanitize_business_terms.py --apply    # 执行替换

若本地规则文件不存在，本脚本会退出并提示，不会用一份内置名单误报无事。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RULES_FILE = Path(__file__).resolve().parent / "sanitize_terms.local.json"

# 只处理文本类型，避免误伤二进制
TEXT_SUFFIXES = {".md", ".py", ".yaml", ".yml", ".example", ".txt", ".bat", ""}

# 本文件自身不参与扫描（规则里可能出现示例名称）
SELF = "scripts/sanitize_business_terms.py"


def load_replacements() -> dict[str, str]:
    if not RULES_FILE.exists():
        print(f"未找到规则文件：{RULES_FILE}")
        print("")
        print("为避免误报，脚本不会使用任何内置名单。")
        print("请创建该文件，内容形如：")
        print(json.dumps({"真实单位名": "某某单位", "真实项目名": "某某项目"},
                         ensure_ascii=False, indent=2))
        print("")
        print("该文件已在 .gitignore 中，不会进版本库。")
        sys.exit(2)

    try:
        data = json.loads(RULES_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"规则文件不是合法 JSON：{exc}")
        sys.exit(2)

    if not isinstance(data, dict) or not data:
        print("规则文件为空，或顶层不是对象。")
        sys.exit(2)

    return {str(k): str(v) for k, v in data.items()}


def tracked_files() -> list[Path]:
    """列出仓库中的文件。

    必须用 -z 取 NUL 分隔的原始路径：git 默认会把含中文等非 ASCII 字符的
    路径转义成八进制并加引号（core.quotePath），直接按行解析会得到
    `"docs/\\347\\225..."` 这样的字符串，导致中文名文件被整体跳过 ——
    实测中文命名的文档因此差点漏检。
    """
    result = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True)
    raw = result.stdout.decode("utf-8", errors="surrogateescape")
    return [ROOT / name for name in raw.split("\0") if name.strip()]


def main() -> int:
    apply = "--apply" in sys.argv
    check_only = "--check" in sys.argv or not apply

    replacements = load_replacements()
    files = [
        f for f in tracked_files()
        if f.is_file() and f.suffix.lower() in TEXT_SUFFIXES
        and f.relative_to(ROOT).as_posix() != SELF
    ]
    if not files:
        print("未找到可处理的文本文件。")
        return 0

    total_changes = 0
    touched: list[str] = []

    for path in files:
        try:
            original = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        updated = original
        hits: dict[str, int] = {}
        # 按长度倒序，避免短词先替换掉长词的局部
        for src in sorted(replacements, key=len, reverse=True):
            if src in updated:
                hits[src] = updated.count(src)
                updated = updated.replace(src, replacements[src])

        if not hits:
            continue

        rel = path.relative_to(ROOT).as_posix()
        touched.append(rel)
        total_changes += sum(hits.values())
        print(f"  {rel}")
        for src, n in sorted(hits.items(), key=lambda kv: -kv[1]):
            print(f"      {src}  →  {replacements[src]}   （{n} 处）")

        if apply:
            path.write_text(updated, encoding="utf-8")

    print("")
    if not touched:
        print("未发现需要清理的业务信息。")
        print("")
        print("提醒：本脚本只扫描文本文件。界面截图等二进制产物会显示本机文件名，")
        print("      如包含业务信息，需在脱敏样例状态下重新生成。")
        return 0

    print(f"涉及 {len(touched)} 个文件，共 {total_changes} 处。")
    if check_only:
        print("")
        print("这是扫描模式，未修改任何文件。确认无误后加 --apply 执行替换。")
    else:
        print("")
        print("已完成替换。请重新运行测试，并检查界面截图等二进制产物是否也需要更新。")
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass
    raise SystemExit(main())
