"""Validate the release version shared by Python and asset manifests."""

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STABLE_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


def release_version(root: Path = ROOT) -> str:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    uv_lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    package_lock = json.loads((root / "package-lock.json").read_text(encoding="utf-8"))

    version = project["project"]["version"]
    if not STABLE_VERSION.fullmatch(version):
        raise ValueError(f"Release version must be stable X.Y.Z; found {version!r}")

    uv_versions = [
        item["version"] for item in uv_lock["package"] if item["name"] == project["project"]["name"]
    ]
    versions = {
        "pyproject.toml": version,
        "uv.lock": uv_versions[0] if len(uv_versions) == 1 else uv_versions,
        "package.json": package["version"],
        "package-lock.json": package_lock["version"],
        "package-lock.json root": package_lock["packages"][""]["version"],
    }
    if any(value != version for value in versions.values()):
        raise ValueError(f"Release versions differ: {versions}")
    return version


if __name__ == "__main__":
    print(release_version())
