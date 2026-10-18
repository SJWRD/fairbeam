"""Overlay a qualified native CPU build onto the pinned upstream Windows distribution.

Preserves upstream wheels, geometry libraries, tools and notices. Does not download, install,
publish, or change runtime pins. The native source and build provenance travel alongside it.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

PIN = "12cd91de21548987b835ce21f10fd8421d74da78"
UPSTREAM = "948d04e67a0b7ee4b149176e746b23e609a8ac46c525ba75d6c11071f354339a"
NAME = "openems-windows-x64-0.37.0rc3-g12cd91de2-ncpu1-r1"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def add(z, name, data):
    entry = zipfile.ZipInfo(name, (2026, 10, 18, 0, 0, 0))
    entry.compress_type = zipfile.ZIP_DEFLATED
    z.writestr(entry, data)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ("upstream", "stage", "source", "qualification", "provenance", "boost-license", "out"):
        ap.add_argument("--" + name, type=Path, required=True)
    a = ap.parse_args()
    upstream = a.upstream.read_bytes()
    if digest(upstream) != UPSTREAM:
        raise ValueError("unexpected upstream archive hash")
    revision = subprocess.check_output(["git", "-C", str(a.source), "rev-parse", "HEAD"], text=True).strip()
    if revision != PIN:
        raise ValueError("unexpected native source revision")
    # Enforce the same seven source hashes as the already-reviewed rc3 patch set.
    import re
    recipe = Path(__file__).parent / "native-cpu/apply-macos.sh"
    expected = re.findall(r'"(FDTD/[^ ]+) ([a-f0-9]{64})"', recipe.read_text())
    if len(expected) != 7 or any(digest((a.source / p).read_bytes()) != h for p, h in expected):
        raise ValueError("native patches do not match the reviewed rc3 files")
    proof = json.loads((a.qualification / "comparison.json").read_text())
    required = {(v, c) for v in ("official", "baseline") for c in ("patch", "line-p1", "line-p2", "dipole")}
    passed = {(r["reference"], r["case"]) for r in proof["records"] if r["all_numeric_exact"]}
    if not required <= passed or not proof["candidate_vs_baseline_exact"]:
        raise ValueError("patch/two-port/PML qualification has not passed")
    control = json.loads((a.qualification / "candidate/patch/control.json").read_text())
    overlay = {}
    for name in ("openEMS.exe", "openEMS.dll", "nf2ff.exe", "nf2ff.dll", "sar_calc.exe",
                 "boost_thread-vc143-mt-x64-1_91.dll", "boost_program_options-vc143-mt-x64-1_91.dll"):
        data = (a.stage / "bin" / name).read_bytes()
        if name in control["dlls"] and digest(data) != control["dlls"][name]["sha256"]:
            raise ValueError("qualified DLL changed: " + name)
        overlay["openEMS/" + name] = data
    a.out.mkdir(parents=True, exist_ok=True)
    binary, source = a.out / (NAME + ".zip"), a.out / (NAME + "-source.zip")
    if binary.exists() or source.exists():
        raise FileExistsError("refusing to replace an existing immutable pack")
    with zipfile.ZipFile(source, "x") as z:
        for p in sorted(a.source.rglob("*")):
            rel = p.relative_to(a.source)
            if ".git" in rel.parts or not p.is_file():
                continue
            if p.is_symlink():
                raise ValueError("source links are not supported")
            add(z, "openEMS/" + rel.as_posix(), p.read_bytes())
        add(z, "build-provenance.json", a.provenance.read_bytes())
        add(z, "qualification.json", (a.qualification / "comparison.json").read_bytes())
    marker = {"marker": "fairbeam openEMS pack v1", "platform": "windows-x64", "native_cpu": True,
              "openems_commit": PIN, "upstream_sha256": UPSTREAM,
              "source_archive": source.name, "source_sha256": digest(source.read_bytes()),
              "native_overlay": {p: digest(b) for p, b in overlay.items()}}
    overlay["openEMS/openems-pack.json"] = json.dumps(marker, indent=2).encode()
    boost_license = a.boost_license.read_bytes()
    if b"Boost Software License - Version 1.0" not in boost_license:
        raise ValueError("missing Boost license text")
    overlay["openEMS/LICENSE.Boost.txt"] = boost_license
    overlay["openEMS/COPYING.openEMS"] = (a.source / "COPYING").read_bytes()
    overlay["openEMS/NOTICE.Fairbeam.txt"] = (
        "Fairbeam native CPU pack. openEMS source " + PIN + ".\n"
        "Changes: opt-in phase dispatch, UPML scalar cursor and row addressing.\n"
        "Same mesh, arithmetic precision and exact energy stopping policy.\n"
        "Source/build provenance: " + source.name + ".\n"
        "Original upstream archive, wheels, CSXCAD, tools and third-party notices are retained.\n"
        "Additional Boost 1.91 vc143 runtime DLLs use the Boost Software License 1.0.\n"
        "The upstream distribution's unresolved binary provenance exceptions remain;\n"
        "see Fairbeam's third-party license inventory. No SZIP/libaec provenance substitution is claimed.\n"
        "Set FAIRBEAM_NATIVE_CPU=0 to opt out of default native patches.\n"
    ).encode()
    with zipfile.ZipFile(a.upstream) as original, zipfile.ZipFile(binary, "x") as z:
        for item in original.infolist():
            if not item.is_dir() and item.filename not in overlay:
                add(z, item.filename, original.read(item))
        for name, data in sorted(overlay.items()):
            add(z, name, data)
    print(json.dumps({"archive": str(binary), "sha256": digest(binary.read_bytes()),
                      "size": binary.stat().st_size, "source": str(source)}, indent=2))


if __name__ == "__main__":
    main()
