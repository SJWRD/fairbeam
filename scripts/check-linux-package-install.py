#!/usr/bin/env python3
"""Install a trusted CI-built package only inside a disposable Ubuntu container.

No Fairbeam process, graphical session, solver, runtime installer or updater is run.
Docker must already be available; this script never installs host dependencies.
"""

import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import tempfile


IMAGE = "ubuntu:24.04"
CONTAINER_CHECK = r"""
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive LC_ALL=C
test "$(dpkg --print-architecture)" = amd64
test "$(dpkg-deb --field /package/fairbeam.deb Package)" = fairbeam
test "$(dpkg-deb --field /package/fairbeam.deb Architecture)" = amd64
version=$(dpkg-deb --field /package/fairbeam.deb Version)
printf 'source_sha=%s\n' "$SOURCE_SHA"
sha256sum /package/fairbeam.deb
cat /etc/os-release
mkdir -p /root/Documents/Fairbeam/qualification
sentinel=/root/Documents/Fairbeam/qualification/keep.txt
printf 'Owned qualification fixture; preserve across package removal.\n' > "$sentinel"
sentinel_sha=$(sha256sum "$sentinel")
apt-get update
apt-get install -y --no-install-recommends /package/fairbeam.deb
test "$(dpkg-query -W -f='${Status}' fairbeam)" = 'install ok installed'
test "$(dpkg-query -W -f='${Version}' fairbeam)" = "$version"
test -x /usr/bin/fairbeam
# Check resolution BEFORE diagnostics can add libraries and hide undeclared dependencies.
ldd /usr/bin/fairbeam > /tmp/fairbeam-ldd.txt
cat /tmp/fairbeam-ldd.txt
if grep -q 'not found' /tmp/fairbeam-ldd.txt; then
  echo 'Unresolved runtime dependency' >&2
  exit 1
fi
apt-get install -y --no-install-recommends binutils desktop-file-utils
readelf -h /usr/bin/fairbeam > /tmp/fairbeam-elf.txt
cat /tmp/fairbeam-elf.txt
grep -Eq 'Class:[[:space:]]+ELF64' /tmp/fairbeam-elf.txt
grep -Eq 'Machine:[[:space:]]+Advanced Micro Devices X86-64' /tmp/fairbeam-elf.txt
desktop=/usr/share/applications/Fairbeam.desktop
desktop-file-validate "$desktop"
grep -Eq '^Exec=fairbeam([[:space:]]|$)' "$desktop"
icon=$(sed -n 's/^Icon=//p' "$desktop")
test -n "$icon"
test "$(basename "$icon")" = "$icon"
find /usr/share/icons/hicolor -type f -name "$icon.png" -print > /tmp/fairbeam-icons.txt
test -s /tmp/fairbeam-icons.txt
for resource in ui/index.html python/fairbeam/_meta.py runtime/pins.json LICENSE.txt THIRD-PARTY-NOTICES.md; do
  test -s "/usr/lib/Fairbeam/$resource"
done
dpkg-query -W -f='${Package} ${Version} ${Architecture}\n' fairbeam
apt-get purge -y fairbeam
test ! -e /usr/bin/fairbeam
test ! -e "$desktop"
test ! -e /usr/lib/Fairbeam/ui/index.html
test "$(sha256sum "$sentinel")" = "$sentinel_sha"
printf 'PASS: package install, declared-library resolution, static resources, purge and owned sentinel.\n'
printf 'NOT TESTED: GUI, first-run runtime, solver, updates or a real user workspace.\n'
"""


def check_package(package: Path, source_sha: str) -> None:
    package = package.resolve(strict=True)
    if not package.is_file() or package.suffix != ".deb":
        raise ValueError("Expected an existing .deb file")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", source_sha):
        raise ValueError("Expected the full source commit SHA")
    # Docker's --mount CSV syntax treats commas specially, even in a discrete argv value.
    if "," in str(package):
        raise ValueError("Package path must not contain a comma")
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    print(f"source_sha={source_sha.lower()}\npackage_sha256={digest}\npackage_bytes={package.stat().st_size}", flush=True)
    subprocess.run(["docker", "pull", "--platform", "linux/amd64", IMAGE], check=True)
    subprocess.run(["docker", "image", "inspect", IMAGE, "--format", "image_id={{.Id}} digests={{json .RepoDigests}}"], check=True)
    with tempfile.TemporaryDirectory(prefix="fairbeam-package-check-") as temporary:
        cidfile = Path(temporary) / "container.cid"
        try:
            subprocess.run([
                "docker", "run", "--rm", "--platform", "linux/amd64",
                "--cidfile", str(cidfile), "--env", f"SOURCE_SHA={source_sha.lower()}",
                "--mount", f"type=bind,source={package},target=/package/fairbeam.deb,readonly",
                IMAGE, "/bin/bash", "-c", CONTAINER_CHECK,
            ], check=True)
        finally:
            # --rm covers normal completion; the CID file also lets us clean up an interrupted run.
            # Never remove by user-supplied name or enumerate other containers.
            if cidfile.exists():
                cid = cidfile.read_text(encoding="ascii").strip()
                if re.fullmatch(r"[0-9a-f]{64}", cid):
                    subprocess.run(["docker", "rm", "--force", cid], check=False,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--source-sha", required=True)
    arguments = parser.parse_args()
    check_package(arguments.package, arguments.source_sha)
