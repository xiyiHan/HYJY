"""把仓库中的业务信息替换为通用占位。

背景：本项目是通用工具，但开发和验证过程中把真实的单位名、项目名、
地名写进了配置示例、测试夹具和文档里。仓库若公开，这些内容会暴露
单位承接的政府项目信息，必须在推送前清理。

用法：
    python scripts/sanitize_business_terms.py --check    # 只扫描，不修改
    python scripts/sanitize_business_terms.py --apply    # 执行替换
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 替换规则：键为需要清理的内容，值为通用占位。
# 按长度倒序应用，避免短词先替换掉长词的局部。
REPLACEMENTS: dict[str, str] = {
    # 单位与机构
    "国研科技": "某某科技",
    "国研咨询": "某某咨询",
    "大同市卫生健康委员会": "某某市卫生健康委员会",
    "大同市疾控中心": "某某市疾控中心",
    "唐山启奥科技股份有限公司": "某某科技公司",

    # 项目与会议
    "大同一张网信息化建设项目": "某某市政务信息化建设项目",
    "大同一张网": "某某政务网",
    "大同市中心血站": "某某市某单位",
    "大同市血站": "某某市某单位",
    "大同市血站录音-20260114": "示例会议录音",
    "血站信息化": "行业信息化",
    "网络数字教学": "业务系统",
    "灵丘县调研": "某县调研",
    "血站调研": "业务调研",
    "中心血站": "某单位",
    "血站": "某单位",

    # 地名
    "大同市": "某某市",
    "大同": "某地",
}

# 只处理这些类型，避免误伤二进制
TEXT_SUFFIXES = {".md", ".py", ".yaml", ".yml", ".example", ".txt", ".bat", ""}


def tracked_files() -> list[Path]:
    """列出仓库中的文件。

    必须用 -z 取 NUL 分隔的原始路径：git 默认会把含中文等非 ASCII 字符的
    路径转义成八进制并加引号（core.quotePath），直接按行解析会得到
    `"docs/\\347\\225..."` 这样的字符串，导致中文名文件被整体跳过 ——
    实测中文命名的文档因此差点漏检。
    """
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
    )
    raw = result.stdout.decode("utf-8", errors="surrogateescape")
    return [ROOT / name for name in raw.split("\0") if name.strip()]


def main() -> int:
    apply = "--apply" in sys.argv
    check_only = "--check" in sys.argv or not apply

    files = [f for f in tracked_files() if f.is_file() and f.suffix.lower() in TEXT_SUFFIXES]
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
        for src in sorted(REPLACEMENTS, key=len, reverse=True):
            dst = REPLACEMENTS[src]
            if src in updated:
                hits[src] = updated.count(src)
                updated = updated.replace(src, dst)

        if not hits:
            continue

        rel = path.relative_to(ROOT).as_posix()
        count = sum(hits.values())
        total_changes += count
        touched.append(rel)
        print(f"  {rel}")
        for src, n in sorted(hits.items(), key=lambda kv: -kv[1]):
            print(f"      {src}  →  {REPLACEMENTS[src]}   （{n} 处）")

        if apply:
            path.write_text(updated, encoding="utf-8")

    print("")
    if not touched:
        print("未发现需要清理的业务信息。")
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
