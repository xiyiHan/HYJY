r"""切换 Git 远程仓库地址，切换前先验证目标仓库确实存在。

为什么需要它：直接执行 git remote set-url 时，即使目标仓库不存在也会"成功"，
错误要等到下次 push 才暴露，而且报错信息（Repository not found）容易让人
误以为是权限问题，浪费时间排查。

本脚本在改地址之前先用 git ls-remote 探一次，确认可达才写入。

用法：
    python scripts\set_remote.py HYJY                       # 按用户名拼接，默认 xiyiHan
    python scripts\set_remote.py git@github.com:xiyiHan/HYJY.git
    python scripts\set_remote.py HYJY --push                # 改完顺便推送
    python scripts\set_remote.py --show                     # 只看当前配置
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OWNER = "xiyiHan"

# 本机 Git 全局配了 127.0.0.1:7890 的代理，而该代理通常未运行，
# 会让 HTTPS 方式的 git 操作直接失败。这里显式绕开，只影响本脚本的探测。
PROXY_OVERRIDES = ["-c", "http.proxy=", "-c", "https.proxy="]


def _run(args: list[str], timeout: int = 40) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            args, cwd=ROOT, capture_output=True, timeout=timeout,
        )
        out = (result.stdout or b"").decode("utf-8", "replace")
        err = (result.stderr or b"").decode("utf-8", "replace")
        return result.returncode, out, err
    except subprocess.TimeoutExpired:
        return 124, "", "操作超时"


def normalize_target(value: str) -> str:
    """把仓库名或完整地址都整理成 SSH 地址。"""
    value = value.strip().rstrip("/")
    if value.endswith(".git"):
        value = value[: -len(".git")]

    if value.startswith(("git@", "https://", "http://", "ssh://")):
        base = value
    else:
        base = f"git@github.com:{DEFAULT_OWNER}/{value}"

    # git@github.com:owner/name 形式
    if base.startswith("git@github.com:"):
        return base + ".git"

    # https://github.com/owner/name 形式转成 SSH（本机 HTTPS 被失效代理拦着）
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)", base)
    if m:
        return f"git@github.com:{m.group(1)}/{m.group(2)}.git"

    return base + ".git"


def probe(url: str) -> tuple[bool, str]:
    """探测仓库是否可达。"""
    code, out, err = _run(["git", *PROXY_OVERRIDES, "ls-remote", url])
    if code == 0:
        return True, "可达"
    combined = (err or out or "").strip()
    if "Repository not found" in combined:
        return False, "仓库不存在，或当前账号无权访问"
    if "Could not resolve" in combined or "Failed to connect" in combined:
        return False, "网络不通（本机 Git 代理可能指向了未运行的端口）"
    if "Permission denied" in combined:
        return False, "SSH 认证失败，请确认公钥已加入 GitHub 账号"
    return False, combined.splitlines()[0] if combined else f"退出码 {code}"


def main() -> int:
    args = [a for a in sys.argv[1:]]
    do_push = "--push" in args
    args = [a for a in args if a != "--push"]

    if not args or args[0] == "--show":
        code, out, err = _run(["git", "remote", "-v"])
        print("当前远程配置：")
        print(out.strip() or "  （未配置）")
        return code

    target = normalize_target(args[0])
    print(f"目标地址：{target}")
    print("正在探测是否可达……")

    ok, reason = probe(target)
    if not ok:
        print("")
        print(f"  切换已中止：{reason}")
        print("")
        print("  请先在 GitHub 网页上创建该仓库，或确认仓库名与账号权限。")
        print(f"  创建后重跑：python scripts\\set_remote.py {args[0]}")
        return 1

    print("  可达，正在切换……")
    code, out, err = _run(["git", "remote", "set-url", "origin", target])
    if code != 0:
        print(f"  切换失败：{(err or out).strip()}")
        return code

    print(f"  已切换：{target}")

    if do_push:
        branch = "master"
        code, out, err = _run(["git", "branch", "--show-current"])
        if code == 0 and out.strip():
            branch = out.strip()
        print(f"  正在推送分支 {branch}……")
        code, out, err = _run(["git", "push", "-u", "origin", branch], timeout=300)
        combined = (out or "") + (err or "")
        print(combined.strip() or "  （无输出）")
        if code != 0:
            return code
        print("  推送完成。")
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass
    raise SystemExit(main())
