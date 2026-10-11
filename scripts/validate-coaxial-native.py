"""Bounded synthetic coax controls. Run sequentially with a native openEMS Python environment.

Independent reference: lossless TEM line Z0=eta0/(2*pi)*ln(b/a), beta=2*pi*f/c.
These controls assess the Cartesian port implementation, not arbitrary cylindrical sources.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import time
import shutil
import subprocess
from datetime import datetime, timezone

import numpy as np
import openEMS
from fairbeam.simulation import Simulation


def loaded_native_libraries():
    """Record actual loaded Windows binaries, not a guessed installation folder."""
    if platform.system() != "Windows":
        return {}
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel.GetModuleHandleW.restype = wintypes.HMODULE
    kernel.GetModuleFileNameW.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
    kernel.GetModuleFileNameW.restype = wintypes.DWORD
    result = {}
    for name in ("openEMS.dll", "CSXCAD.dll"):
        handle = kernel.GetModuleHandleW(name)
        buffer = ctypes.create_unicode_buffer(32768)
        size = kernel.GetModuleFileNameW(handle, buffer, len(buffer)) if handle else 0
        if not size or size >= len(buffer):
            raise RuntimeError(f"Cannot identify loaded native library: {name}")
        path = Path(buffer.value)
        result[name] = dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    return result


parser = argparse.ArgumentParser()
parser.add_argument("--out", required=True)
parser.add_argument("--case", choices=["through", "open", "short"], default="through")
parser.add_argument("--step", type=float, default=0.25)
parser.add_argument("--axial-step", type=float, default=1.0)
parser.add_argument("--eps", type=float, default=1.0)
parser.add_argument("--axis", choices=list("xyz"), default="z")
parser.add_argument("--reverse", action="store_true")
parser.add_argument("--measurement", type=float, default=25.0)
parser.add_argument("--pml", type=int, default=8)
parser.add_argument("--end-db", type=float, default=-50.0)
parser.add_argument("--max-steps", type=int, default=90000)
parser.add_argument("--cell-constant", action="store_true", help="Diagnostic material-averaging control, not a port correction")
parser.add_argument("--native", action="store_true", help="Bypass Fairbeam's port construction; use installed AddCoaxialPort directly")
parser.add_argument("--drive", type=int, choices=[1, 2], default=1)
parser.add_argument("--threads", type=int, default=2)
parser.add_argument("--staircase-control", action="store_true", help="Diagnostic grid-aligned PEC cross-section; changes geometry, never a production correction")
parser.add_argument("--transverse-offset", type=float, default=0, help="Diagnostic transverse mesh translation in cell fractions; physical geometry is unchanged")
parser.add_argument("--reuse-solve", help="Reprocess retained raw probes without starting another solve")
args = parser.parse_args()
if args.threads < 1:
    parser.error("--threads must be positive")
if not np.isfinite(args.transverse_offset) or not -.5 <= args.transverse_offset <= .5:
    parser.error("--transverse-offset must be finite and between -0.5 and 0.5 cells")
if args.drive == 2 and args.case != "through":
    parser.error("--drive 2 is only meaningful for a two-port through line")
if min(args.step, args.axial_step, args.eps) <= 0:
    parser.error("mesh steps and relative permittivity must be positive")
out = Path(args.out)
out.mkdir(parents=True, exist_ok=False)
shutil.copy2(__file__, out / "generator.py")
start_time = time.time()
manifest = dict(case=args.case, transverse_step_mm=args.step, domain_mm=[[-3.5, -3.5, -50], [3.5, 3.5, 50]],
                radii_mm=[1, 3, 3.5], band_hz=[1e9, 2e9], source="synthetic analytical control",
                openems_version=openEMS.__version__, runtime=openEMS.__file__, python=platform.python_version(),
                native_libraries=loaded_native_libraries(),
                generator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), status="started")
manifest.update(parameters=vars(args), started_utc=datetime.now(timezone.utc).isoformat(),
                repo_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                repo_dirty=bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()),
                assumptions="synthetic lossless homogeneous PEC coax; analytical TEM; no measured hardware",
                analytic_acceptance=dict(max_complex_error=.05, max_magnitude_error=.01, max_power_excess=.01),
                ports_module_sha256=hashlib.sha256(Path(openEMS.__file__).with_name("ports.py").read_bytes()).hexdigest())
(out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
try:
    axis = "xyz".index(args.axis)
    sign = -1 if args.reverse else 1
    def point(z):
        v = [0., 0., 0.]
        v[axis] = sign*z
        return v
    boundary = f"PML_{args.pml}" if args.case == "through" else "PMC" if args.case == "open" else "PEC"
    boundaries = ["PEC"]*6
    boundaries[2*axis:2*axis+2] = [f"PML_{args.pml}", boundary][::sign]
    sim = Simulation(1e9, 2e9, excitation="gauss", boundaries=boundaries, end_criteria_db=args.end_db, max_timesteps=args.max_steps)
    if args.cell_constant:
        sim.fdtd.SetCellConstantMaterial(True)
    metal = sim.metal("Analytical coax PEC")
    metal.AddCylinder(point(-50), point(50), 1, priority=5)
    metal.AddCylindricalShell(point(-50), point(50), 3.25, .5, priority=5)
    fill = dict(dielectric="Synthetic dielectric", eps_r=args.eps) if args.eps != 1 else {}
    if fill:
        sim.dielectric("Uniform filling", args.eps).AddCylindricalShell(point(-50), point(50), 2, 2, priority=4)
    port_specs = [(1, point(-50), point(-10), args.drive == 1)]
    if args.case == "through":
        port_specs.append((2, point(50), point(10), args.drive == 2))
    if not args.native:
        for number, a, b, excited in port_specs:
            sim.coaxial_port(number, a, b, args.axis, 1, 3, 3.5, excite=excited, feed_shift=12 if excited else 0, measurement_shift=args.measurement, **fill)
    transverse_lines = np.linspace(-3.5, 3.5, int(round(7 / args.step)) + 1)
    if args.transverse_offset:
        # Pad inside the enclosing PEC region so a translated domain still contains
        # the complete physical shield. The circle and all port coordinates stay fixed.
        transverse_lines = np.linspace(-3.5-args.step, 3.5+args.step, int(round(7 / args.step)) + 3)
        transverse_lines += args.transverse_offset*args.step
    for k in range(3):
        sim.mesh.AddLine(k, np.linspace(-50, 50, int(round(100 / args.axial_step))+1) if k == axis else transverse_lines)
    sim.finalize_coaxial_ports()
    if args.native:
        for number, a, b, excited in port_specs:
            pec = sim.csx.AddMetal(f"Direct native conductor {number}")
            mat = sim.csx.AddMaterial(f"Direct native fill {number}", epsilon=args.eps) if fill else None
            native = sim.fdtd.AddCoaxialPort(number, pec, mat, a, b, args.axis, 1, 3, 3.5,
                                           excite=1 if excited else 0, priority=5, FeedShift=12 if excited else 0,
                                           MeasPlaneShift=args.measurement, Feed_R=np.inf)
            sim._port_objs.append(native)
            sim.ports.append(dict(number=number, type="coaxial", R=376.730313668/(2*np.pi)*np.log(3)/np.sqrt(args.eps),
                                  reference_plane=a[axis]+np.sign(b[axis]-a[axis])*native.measplane_shift,
                                  actual_measurement_shift=float(native.measplane_shift)))
    if args.staircase_control:
        if args.eps != 1:
            raise ValueError("staircase diagnostic is restricted to homogeneous air")
        for prop in sim.csx.GetAllProperties():
            if prop.GetTypeString() == "Metal":
                sim.csx.DeleteProperty(prop)
        voxels = sim.csx.AddMetal("Diagnostic grid-aligned PEC")
        transverse = [k for k in range(3) if k != axis]
        u, v = (sim.mesh.GetLines(k) for k in transverse)
        for i in range(len(u)-1):
            for j in range(len(v)-1):
                radius = np.hypot((u[i]+u[i+1])/2, (v[j]+v[j+1])/2)
                if radius < 1 or radius > 3:
                    a, b = point(-50), point(50)
                    a[transverse[0]], b[transverse[0]] = u[i], u[i+1]
                    a[transverse[1]], b[transverse[1]] = v[j], v[j+1]
                    voxels.AddBox(a, b, priority=5)
    manifest.update(status="model_built", cells=int(np.prod([len(sim.mesh.GetLines(a)) - 1 for a in "xyz"])), ports=sim.ports)
    manifest["mesh_lines_mm"] = {a: np.asarray(sim.mesh.GetLines(a)).tolist() for a in "xyz"}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    sim.csx.Write2XML(str(out / "model.xml"))
    manifest["model_xml_sha256"] = hashlib.sha256((out / "model.xml").read_bytes()).hexdigest()
    solve = Path(args.reuse_solve).resolve() if args.reuse_solve else out / "solve"
    manifest["status"] = "solver_started" if not args.reuse_solve else "reprocessing"
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    stats = {"reused_probe_directory": str(solve)} if args.reuse_solve else sim.run(str(solve), threads=args.threads, engine="cpu")
    freq = np.linspace(1e9, 2e9, 51)
    for port in sim._port_objs:
        port.CalcPort(str(solve), freq)
    native_z = np.asarray(sim._port_objs[args.drive-1].Z_ref).copy()
    native_beta = np.asarray(sim._port_objs[args.drive-1].beta).copy()
    for port, metadata in zip(sim._port_objs, sim.ports):
        port.CalcPort(str(solve), freq, ref_impedance=metadata["R"])
    np.savez(out / "waves.npz", frequency_hz=freq,
             native_beta=native_beta, native_impedance=native_z,
             incident=np.array([p.uf_inc/np.sqrt(m["R"]) for p, m in zip(sim._port_objs, sim.ports)]),
             reflected=np.array([p.uf_ref/np.sqrt(m["R"]) for p, m in zip(sim._port_objs, sim.ports)]))
    first = sim._port_objs[args.drive-1]
    s11 = first.uf_ref / first.uf_inc
    response = sim._port_objs[2-args.drive].uf_ref / first.uf_inc if args.case == "through" else s11
    length_m = abs(sim.ports[1]["reference_plane"]-sim.ports[0]["reference_plane"])*.001 if args.case == "through" else 2*abs(sign*50-sim.ports[0]["reference_plane"])*.001
    reference = np.exp(-1j * 2 * np.pi * freq / 299792458 * np.sqrt(args.eps)*length_m) * (-1 if args.case == "short" else 1)
    np.savetxt(out / "complex.csv", np.c_[freq, s11.real, s11.imag, response.real, response.imag, reference.real, reference.imag], delimiter=",", header="frequency_hz,reflection_re,reflection_im,response_re,response_im,reference_re,reference_im", comments="")
    manifest.update(status="results_exported", stats=stats, elapsed_s=time.time()-start_time,
                    finite=bool(np.isfinite(response).all()), max_complex_error=float(max(abs(response-reference))),
                    max_magnitude_error=float(max(abs(abs(response)-1))), max_s11=float(max(abs(s11))),
                    nominal_z0_ohm=sim.ports[0]["R"], measured_z_real_range=[float(min(native_z.real)), float(max(native_z.real))],
                    max_phase_error_deg=float(max(abs(np.angle(response/reference, deg=True)))),
                    max_power_excess=float(max(abs(s11)**2 + (abs(response)**2 if args.case == "through" else 0)-1)),
                    reference_length_m=length_m, boundaries=boundaries)
    manifest["native_beta_over_analytic_range"] = [float(min(native_beta.real/(2*np.pi*freq/299792458*np.sqrt(args.eps)))), float(max(native_beta.real/(2*np.pi*freq/299792458*np.sqrt(args.eps))))]
    manifest["analytic_pass"] = bool(manifest["finite"] and manifest["max_complex_error"] <= .05 and manifest["max_magnitude_error"] <= .01 and manifest["max_power_excess"] <= .01)
    manifest["complex_csv_sha256"] = hashlib.sha256((out / "complex.csv").read_bytes()).hexdigest()
    manifest["power_check_scope"] = "Single excitation column only; full B A^-1 matrix requires both through drives"
    manifest["solver_converged"] = stats.get("converged")
    # A single analytical agreement is not cross-run mesh/port convergence.
    manifest["status"] = "results_exported"
    manifest["qualification"] = "pending cross-run convergence and nuisance checks"
    manifest["diagnostic_geometry_change"] = args.staircase_control
    manifest["domain_mm"] = [[float(min(sim.mesh.GetLines(k))) for k in range(3)], [float(max(sim.mesh.GetLines(k))) for k in range(3)]]
except Exception as exc:
    manifest.update(status="failed", error=str(exc))
    raise
finally:
    manifest["elapsed_s"] = time.time()-start_time
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
print(json.dumps(manifest, indent=2))
