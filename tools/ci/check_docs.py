"""Fast whitespace and local-link validation for lightweight changes."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path


LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


def local_link_errors(root: Path, paths: list[str]) -> list[str]:
    errors: list[str] = []
    for value in paths:
        path = root / value
        if path.suffix.lower() != ".md" or not path.exists():
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for target in LINK.findall(line):
                clean = target.strip().strip("<>").split("#", 1)[0]
                if not clean or "://" in clean or clean.startswith(("mailto:", "codex:")):
                    continue
                if not (path.parent / clean).resolve().exists():
                    errors.append(f"{value}:{line_number}: missing local link target {target}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--paths-file", type=Path, required=True)
    parser.add_argument("--base")
    parser.add_argument("--head")
    args = parser.parse_args()
    if args.base and args.head:
        diff = subprocess.run(
            ["git", "diff", "--check", args.base, args.head], cwd=args.root, text=True, capture_output=True
        )
        if diff.returncode:
            print(diff.stdout + diff.stderr, end="")
            return diff.returncode
    errors = local_link_errors(args.root, args.paths_file.read_text(encoding="utf-8").splitlines())
    if errors:
        print("\n".join(errors))
        return 1
    print("Changed-file whitespace and local Markdown links are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
