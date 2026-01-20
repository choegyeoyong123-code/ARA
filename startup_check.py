from __future__ import annotations

import os
import sys
from importlib import metadata
from typing import Dict, List, Tuple


def _parse_pinned_requirements(requirements_path: str = "requirements.txt") -> Dict[str, str]:
    pinned: Dict[str, str] = {}
    if not os.path.exists(requirements_path):
        return pinned

    with open(requirements_path, "r", encoding="utf-8") as f:
        for raw in f.read().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            # handle extras: uvicorn[standard]==x.y.z
            if "==" in line:
                name, ver = line.split("==", 1)
                pkg = name.split("[", 1)[0].strip()
                pinned[pkg] = ver.strip()
    return pinned


def check_env(required_keys: List[str]) -> Tuple[bool, List[str]]:
    missing = [k for k in required_keys if not (os.environ.get(k) or "").strip()]
    return (len(missing) == 0, missing)


def check_versions(requirements_path: str = "requirements.txt") -> Tuple[bool, List[str]]:
    pinned = _parse_pinned_requirements(requirements_path)
    mismatches: List[str] = []
    for pkg, want in pinned.items():
        try:
            got = metadata.version(pkg)
        except Exception:
            mismatches.append(f"{pkg}: NOT_INSTALLED (wanted {want})")
            continue
        if got != want:
            mismatches.append(f"{pkg}: {got} (wanted {want})")
    return (len(mismatches) == 0, mismatches)


def check_venv() -> bool:
    # True if running inside venv
    return getattr(sys, "base_prefix", sys.prefix) != sys.prefix or bool(os.environ.get("VIRTUAL_ENV"))


def run_startup_checks() -> None:
    """
    Render/Production safety checks:
    - env var presence
    - pinned dependency versions
    - venv warning (local only)
    NOTE: Do not print secrets.
    """
    ok_env, missing = check_env(
        required_keys=[
            "DATA_GO_KR_SERVICE_KEY",
        ]
    )
    if not ok_env:
        print(f"WARNING: Missing env keys: {', '.join(missing)}")

    ok_ver, mismatches = check_versions("requirements.txt")
    if not ok_ver:
        print("WARNING: Dependency version mismatch detected:")
        for m in mismatches[:20]:
            print(f"- {m}")

    if check_venv() and (not ok_ver):
        print("WARNING: Local .venv/virtualenv differs from pinned production requirements.")

