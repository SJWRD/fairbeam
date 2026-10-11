"""Touchstone (v1) export: one-port reflection (.s1p) and N-port S-matrices (.s2p, .s3p, ...), and a
Touchstone v1/v2 reader (:func:`read_touchstone`, :func:`read_snp`, :func:`read_s1p`).

The file is ``# GHz S RI R <z>``: frequency in GHz, then Re/Im of S11. openEMS reports S11 against
the lumped port's own resistance; for a one-port the reflection coefficient can be renormalised
exactly to any reference through the input impedance, ``S' = (Zin - Z) / (Zin + Z)``. By default we
write 50 ohm so the file imports into CST, ADS or scikit-rf without surprises.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from numbers import Real

import numpy as np


def _reference(value) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise ValueError("Touchstone reference impedance must be finite and positive")
    try:
        value = float(value)
    except (ValueError, OverflowError):
        raise ValueError("Touchstone reference impedance must be finite and positive") from None
    if not np.isfinite(value) or value <= 0:
        raise ValueError("Touchstone reference impedance must be finite and positive")
    return value


def _known_phase(bundle):
    if (bundle.get("reference") or {}).get("phaseKnown") is False:
        raise ValueError("Touchstone export requires complex S-parameters with known phase")


def _frequencies(value):
    f = np.asarray(value)
    if (f.dtype.kind not in "fiu" or f.ndim != 1 or not f.size or not np.isfinite(f).all()
            or np.any(f < 0) or np.any(np.diff(f.astype(float)) <= 0)):
        raise ValueError("Touchstone frequencies must be finite, nonnegative and strictly increasing")
    return f.astype(float)


def _complex_samples(real, imaginary, f):
    parts = [np.asarray(part) for part in (real, imaginary)]
    if any(part.dtype.kind not in "fiu" or part.shape != f.shape or not np.isfinite(part).all() for part in parts):
        raise ValueError("Touchstone S-parameter arrays must be finite and match the frequency vector")
    return parts[0].astype(float) + 1j * parts[1].astype(float)


def _matrix(f, s):
    s = np.asarray(s, dtype=complex)
    if s.ndim != 3 or s.shape[0] != len(f) or s.shape[1] < 1 or s.shape[1] != s.shape[2] or not np.isfinite(s).all():
        raise ValueError("Touchstone requires a finite square S-matrix at every frequency")
    return s


def _port_references(bundle, key, scalar, f):
    """Actual real references, never the band-center summary for a waveguide port."""
    pr = (bundle.get("results", {}).get("ports") or {}).get(str(key), {})
    if "z_ref_f" in pr:
        refs = np.asarray(pr["z_ref_f"], dtype=object)
        if refs.shape != f.shape:
            raise ValueError("Touchstone needs a finite positive real reference at every frequency")
        return np.asarray([_reference(v) for v in refs])
    if any(str(p.get("number")) == str(key) and p.get("type") == "waveguide"
           for p in bundle.get("ports", [])):
        raise ValueError("Waveguide Touchstone export needs the actual per-frequency reference impedances")
    return np.full(len(f), _reference(scalar))


def _matrix_references(bundle, f, scalar):
    sp = bundle["results"]["sparams"]
    physical = sp.get("port_numbers", sp["ports"])
    if (not isinstance(physical, list) or len(physical) != len(scalar)
            or any(isinstance(p, bool) or not isinstance(p, int) or p <= 0 for p in physical)
            or len(set(physical)) != len(physical)):
        raise ValueError("Touchstone needs an unambiguous physical port mapping")
    mapped = {str(p) for p in physical}
    frequency_ports = {str(k) for k, pr in (bundle["results"].get("ports") or {}).items() if "z_ref_f" in pr}
    waveguide_ports = {str(p.get("number")) for p in bundle.get("ports", []) if p.get("type") == "waveguide"}
    if not (frequency_ports | waveguide_ports).issubset(mapped):
        raise ValueError("Touchstone frequency references do not match the physical port mapping")
    for key, z in zip(physical, scalar):
        pr = (bundle["results"].get("ports") or {}).get(str(key), {})
        # Compare the two scalar summaries, not the actual vector with its rounded summary.
        # A differing matrix reference may describe already-renormalized data; do not guess.
        if "z_ref_f" in pr and _reference(pr.get("z_ref")) != z:
            raise ValueError("Touchstone matrix and native port reference summaries disagree")
    return np.column_stack([_port_references(bundle, key, z, f) for key, z in zip(physical, scalar)])


def reflection(bundle: dict, port: str | int | None = None, z_ref: float | None = 50.0):
    """Return ``(f_hz, s11_complex, z_ref_used, port_key)`` for one port of a simulated bundle."""
    _known_phase(bundle)
    if z_ref is not None:
        z_ref = _reference(z_ref)
    res = bundle.get("results")
    if not res:
        raise ValueError("bundle has no results (geometry-only?)")
    ports = res["ports"]
    if port is None:
        excited = [str(p["number"]) for p in bundle.get("ports", []) if p.get("excite")]
        port = excited[0] if excited else sorted(ports)[0]
    key = str(port)
    if key not in ports:
        raise ValueError(f"port {key} not in results (available: {', '.join(sorted(ports))})")
    pr = ports[key]
    f = _frequencies(res["frequency"])
    s = _complex_samples(pr["s11_re"], pr["s11_im"], f)
    native = _reference(pr["z_ref"])
    refs = _port_references(bundle, key, native, f)
    if z_ref is None:
        if np.any(refs != refs[0]):
            raise ValueError("Touchstone v1 cannot keep frequency-dependent native references; choose a positive --ref")
        z_ref = float(refs[0])
    if np.all(refs == z_ref):
        return f, s, z_ref, key
    # Scalar form of multiport.renormalize: avoid the singular intermediate impedance at
    # an ideal open (S11=1). Use the full complex S data, not rounded impedance arrays.
    # Scale first so even large finite positive references cannot overflow the sum.
    scale = np.maximum(refs, z_ref)
    g = (z_ref / scale - refs / scale) / (z_ref / scale + refs / scale)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        s = (s - g) / (1 - g * s)
    if not np.isfinite(s).all():
        raise ValueError("Touchstone renormalization produced nonfinite S-parameters")
    return f, s, z_ref, key


def write_s1p(bundle: dict, path, port=None, z_ref: float | None = 50.0) -> str:
    f, s, z, key = reflection(bundle, port, z_ref)
    model = bundle.get("model", {})
    native = bundle["results"]["ports"][key]["z_ref"]
    lines = [
        f"! {bundle.get('name', model.get('name', 'fairbeam'))}",
        f"! generated by Fairbeam {bundle.get('generator', {}).get('version', '')} from an "
        f"{bundle.get('solver', {}).get('engine', 'openEMS')} simulation ({bundle.get('created', '')})",
        f"! model {model.get('id', '')}, port {key}, native port impedance {native:g} ohm"
        + ("" if abs(native - z) < 1e-9 else f", renormalised to {z:g} ohm"),
        *(["! S11 renormalised from the per-frequency real port reference to the fixed R below"]
          if "z_ref_f" in bundle["results"]["ports"][key] else []),
        f"# GHz S RI R {z:.17g}",
    ]
    lines += [f"{fi / 1e9:.17g} {si.real: .17g} {si.imag: .17g}" for fi, si in zip(f, s)]
    text = "\n".join(lines) + "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return text


# ---------------------------------------------------------------------------- N-port

def full_matrix(bundle: dict):
    """``(f_hz, S (n_f, N, N), z_ref list)`` from ``results.sparams``; every port must have been excited
    (a Touchstone file needs the full matrix: run with ``--excite all``)."""
    from .multiport import s_from_section

    _known_phase(bundle)
    res = bundle.get("results") or {}
    sp = res.get("sparams")
    if not sp:
        raise ValueError("bundle has no results.sparams (simulated before multi-port support?)")
    f = _frequencies(res["frequency"])
    n = len(sp["ports"])
    if not n or not sp["s"]:
        raise ValueError("Touchstone requires S-parameter data for at least one port")
    if (not isinstance(sp["ports"], list)
            or any(type(p) is not int or p != i + 1 for i, p in enumerate(sp["ports"]))):
        raise ValueError("Touchstone matrix indices must be 1..N; use port_numbers for physical ports")
    refs = np.asarray(sp["z_ref"], dtype=object)
    if refs.ndim != 1 or len(refs) != n:
        raise ValueError("Touchstone needs one reference impedance per matrix port")
    zr = [_reference(z) for z in refs]
    allowed = {f"{i},{j}" for i in range(1, n + 1) for j in range(1, n + 1)}
    for key, entry in sp["s"].items():
        if key not in allowed:
            raise ValueError("Touchstone S-parameter index is outside the port matrix")
        _complex_samples(entry["re"], entry["im"], f)
    s = s_from_section(sp)
    if np.isnan(s).any():
        missing = sorted({j + 1 for j in range(s.shape[2]) if np.isnan(s[:, :, j]).any()})
        raise ValueError(f"columns for port(s) {missing} were not simulated; run with --excite all "
                         "(or write single-port reflections with --port)")
    return f, _matrix(f, s), zr


def format_snp(f, s, z: float, header: list[str]) -> str:
    """Touchstone v1 text. 2-port data are written column-wise on one line (S11 S21 S12 S22), as the
    format requires; N >= 3 row by row, at most four complex pairs per line."""
    f = _frequencies(f)
    s = _matrix(f, s)
    z = _reference(z)
    n = s.shape[1]
    lines = [f"! {h}" for h in header] + [f"# GHz S RI R {z:.17g}"]

    def pair(v):
        return f"{v.real: .17g} {v.imag: .17g}"

    for k, fk in enumerate(f):
        fs = f"{fk / 1e9:.17g}"
        if n == 1:
            lines.append(f"{fs} {pair(s[k, 0, 0])}")
        elif n == 2:
            lines.append(f"{fs} " + " ".join(pair(s[k, i, j]) for j, i in ((0, 0), (0, 1), (1, 0), (1, 1))))
        else:
            for i in range(n):
                row = [pair(s[k, i, j]) for j in range(n)]
                for c in range(0, n, 4):
                    lead = fs if (i == 0 and c == 0) else " " * len(fs)
                    lines.append(f"{lead} " + " ".join(row[c:c + 4]))
    return "\n".join(lines) + "\n"


def write_snp(bundle: dict, path, z_ref: float | None = 50.0) -> str:
    """Write the bundle's full S-matrix as a Touchstone v1 ``.sNp`` file, renormalised to ``z_ref``
    (``None`` keeps the ports' common impedance)."""
    from .multiport import renormalize

    if z_ref is not None:
        z_ref = _reference(z_ref)
    f, s, zr = full_matrix(bundle)
    n = s.shape[1]
    refs = _matrix_references(bundle, f, zr)
    if z_ref is None:
        if np.any(refs != refs[0, 0]):
            raise ValueError("ports have different or frequency-dependent reference impedances; Touchstone v1 needs one (use --ref)")
        z = float(refs[0, 0])
    else:
        z = float(z_ref)
        if np.any(refs != z):
            try:
                # The full power-wave transform, not an elementwise reflection conversion.
                with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
                    s = np.concatenate([renormalize(s[k:k + 1], refs[k], [z] * n) for k in range(len(f))])
            except np.linalg.LinAlgError:
                raise ValueError("Touchstone renormalization is singular") from None
    model = bundle.get("model", {})
    sp = bundle["results"]["sparams"]
    header = [
        f"{bundle.get('name', model.get('name', 'fairbeam'))}",
        f"generated by Fairbeam {bundle.get('generator', {}).get('version', '')} from an "
        f"{bundle.get('solver', {}).get('engine', 'openEMS')} simulation ({bundle.get('created', '')})",
        f"model {model.get('id', '')}, {n} ports, port impedances {', '.join(f'{v:g}' for v in zr)} ohm"
        + ("" if all(abs(v - z) < 1e-9 for v in zr) else f", renormalised to {z:g} ohm"),
        f"S-matrix method {sp.get('method')}; excited ports {sp.get('excited')}",
        "S-matrix referred to the fixed real R below; per-frequency native references are renormalised",
    ]
    text = format_snp(f, s, z, header)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return text


# ---------------------------------------------------------------------------- reader
#
# Touchstone v1 and v2 reader. src/import/touchstone.ts implements the same rules with the same
# messages; both are checked against examples/import-fixtures/touchstone/expected.json.
#
# - Option line "# <unit> <parameter> <format> R <ref>" (any order, any case, the first one counts;
#   defaults GHz S MA R 50). Units Hz/kHz/MHz/GHz(/THz), formats RI/MA/DB.
# - Y and Z are converted to S (v1: normalised to R; v2: in siemens/ohms); H and G are refused.
# - Data may wrap onto continuation lines anywhere; "!" starts a comment anywhere. 2-port data are
#   S11 S21 S12 S22 (v1, v2 "21_12") or S11 S12 S21 S22 (v2 "12_21"); N >= 3 row by row.
# - A 2-port frequency that is not above the previous one starts the noise block (v1), as does
#   [Noise Data] (v2); noise parameters are counted and skipped.
# - v2 keywords: [Version], [Number of Ports], [Two-Port Data Order], [Number of Frequencies],
#   [Matrix Format] Full/Lower/Upper (mirrored to the full matrix), [Reference] (per-port values;
#   different ones are renormalised to the option line's R, so the result has a single z0),
#   [Network Data], [Noise Data], [Begin/End Information] (skipped), [End] (stops reading).

_UNITS = {"HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9, "THZ": 1e12}
_UNIT_NAMES = {"HZ": "Hz", "KHZ": "kHz", "MHZ": "MHz", "GHZ": "GHz", "THZ": "THz"}
_NUM = re.compile(r"^[-+]?(?:\d+\.?\d*|\.\d+)(?:[eEdD][-+]?\d+)?$")


@dataclass
class Touchstone:
    """A parsed Touchstone file: ``s[k, i, j]`` (zero-based ports) at ``f[k]`` Hz, referred to ``z0``."""

    f: np.ndarray
    s: np.ndarray
    z0: float
    ports: int
    parameter: str          # as written in the file (S, Y or Z); the data are always S
    format: str             # RI, MA or DB
    unit: str               # the file's frequency unit
    version: int            # 1 or 2
    noise_frequencies: int = 0
    warnings: list = field(default_factory=list)


def _numbers(raw: str, line: str) -> list[float]:
    out = []
    for tok in re.split(r"[\s,]+", line):
        if not tok:
            continue
        if not _NUM.match(tok):
            raise ValueError(f'Not a number in Touchstone data: "{raw.strip()}"')
        out.append(float(tok.replace("d", "e").replace("D", "e")))
    return out


def ports_from_name(name) -> int | None:
    m = re.search(r"\.s(\d+)p$", str(name).strip(), re.I)
    return int(m.group(1)) if m else None


def parse_touchstone(text: str, name: str = "", n: int | None = None) -> Touchstone:
    """Parse Touchstone v1/v2 text. The port count comes from ``n``, else [Number of Ports], else the
    ``.sNp`` suffix of ``name``, else the first data line (3 numbers: 1 port, 9: 2 ports)."""
    warn: list[str] = []
    unit, param, fmt, r_opt = "GHZ", "S", "MA", 50.0
    option = False
    version = 1
    ports_kw = None
    order = None
    n_freq = None
    matrix = "FULL"
    reference: list[float] | None = None
    ref_open = False
    section = "network"
    net: list[tuple[str, list[float]]] = []
    noise: list[tuple[str, list[float]]] = []
    for raw in text.lstrip("﻿").splitlines():
        line = raw.split("!")[0].strip()
        if not line:
            continue
        if section == "info":
            if re.match(r"^\[\s*end\s+information\s*\]", line, re.I):
                section = "network"
            continue
        if line.startswith("["):
            m = re.match(r"^\[([^\]]*)\]\s*(.*)$", line)
            if not m:
                raise ValueError(f"Malformed Touchstone keyword: {line}")
            kw, arg = " ".join(m.group(1).lower().split()), m.group(2).strip()
            ref_open = False
            if kw == "version":
                if not re.match(r"^2(\.\d+)?$", arg):
                    raise ValueError(f"Touchstone [Version] {arg} is not supported (2.x)")
                version = 2
            elif kw == "number of ports":
                if not re.match(r"^\d+$", arg) or int(arg) < 1:
                    raise ValueError(f"Invalid [Number of Ports]: {arg}")
                ports_kw = int(arg)
            elif kw == "two-port data order":
                if arg not in ("12_21", "21_12"):
                    raise ValueError(f"Invalid [Two-Port Data Order]: {arg} (12_21 or 21_12)")
                order = arg
            elif kw == "number of frequencies":
                if not re.match(r"^\d+$", arg):
                    raise ValueError(f"Invalid [Number of Frequencies]: {arg}")
                n_freq = int(arg)
            elif kw == "matrix format":
                if arg.upper() not in ("FULL", "LOWER", "UPPER"):
                    raise ValueError(f"Invalid [Matrix Format]: {arg} (Full, Lower or Upper)")
                matrix = arg.upper()
            elif kw == "reference":
                if ports_kw is None:
                    raise ValueError("[Reference] must come after [Number of Ports]")
                reference = _numbers(raw, arg)
                ref_open = len(reference) < ports_kw
            elif kw == "network data":
                section = "network"
            elif kw == "noise data":
                section = "noise"
            elif kw == "begin information":
                section = "info"
            elif kw == "end":
                break
            elif kw == "mixed-mode order":
                raise ValueError("Mixed-mode Touchstone data ([Mixed-Mode Order]) is not supported")
            elif kw != "number of noise frequencies":
                warn.append(f"Touchstone 2 keyword ignored: [{m.group(1).strip()}]")
            continue
        if line.startswith("#"):
            if option:
                continue  # only the first option line counts
            option = True
            tok = line[1:].upper().split()
            i = 0
            while i < len(tok):
                t = tok[i]
                if t in _UNITS:
                    unit = t
                elif t in ("S", "Y", "Z", "H", "G"):
                    param = t
                elif t in ("RI", "MA", "DB"):
                    fmt = t
                elif t == "R":
                    i += 1
                    r_opt = _numbers(raw, tok[i])[0] if i < len(tok) and _NUM.match(tok[i]) else float("nan")
                i += 1
            if param in ("H", "G"):
                raise ValueError(f"Touchstone {param}-parameters are not supported (S, Y and Z only)")
            if not np.isfinite(r_opt) or r_opt <= 0:
                raise ValueError("Invalid reference impedance in the option line")
            continue
        vals = _numbers(raw, line)
        if ref_open:
            reference += vals
            ref_open = len(reference) < ports_kw
        elif section == "noise":
            noise.append((raw, vals))
        else:
            net.append((raw, vals))
    if not option:
        warn.append("No option line: assumed # GHz S MA R 50")
    if not net:
        raise ValueError("No Touchstone network data found")

    from_name = ports_from_name(name)
    if n is not None and ports_kw is not None and n != ports_kw:
        raise ValueError(f"[Number of Ports] is {ports_kw}, but {n} ports were asked for")
    N = n or ports_kw or from_name
    if ports_kw and from_name and ports_kw != from_name:
        warn.append(f"[Number of Ports] {ports_kw} overrides the file name's {from_name}")
    if not N:
        N = {3: 1, 9: 2}.get(len(net[0][1]), 0)
        if not N:
            raise ValueError("Cannot tell the port count: name the file .sNp (or pass n)")
        warn.append(f"Port count inferred from the data: {N}")

    count = N * N if matrix == "FULL" else N * (N + 1) // 2
    per = 1 + 2 * count
    recs: list[list[float]] = []
    cur: list[float] = []
    total = 0
    for idx, (raw, vals) in enumerate(net):
        if not cur and recs and vals[0] <= recs[-1][0]:
            rest = net[idx:]
            if N != 2 or any(len(v) != 5 for _, v in rest):
                raise ValueError("Touchstone frequencies are not strictly increasing")
            noise = rest + noise  # v1: a 2-port frequency that drops back starts the noise block
            break
        total += len(vals)
        cur += vals
        while len(cur) >= per:
            recs.append(cur[:per])
            cur = cur[per:]
    if cur or not recs:
        raise ValueError(f"Touchstone data does not fit {N} port(s): {total} numbers is not a multiple of {per}")
    for raw, vals in noise:
        if len(vals) != 5:
            raise ValueError(f'Touchstone noise data needs 5 numbers per line: "{raw.strip()}"')
    if noise:
        warn.append(f"Noise parameters ignored ({len(noise)} frequencies)")
    if n_freq is not None and n_freq != len(recs):
        raise ValueError(f"[Number of Frequencies] is {n_freq}, but the file has {len(recs)}")

    data = np.asarray(recs, float)
    f = data[:, 0] * _UNITS[unit]
    if np.any(np.diff(f) <= 0):
        raise ValueError("Touchstone frequencies are not strictly increasing")
    a, b = data[:, 1::2], data[:, 2::2]
    if fmt == "RI":
        v = a + 1j * b
    else:
        v = (10 ** (a / 20) if fmt == "DB" else a) * np.exp(1j * np.deg2rad(b))
    nf = len(recs)
    if matrix == "FULL":
        m = v.reshape(nf, N, N)
        if N == 2:
            if order is None and version == 2:
                warn.append("[Two-Port Data Order] missing in a version 2 file: assumed 21_12")
            if (order or "21_12") == "21_12":
                m = np.transpose(m, (0, 2, 1))  # S11 S21 S12 S22
    else:
        m = np.zeros((nf, N, N), complex)
        pairs = [(i, j) for i in range(N) for j in (range(i + 1) if matrix == "LOWER" else range(i, N))]
        for q, (i, j) in enumerate(pairs):
            m[:, i, j] = m[:, j, i] = v[:, q]

    if reference is not None and len(reference) != N:
        raise ValueError(f"[Reference] needs {N} values, found {len(reference)}")
    zp = [float(z) for z in reference] if reference is not None else [r_opt] * N
    if any(not np.isfinite(z) or z <= 0 for z in zp):
        raise ValueError("Invalid reference impedance in [Reference]")
    z0 = zp[0] if all(abs(z - zp[0]) < 1e-12 for z in zp) else r_opt
    eye = np.eye(N)
    if param == "S":
        if any(abs(z - z0) > 1e-12 for z in zp):
            from .multiport import renormalize

            m = renormalize(m, zp, [z0] * N)
            warn.append(f"Per-port references {', '.join(f'{z:g}' for z in zp)} ohm renormalised to {z0:g} ohm")
    else:
        # v1 stores Y and Z normalised to R; v2 in siemens and ohms
        x = m * (r_opt if param == "Z" else 1 / r_opt) if version == 1 else m
        xn = x / z0 if param == "Z" else x * z0
        try:
            m = np.linalg.solve(xn + eye, xn - eye) if param == "Z" else np.linalg.solve(eye + xn, eye - xn)
        except np.linalg.LinAlgError:
            raise ValueError(f"{param}-parameters cannot be converted to S (singular matrix)") from None
        warn.append(f"{param}-parameters converted to S (reference {z0:g} ohm)")
    return Touchstone(f=f, s=m, z0=z0, ports=N, parameter=param, format=fmt, unit=_UNIT_NAMES[unit],
                      version=version, noise_frequencies=len(noise), warnings=warn)


def read_touchstone(path, n: int | None = None) -> Touchstone:
    """Read a Touchstone v1/v2 file (``.sNp`` or ``.ts``); see :func:`parse_touchstone`."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        return parse_touchstone(fh.read(), str(path), n)


def read_snp(path, n: int | None = None):
    """Touchstone reader for any port count. Returns ``(f_hz, S (n_f, N, N), z0)``; Y/Z data are
    converted to S and per-port references renormalised to one (see :func:`parse_touchstone`)."""
    t = read_touchstone(path, n)
    return t.f, t.s, t.z0


def read_s1p(path):
    """One-port Touchstone reader. Returns ``(f_hz, s11, z0)``."""
    t = read_touchstone(path)
    if t.ports != 1:
        raise ValueError(f"{path}: {t.ports}-port data; read_s1p reads one-port files (use read_snp)")
    return t.f, t.s[:, 0, 0], t.z0
