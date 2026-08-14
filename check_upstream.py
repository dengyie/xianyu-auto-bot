#!/usr/bin/env python3
"""检查 upstream (GuDong2003/xianyu-auto-reply-fix) 是否有值得同步的新 commit/PR。

用法:
    python3 check_upstream.py          # 拉取 upstream 并列出新 PR/commit
    python3 check_upstream.py --diff   # 同时输出各 PR 的核心文件变更

前置: 仓库已配置 upstream remote (git remote add upstream <url>)

注意: upstream main 历史是浅克隆 (root commit 无父)，且与我们无 merge-base，
所以不能直接 git log merge-base..upstream。本脚本依赖 refs/remotes/upstream/pr/*。
"""
import argparse
import subprocess
import sys

UPSTREAM = "upstream"
PR_PREFIX = f"refs/remotes/{UPSTREAM}/pr/"


def sh(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True).stdout.strip()


def fetch() -> None:
    print(f"[*] fetching {UPSTREAM} ...")
    sh("git", "fetch", UPSTREAM)
    sh("git", "fetch", UPSTREAM, "refs/pull/*/head:refs/remotes/upstream/pr/*")


def list_prs(show_diff: bool) -> None:
    refs = sh("git", "for-each-ref", "--format=%(refname:short) %(objectname:short) %(creatordate:short) %(subject)",
              PR_PREFIX).splitlines()
    if not refs:
        print("[!] 无 PR refs，先运行 fetch")
        return

    # 按时间倒序
    def _key(line: str):
        parts = line.split(" ", 3)
        return parts[2] if len(parts) > 2 else ""

    for line in sorted(refs, key=_key, reverse=True):
        parts = line.split(" ", 3)
        pr = parts[0].rsplit("/", 1)[-1]
        date = parts[2] if len(parts) > 2 else "?"
        subject = parts[3] if len(parts) > 3 else ""
        print(f"PR {pr:>4}  {date}  {subject}")

        if show_diff:
            files = sh("git", "show", "--stat", "--oneline", parts[1]).splitlines()
            core = [f for f in files if f.strip().endswith((".py", ".js", ".html", ".yml"))]
            for f in core[:8]:
                print(f"        {f.strip()}")


def main() -> None:
    ap = argparse.ArgumentParser(description="check upstream PRs")
    ap.add_argument("--diff", action="store_true", help="show per-PR file stats")
    args = ap.parse_args()

    try:
        fetch()
    except subprocess.CalledProcessError as e:
        print(f"[!] fetch 失败: {e}")
        sys.exit(1)

    list_prs(args.diff)


if __name__ == "__main__":
    main()
