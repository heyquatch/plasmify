"""Check that every package Plasmify may install exists on the running distribution.

Meant to run inside distribution containers in CI (no Plasma needed). Exits non-zero
when a mapped package name is not found in the configured repositories.
"""

from __future__ import annotations

import os
import subprocess
import sys

from plasmify import distro, package_map, packages


def is_provided(backend: str, name: str) -> bool:
    """Whether a package provides ``name`` (openSUSE's python3-PyQt6 comes from python313-PyQt6).
    zypper and dnf install such names too, which is what install.sh relies on."""
    if backend == "zypper":
        args = ["zypper", "--non-interactive", "--quiet", "search", "--provides", "--match-exact", name]
    elif backend == "dnf":
        args = ["dnf", "--quiet", "repoquery", "--whatprovides", name]
    else:
        return False
    proc = subprocess.run(args, capture_output=True, text=True, check=False)  # noqa: S603 (fixed program)
    return proc.returncode == 0 and bool(proc.stdout.strip())


def main() -> int:
    target = distro.detect()
    if target is None:
        print("Unsupported distribution")
        return 1
    backend = packages.backend_for(target)
    natives = {}
    for canonical in package_map.PACKAGES:
        native = package_map.native_name(canonical, target)
        if native:
            natives[canonical] = native
    names = sorted(set(natives.values()))
    missing = set(backend.missing(names))
    sizes = backend.sizes(sorted(missing))
    provided = {n for n in names if n in missing and n not in sizes and is_provided(backend.name, n)}
    broken = [n for n in names if n in missing and n not in sizes and n not in provided]
    print(f"{target} ({backend.name}): checked {len(names)} packages")
    for canonical, native in sorted(natives.items()):
        state = "MISSING" if native in broken else ("installed" if native not in missing else "available")
        state = "provided" if native in provided else state
        print(f"  {state:9} {native:28} ({canonical})")
    for name in broken:
        fallback = (
            "font fallback exists"
            if any(natives[c] == name and c in package_map.FONT_FALLBACKS for c in natives)
            else "no fallback"
        )
        print(f"::error::{name} is not available on {target} ({fallback})")
    # Root runs these by full path, so each path must be right on every distribution.
    absent = [p for p in backend.install_command(["x"]) if p.startswith("/") and not os.access(p, os.X_OK)]
    for program in absent:
        print(f"::error::{program}, used to install packages, does not exist on {target}")
    return 1 if broken or absent else 0


if __name__ == "__main__":
    sys.exit(main())
