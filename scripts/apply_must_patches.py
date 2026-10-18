#!/usr/bin/env python3
"""Check the recorded upstream bases and optionally apply the three MUST patches."""
import argparse
import json
import subprocess
from pathlib import Path


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--android-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    revisions = json.loads((project / "source/baseline-revisions.json").read_text())
    checked = []
    for relative, metadata in revisions.items():
        repo = args.android_root.resolve() / relative
        if git(repo, "rev-parse", "HEAD") != metadata["upstream_base"]:
            raise SystemExit(f"Wrong base: {relative}; need {metadata['upstream_base']}")
        if git(repo, "status", "--porcelain", "--untracked-files=normal"):
            raise SystemExit(f"Workspace is not clean: {relative}")
        patch = project / metadata["patch"]
        git(repo, "apply", "--check", str(patch))
        checked.append((repo, patch))
        print("Checked:", relative)
    if not args.apply:
        print("All checks passed. No files changed. Use --apply to apply patches.")
        return
    for repo, patch in checked:
        git(repo, "apply", str(patch))
        print("Applied:", repo)
    print("Patches applied as uncommitted changes. Review before building.")


if __name__ == "__main__":
    main()
