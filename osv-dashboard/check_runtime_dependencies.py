"""Fail publishing early if a dashboard dependency is missing or undeclared."""
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import sys
import tomllib

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def main():
    dashboard = Path(__file__).resolve().parent
    project = tomllib.loads((dashboard.parent / "pyproject.toml").read_text())
    declared = {
        canonicalize_name(Requirement(item).name)
        for item in project["project"]["dependencies"]
    }
    failures = []
    checked = 0
    for line in (dashboard / "requirements.txt").read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        requirement = Requirement(line)
        if requirement.marker and not requirement.marker.evaluate():
            continue
        checked += 1
        if canonicalize_name(requirement.name) not in declared:
            failures.append(f"{requirement.name}: missing from root pyproject.toml")
        try:
            installed = version(requirement.name)
        except PackageNotFoundError:
            failures.append(f"{requirement.name}: not installed")
            continue
        if installed not in requirement.specifier:
            failures.append(f"{requirement.name}: {installed} does not satisfy {requirement.specifier}")
    if failures:
        print("Dashboard runtime dependency check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print(f"Dashboard runtime dependencies OK ({checked} checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())