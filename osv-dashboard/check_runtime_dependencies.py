"""Fail publishing early if dashboard runtime dependencies are unsafe."""
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import sys
import tomllib

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


# These tools are useful for local development and browser tests, but their
# transitive Nix closures make the production image substantially larger.
FORBIDDEN_NIX_PACKAGE_PREFIXES = {
    "cargo": "Rust/Cargo toolchain",
    "clang": "C/C++ compiler toolchain",
    "cmake": "native build toolchain",
    "gcc": "C/C++ compiler toolchain",
    "git-svn": "Git-SVN and Perl dependencies",
    "libclang": "compiler libraries",
    "llvm": "compiler libraries",
    "playwright-driver": "Playwright browsers and driver",
    "rust": "Rust/Cargo toolchain",
    "rustc": "Rust/Cargo toolchain",
}


def matches_package_family(package, family):
    suffix = package[len(family) :]
    return (
        package == family
        or suffix.startswith("-")
        or suffix[:1].isdigit()
        or suffix.startswith("packages")
    )


def unsafe_nix_packages(replit_config):
    """Return configured Nix packages known to inflate production images."""
    packages = replit_config.get("nix", {}).get("packages", [])
    if not isinstance(packages, list):
        return [("<invalid packages value>", "the Nix package list must be an array")]

    failures = []
    for package in packages:
        if not isinstance(package, str):
            failures.append((repr(package), "Nix package names must be strings"))
            continue
        normalized = package.casefold()
        for prefix, reason in FORBIDDEN_NIX_PACKAGE_PREFIXES.items():
            if normalized.startswith(prefix) and matches_package_family(normalized, prefix):
                failures.append((package, reason))
                break
    return failures


def check_nix_runtime_packages(project_root):
    replit_path = project_root / ".replit"
    replit_config = tomllib.loads(replit_path.read_text())
    failures = unsafe_nix_packages(replit_config)
    if failures:
        print("Production Nix dependency check failed:", file=sys.stderr)
        print(
            "Development-only Nix packages increase the transitive closure "
            "included in the production image:",
            file=sys.stderr,
        )
        for package, reason in failures:
            print(f"  - {package}: {reason}", file=sys.stderr)
        print(
            "Remove these packages from [nix].packages in .replit. "
            "LibreOffice, Tesseract and Poppler remain allowed runtime tools.",
            file=sys.stderr,
        )
        return False
    print(
        "Production Nix dependencies OK "
        f"({len(replit_config.get('nix', {}).get('packages', []))} checked)"
    )
    return True


def main():
    dashboard = Path(__file__).resolve().parent
    project_root = dashboard.parent
    project = tomllib.loads((project_root / "pyproject.toml").read_text())
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
    python_dependencies_ok = not failures
    if not python_dependencies_ok:
        print("Dashboard runtime dependency check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
    else:
        print(f"Dashboard runtime dependencies OK ({checked} checked)")

    nix_dependencies_ok = check_nix_runtime_packages(project_root)
    return 0 if python_dependencies_ok and nix_dependencies_ok else 1


if __name__ == "__main__":
    sys.exit(main())