"""Bounded Cartesian coaxial/TEM port contract; never substitutes a discrete feed."""
import math


def validate_coax(port):
    """Validate resolved drawing-unit values before mutating CSXCAD."""
    def bad(message):
        raise ValueError(message)
    if not isinstance(port.get("direction"), str) or port["direction"] not in ("x", "y", "z"):
        bad("coaxial direction must be x, y or z")
    axis = "xyz".index(port["direction"])
    a, b = port.get("start"), port.get("stop")
    if not isinstance(a, (list, tuple)) or not isinstance(b, (list, tuple)) or len(a) != 3 or len(b) != 3:
        bad("coaxial endpoints must be finite triples")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in [*a, *b]):
        bad("coaxial endpoints must be finite triples")
    if any(abs(a[k] - b[k]) > 1e-9 for k in range(3) if k != axis) or a[axis] == b[axis]:
        bad("coaxial endpoints must share a centerline along the selected axis")
    radii = [port.get(key) for key in ("r_i", "r_o", "r_os")]
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in radii) or not 0 < radii[0] < radii[1] < radii[2]:
        bad("coaxial radii must satisfy 0 < inner radius < shield inner radius < shield outer radius")
    # Native Cartesian current probes are square loops of this half-width. A loop
    # crossing the shield also encloses return current and is not an inner-current probe.
    if math.sqrt(2) * (radii[0] + .1 * (radii[1] - radii[0])) >= radii[1]:
        bad("coaxial annulus is too narrow for the native square current probe; increase shield inner radius")
    length = abs(b[axis] - a[axis])
    feed, measurement = port.get("feed_shift", 0), port.get("measurement_shift")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (feed, measurement)) or not 0 <= feed < length or not 0 < measurement < length:
        bad("coaxial feed offset must be inside the line and measurement offset strictly inside its length")
    if port.get("excite", True) and measurement <= feed:
        bad("coaxial measurement plane must be beyond the excitation plane")
    if port.get("termination", "open") not in ("open", "short") or "Feed_R" in port or "feed_R" in port or "R" in port:
        bad("coaxial termination supports open or short only; finite feed resistance is unsupported")
    if port.get("mode", "TEM") != "TEM" or "group" in port or "reference_impedance" in port:
        bad("coaxial ports support TEM only, without grouped feeds or custom reference impedance")
    return axis


def resolved_coax(port, materials, num):
    """Resolve schema expressions and material references without creating geometry."""
    resolved = dict(port)
    for key in ("start", "stop"):
        resolved[key] = [num(v, key) for v in port[key]]
    for key in ("r_i", "r_o", "r_os", "feed_shift", "measurement_shift"):
        resolved[key] = num(port.get(key, 0 if key == "feed_shift" else None), key)
    validate_coax(resolved)
    conductor = next((m for m in materials if m.get("name") == port.get("conductor")), None)
    if conductor is None or conductor.get("kind") != "metal" or conductor.get("conductivity") is not None:
        raise ValueError("coaxial conductor must reference a PEC metal material")
    dielectric = None
    if port.get("dielectric") is not None:
        dielectric = next((m for m in materials if m.get("name") == port["dielectric"]), None)
        if dielectric is None or dielectric.get("kind") != "dielectric" or dielectric.get("dispersion") is not None:
            raise ValueError("coaxial dielectric must reference a nondispersive dielectric material or air")
    resolved["eps_r"] = num(dielectric.get("eps_r", 1), "eps_r") if dielectric else 1.0
    resolved["mu_r"] = num(dielectric.get("mu_r", 1), "mu_r") if dielectric else 1.0
    resolved["tan_d"] = num(dielectric.get("tan_d", 0), "tan_d") if dielectric else 0.0
    resolved["tan_d_freq"] = num(dielectric["tan_d_freq"], "tan_d_freq") * 1e9 if dielectric and dielectric.get("tan_d_freq") is not None else None
    if resolved["eps_r"] <= 0 or resolved["mu_r"] <= 0 or resolved["tan_d"] < 0:
        raise ValueError("coaxial dielectric requires positive permittivity/permeability and nonnegative loss tangent")
    return resolved


def validate_mesh(sim, port):
    """Require resolved annulus and three interior TL probe planes, outside absorbing cells."""
    import numpy as np
    axis = validate_coax(port)
    if sim.mesh.GetMeshType() != 0:
        raise ValueError("native coaxial ports currently require a Cartesian mesh")
    a, b = port["start"], port["stop"]
    sign = 1 if b[axis] > a[axis] else -1
    lines = [np.asarray(sim.mesh.GetLines(k)) for k in range(3)]
    if any(len(v) < 4 or np.any(np.diff(v) <= 0) for v in lines):
        raise ValueError("coaxial port requires a completed increasing mesh before native probe construction")
    limit = min(port["r_i"] / 2, (port["r_o"] - port["r_i"]) / 3)
    for k in range(3):
        if k == axis:
            continue
        v = lines[k]
        if v[0] > a[k] - port["r_os"] or v[-1] < a[k] + port["r_os"]:
            raise ValueError("coaxial shield must fit inside the transverse mesh")
        radial_pml = [int(str(sim.boundaries[2*k+side]).split("_")[-1]) if str(sim.boundaries[2*k+side]).startswith("PML_") else 0 for side in (0, 1)]
        if (radial_pml[0] >= len(v) or radial_pml[1] >= len(v)
                or a[k] - port["r_os"] < v[radial_pml[0]]
                or a[k] + port["r_os"] > v[len(v)-radial_pml[1]-1]):
            raise ValueError("coaxial cross-section and shield must remain outside transverse PML")
        widths = np.diff(v)[(v[:-1] < a[k] + port["r_o"]) & (v[1:] > a[k] - port["r_o"])]
        if np.max(widths) > limit * (1 + 1e-9):
            raise ValueError("coaxial mesh is too coarse: use at least three cells across the annulus and two per inner radius")
    v = lines[axis]
    if min(a[axis], b[axis]) < v[0] or max(a[axis], b[axis]) > v[-1]:
        raise ValueError("coaxial endpoints must fit inside the axial mesh")
    target = a[axis] + sign * port["measurement_shift"]
    idx = int(np.argmin(abs(v - target)))
    pml = [int(str(sim.boundaries[2 * axis + side]).split("_")[-1]) if str(sim.boundaries[2 * axis + side]).startswith("PML_") else 0 for side in (0, 1)]
    if port.get("termination", "open") == "short" and (pml[0] >= len(v) or pml[1] >= len(v)
            or a[axis] < v[pml[0]] or a[axis] > v[len(v)-pml[1]-1]):
        raise ValueError("coaxial physical short termination must remain outside axial PML")
    if idx <= pml[0] or idx >= len(v) - pml[1] - 1 or not min(a[axis], b[axis]) < v[idx-1] < v[idx+1] < max(a[axis], b[axis]):
        raise ValueError("coaxial measurement needs three adjacent planes strictly inside the line and outside PML")
    if not np.isclose(v[idx]-v[idx-1], v[idx+1]-v[idx], rtol=1e-6, atol=1e-12):
        raise ValueError("coaxial native TL probes require equally spaced mesh lines around the measurement plane")
    feed_idx = int(np.argmin(abs(v - (a[axis] + sign * port.get("feed_shift", 0)))))
    if port.get("excite", True) and (feed_idx <= pml[0] or feed_idx >= len(v) - pml[1] - 1 or sign * (v[idx] - v[feed_idx]) <= 0):
        raise ValueError("coaxial excitation must be outside PML and before the measurement plane")
    return float(v[idx])
