"""Native Cartesian microstrip/CPW feed contract and final-mesh probe adapter."""
from pathlib import Path
import math

KINDS = ("microstrip", "cpw")


def compare_responses(first, second, *, complex_limit=.02, db_limit=.5, db_floor=-30):
    """Full-curve check on a common grid; no fitting, phase shift or smoothing.

    The limits are explicit, configurable admission thresholds, not a universal
    accuracy guarantee. Callers must separately check energy, mesh and reference
    planes. Magnitude agreement never overrides a failed complex comparison.
    """
    import numpy as np
    for limit in (complex_limit,db_limit):
        if not np.isfinite(limit) or limit<=0:raise ValueError("response limits must be finite and positive")
    if not np.isfinite(db_floor) or db_floor>=0:raise ValueError("response dB floor must be finite and negative")
    arrays=[]
    for curve in (first,second):
        f=np.asarray(curve["frequency"],float)
        z=np.asarray(curve["s11_re"],float)+1j*np.asarray(curve["s11_im"],float)
        if f.ndim!=1 or z.shape!=f.shape or len(f)<3 or not np.isfinite(f).all() or not np.isfinite(z).all() or np.any(np.diff(f)<=0):
            raise ValueError("response needs finite complex samples on an increasing frequency grid")
        arrays.append((f,z))
    if not np.array_equal(arrays[0][0],arrays[1][0]):
        raise ValueError("response comparisons require the same frequency grid")
    a,b=arrays[0][1],arrays[1][1]
    floor=10**(db_floor/20)
    complex_rmse=float(np.sqrt(np.mean(abs(a-b)**2)))
    db_rmse=float(np.sqrt(np.mean((20*np.log10(np.maximum(abs(a),floor))-20*np.log10(np.maximum(abs(b),floor)))**2)))
    return dict(complex_rmse=complex_rmse,floor_db_rmse=db_rmse,
        limits=dict(complex_rmse=complex_limit,floor_db_rmse=db_limit,db_floor=db_floor),
        passed=bool(complex_rmse<=complex_limit and db_rmse<=db_limit))


def validate_planar(port):
    def number(key, default=None):
        x = port.get(key, default)
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
            raise ValueError(f"planar {key} must be a finite number")
        return x
    if port.get("type") not in KINDS:
        raise ValueError("planar port type must be microstrip or cpw")
    if port.get("direction") not in ("x", "y", "z") or port.get("excitation_direction") not in ("x", "y", "z"):
        raise ValueError("planar propagation and excitation directions must be x, y or z")
    k, e = "xyz".index(port["direction"]), "xyz".index(port["excitation_direction"])
    if k == e:
        raise ValueError("planar propagation and excitation directions must differ")
    a, b = port.get("start"), port.get("stop")
    if not isinstance(a, (list, tuple)) or not isinstance(b, (list, tuple)) or len(a) != 3 or len(b) != 3:
        raise ValueError("planar endpoints must be finite triples")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in [*a, *b]):
        raise ValueError("planar endpoints must be finite triples")
    other = 3-k-e
    if a[k] == b[k]:
        raise ValueError("planar line must have nonzero propagation length")
    if port["type"] == "microstrip" and (a[e] == b[e] or a[other] == b[other]):
        raise ValueError("microstrip box must span strip width and strip-to-ground height")
    if port["type"] == "cpw" and (a[e] == b[e] or a[other] != b[other] or number("gap") <= 0):
        raise ValueError("CPW box must span the center strip in width, remain in one plane and have a positive gap")
    length = abs(b[k]-a[k])
    feed, meas = number("feed_shift", 0), number("measurement_shift")
    if not (0 <= feed < length and 0 < meas < length):
        raise ValueError("planar feed offset must be inside the line and measurement offset strictly inside its length")
    if port.get("excite", True) and feed >= meas:
        raise ValueError("planar measurement plane must be beyond the excitation plane")
    if number("reference_ohm", 50) <= 0:
        raise ValueError("planar reference impedance must be positive and real")
    if port.get("termination", "open") not in ("open", "short", "pml"):
        raise ValueError("planar physical termination must be open, short or pml")
    if any(x in port for x in ("group", "reference_impedance", "Feed_R", "feed_R")):
        raise ValueError("planar ports do not support grouped feeds, complex references or finite feed resistance")
    return k, e


def resolved_planar(port, materials, num):
    out = dict(port)
    for key in ("start", "stop"):
        out[key] = [num(v, key) for v in port[key]]
    for key, default in (("feed_shift", 0), ("measurement_shift", None), ("reference_ohm", 50)):
        out[key] = num(port.get(key, default), key)
    if port["type"] == "cpw":
        out["gap"] = num(port.get("gap"), "gap")
    validate_planar(out)
    material = next((m for m in materials if m.get("name") == port.get("conductor")), None)
    if material is None or material.get("kind") != "metal":
        raise ValueError("planar conductor must reference a metal material")
    out["conductivity"] = None if material.get("conductivity") is None else num(material["conductivity"], "conductivity")
    out["thickness"] = num(material.get("thickness", .035), "thickness")
    if out["thickness"] <= 0 or (out["conductivity"] is not None and out["conductivity"] <= 0):
        raise ValueError("planar metal conductivity and sheet thickness must be positive")
    return out


def align_automatic_probes(sim, protected=None):
    """Make final automatic TL stencils symmetric without creating sliver cells.

    Replace the longer-side neighbor, rather than inserting its mirror beside
    it. The two observation intervals use the smaller existing distance; the
    outer intervals can only grow, so this operation cannot lower the minimum
    cell size. Required geometry, user lines and other port stencils are never
    moved. A conflict fails before changing the grid. Manual meshes are checked
    by ``validate_mesh`` and are not passed through this automatic-only helper.
    """
    import numpy as np
    records=[p for p in getattr(sim,"ports",[]) if p.get("type") in KINDS]
    axes={validate_planar(p)[0] for p in records}
    grids={k:np.asarray(sim.mesh.GetLines(k),float).copy() for k in axes}
    fixed={k:list(protected[k]) if protected is not None else [] for k in axes}
    centers=[]
    for record in records:
        k,_=validate_planar(record)
        sign=np.sign(record["stop"][k]-record["start"][k])
        center=record["start"][k]+sign*record["measurement_shift"]
        centers.append((k,center))
        fixed[k].append(center)
    for k,center in centers:
        lines=grids[k]
        idx=int(np.argmin(abs(lines-center))) if len(lines) else 0
        if not 0<idx<len(lines)-1 or abs(lines[idx]-center)>1e-9:
            raise ValueError("planar measurement needs its center and neighboring automatic mesh planes")
        center=lines[idx]
        left,right=center-lines[idx-1],lines[idx+1]-center
        if min(left,right)<=0:
            raise ValueError("planar automatic mesh planes must be strictly increasing")
        if not np.isclose(left,right,rtol=1e-10,atol=1e-12):
            delta=min(left,right)
            for neighbor,target in ((idx-1,center-delta),(idx+1,center+delta)):
                if abs(lines[neighbor]-target)<=1e-12:
                    continue
                if any(abs(lines[neighbor]-value)<=1e-9 for value in fixed[k]):
                    raise ValueError("automatic planar probe symmetry conflicts with a protected mesh plane; "
                                     "choose a measurement offset in a uniform line section or a symmetric manual mesh")
                lines[neighbor]=target
        fixed[k].extend(lines[idx-1:idx+2])
    # Overlapping port observation regions must remain valid together. Nothing
    # is written until all stencils have passed, including protected conflicts.
    for k,center in centers:
        lines=grids[k];idx=int(np.argmin(abs(lines-center)))
        if not np.isclose(lines[idx]-lines[idx-1],lines[idx+1]-lines[idx],rtol=1e-10,atol=1e-12):
            raise ValueError("automatic planar measurement stencils overlap incompatibly")
    for k,lines in grids.items():
        sim.mesh.SetLines("xyz"[k],lines)


def validate_mesh(sim, record):
    import numpy as np
    k, e = validate_planar(record)
    grid = [np.asarray(sim.mesh.GetLines(j), float) for j in range(3)]
    if sim.mesh.GetMeshType() != 0 or any(len(v) < 5 or np.any(np.diff(v) <= 0) for v in grid):
        raise ValueError("planar ports require a completed increasing Cartesian mesh")
    a, b = record["start"], record["stop"]
    sign = np.sign(b[k]-a[k]); lines = grid[k]
    idx = int(np.argmin(abs(lines-(a[k]+sign*record["measurement_shift"]))))
    source = int(np.argmin(abs(lines-(a[k]+sign*record["feed_shift"]))))
    def pml(j, side):
        value = str(sim.boundaries[2*j+side])
        return int(value.split("_")[-1]) if value.startswith("PML_") else 0
    if idx <= pml(k,0) or idx >= len(lines)-pml(k,1)-1:
        raise ValueError("planar measurement needs three interior planes outside PML")
    if not min(a[k],b[k]) < lines[idx-1] < lines[idx+1] < max(a[k],b[k]):
        raise ValueError("planar measurement probes must lie strictly inside the port length")
    if not np.isclose(lines[idx]-lines[idx-1], lines[idx+1]-lines[idx], rtol=1e-6, atol=1e-12):
        raise ValueError("planar native TL probes require equally spaced neighboring measurement planes")
    if record["excite"] and (source <= pml(k,0) or source >= len(lines)-pml(k,1)-1 or sign*(lines[idx]-lines[source]) <= 0):
        raise ValueError("planar excitation must lie outside PML and before the measurement plane")
    if record["termination"] == "short" and not lines[pml(k,0)] < a[k] < lines[len(lines)-pml(k,1)-1]:
        raise ValueError("planar short termination must remain outside PML")
    if record["termination"] == "pml":
        side = 0 if sign > 0 else 1
        in_pml = a[k] <= lines[pml(k,0)] if side == 0 else a[k] >= lines[len(lines)-pml(k,1)-1]
        if pml(k,side) == 0 or not in_pml or not lines[0] <= a[k] <= lines[-1]:
            raise ValueError("planar PML termination requires the physical strip start inside the incoming axial PML")
    normal = e if record["type"] == "microstrip" else 3-k-e
    heights = (a[e], b[e]) if record["type"] == "microstrip" else (a[normal],)
    for height in heights:
        if min(abs(grid[normal]-height)) > 1e-8:
            raise ValueError("planar metal and ground planes must align with mesh lines")
    hidx = int(np.argmin(abs(grid[normal]-a[normal])))
    if record["type"] == "cpw" and not pml(normal,0)+2 <= hidx <= len(grid[normal])-pml(normal,1)-3:
        raise ValueError("CPW current probes need two mesh lines on both sides of the metal plane outside PML")
    for j in range(3):
        if j == k:
            continue
        lo, hi = min(a[j],b[j]), max(a[j],b[j])
        if record["type"] == "cpw" and j == e:
            lo -= record["gap"]; hi += record["gap"]
        v = grid[j]
        if lo < v[pml(j,0)] or hi > v[len(v)-pml(j,1)-1]:
            raise ValueError("planar cross-section must fit outside transverse PML")
    # Minimum resolution is an admission check, not a convergence certificate.
    intervals = [(e, min(a[e],b[e]), max(a[e],b[e]))] if record["type"] == "microstrip" else [
        (e,min(a[e],b[e])-record["gap"],min(a[e],b[e])),
        (e,max(a[e],b[e]),max(a[e],b[e])+record["gap"])]
    for j,lo,hi in intervals:
        sizes = np.diff(grid[j])[(grid[j][:-1] < hi) & (grid[j][1:] > lo)]
        if not len(sizes) or max(sizes) > (hi-lo)/3*(1+1e-8):
            raise ValueError("planar mesh requires at least three cells across substrate height or each CPW gap")
    # Verify actual conductors at the source and the three measurement sections.
    # Merely naming a material must not create an ungrounded or shorted feed.
    sim.csx.Update()  # Polygon point queries need the current native geometry cache.
    normal = e if record["type"] == "microstrip" else 3-k-e
    width = 3-k-normal
    for position in sorted(set([float(lines[source])] if record["excite"] else []) | set(lines[idx-1:idx+2])):
        point = [(a[j]+b[j])/2 for j in range(3)]
        point[k], point[normal] = position, a[normal]
        samples = [point.copy()]
        if record["type"] == "microstrip":
            point[normal] = b[normal]
            samples.append(point.copy())
        else:
            for side in (-1,1):
                point[width] = (min(a[width],b[width])-1.1*record["gap"] if side < 0
                                else max(a[width],b[width])+1.1*record["gap"])
                samples.append(point.copy())
        for point in samples:
            prop = sim.csx.GetPropertyByCoordPriority(point)
            if prop is None or prop.GetTypeString() not in ("Metal","ConductingSheet"):
                raise ValueError("planar source and measurement sections require a continuous center strip and matching ground conductors")
        # Check the voltage-integration gaps are not shorted by a conductor.
        if record["type"] == "microstrip":
            gaps = [[(a[j]+b[j])/2 for j in range(3)]]
        else:
            gaps = [samples[0].copy(), samples[0].copy()]
            gaps[0][width] = min(a[width],b[width])-record["gap"]/2
            gaps[1][width] = max(a[width],b[width])+record["gap"]/2
        for point in gaps:
            point[k] = position
            prop = sim.csx.GetPropertyByCoordPriority(point)
            if prop is not None and prop.GetTypeString() in ("Metal","ConductingSheet"):
                raise ValueError("planar voltage-integration gap is shorted by metal")
    return {"reference_plane": float(lines[idx]), "actual_source_plane": float(lines[source]),
            "actual_measurement_shift": float(abs(lines[idx]-a[k]))}


class NativePlanarPort:
    """Retain native TL impedance/beta, use an explicit real output reference."""
    def __init__(self, native, reference):
        self.native, self.reference = native, reference

    def __getattr__(self, key):
        return getattr(self.native, key)

    def CalcPort(self, path, frequency):
        import numpy as np
        self.native.CalcPort(path, frequency)
        self.native_line_z = np.asarray(self.native.Z_ref, complex).copy()
        self.native.CalcPort(path, frequency, ref_impedance=self.reference)
        # Some native CPW versions don't expose u_data/u_time. Use the time
        # column of their actual probe, without changing any voltage spectrum.
        for kind, filenames, values in (("u",self.native.U_filenames,self.native.ut_tot),
                                       ("i",self.native.I_filenames,self.native.it_tot)):
            axes = [np.loadtxt(Path(path)/filename, comments="%", usecols=0) for filename in filenames]
            t = axes[0]
            if t.ndim != 1 or len(t) != len(values) or len(t) < 2 or not np.isfinite(t).all() or np.any(np.diff(t) <= 0):
                raise ValueError("native planar probe time axes are inconsistent with their wave arrays")
            if any(not np.array_equal(t, other) for other in axes[1:]):
                raise ValueError("native planar probes must have identical time axes within each voltage/current set")
            setattr(self, kind+"_time", t)
