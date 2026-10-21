# Native planar transmission-line ports

This topic branch adds experimental Cartesian `microstrip` and `cpw` ports. They use the
runtime's native `AddMSLPort` and `AddCPWPort`, respectively. A missing API is an error; a
rectangular-waveguide or discrete source is never substituted. API availability and successful
construction do not qualify a runtime, antenna, or mesh for accuracy.

## Geometry and normalization

`direction` is the propagation axis. `excitation_direction` is the transverse electric-field
axis: strip-to-ground for microstrip, across the two gaps for CPW. Endpoints span the center
strip's width and length. For microstrip, `start` is on the strip plane and `stop` on the ground
plane; for CPW, both are on the same metal plane and `gap` is the width of each gap. Reversed
propagation is supported.

The port creates only the center strip. Supply the ground conductor(s), dielectric, connecting
strip, and any continuation explicitly. `conductor` refers to a metal design material; PEC and
finite-conductivity sheets are supported. Finite conductivity and sheet thickness are retained
in the bundle and exported Python. This does not model arbitrary roughness or conductor volume.

Offsets are positive distances from start toward stop, in drawing units (millimeters in designs).
Require an interior measurement offset and, for an active port, a source before that plane.
`termination` describes the **physical** incoming end: `open`, `short`, or `pml`. PML requires
the actual strip start inside the incoming axial PML. It does not extend the geometry for you.
Finite feed resistances, grouped planar feeds, and complex wave references are rejected.

`reference_ohm` is a positive real output-wave reference, default 50 ohms. It is independent of
the physical termination and the native line-impedance estimate. Results retain complex native
impedance and propagation constant as `native_line_z_re/im` and `beta_re/im`. Requested offsets,
actual source plane, and actual measurement plane remain in the bundle. Time traces use the
native probes' recorded time axes, including their voltage/current staggering.

```python
sim.microstrip_port(1, [-60, -1, 1], [-6, 1, 0], "x", "z",
    conductor="PEC", feed_shift=30, measurement_shift=45,
    termination="pml", reference_ohm=50)
```

This call alone is not a complete model: ground, dielectric, air space, and mesh are still needed.
In the Python API, `conductor` is a provenance label: pass `conductivity` and `thickness` explicitly
for a lossy strip, as with `Simulation.metal`. A design file instead resolves these settings from
its referenced metal material before calling the API.
The design editor and feed-creation dialog expose both native types. Preview geometry includes
the generated strip, and the source-to-measurement arrow is not drawn as a diagonal discrete
gap. VBA macro export with these ports is explicitly refused; geometry-only export is available.
An export package keeps the native port in `project.json` and includes an explicitly labeled
geometry-only macro. It never replaces the feed with a discrete source.

Python export from a Design is supported. The reverse Python-to-Design conversion (including
the Python panel's Apply action) is not yet implemented for native planar ports and explicitly
refuses the conversion. Keep such a model as Python, or edit the existing Design directly;
do not remove its port to force conversion.

## Mesh admission and convergence

Native probes are constructed after the final mesh. Metal planes must be exact mesh planes;
in-plane free edges may use the thirds rule. Three equally spaced neighboring measurement
planes must be strictly inside the port and outside PML. Source and CPW current-loop planes
must also remain outside PML. The automatic mesher retains a symmetric final-grid stencil after
grading and feature refinement. It replaces a free neighbor instead of inserting a second,
nearly coincident plane. Required geometry and user lines are protected; an incompatible
stencil fails with an explicit error. A manual mesh is checked, not rewritten.

At least three cells across substrate height or each CPW gap are required. Source and measurement
sections are checked for actual strip/ground contact and unshorted voltage-integration gaps.
These are admission checks, not a convergence certificate. Verify uniform cross-section around
all probes, refine both sheet-normal and transverse resolution, and check source distance and
absorbing-boundary clearance separately.

A native CPW port sums its two gap-voltage observations. It is not a general multiconductor
modal solver: unequal ground potentials or asymmetric loads may excite additional slotline or
common-mode fields. A single S11 trace or a symmetric through-line test does not establish mode
purity or equivalent termination of those fields. Check return paths and modal/source sensitivity
independently before using an asymmetric structure as a validation reference.

Mesh-convergence reports require at least three native planar levels and compare the full complex S11
curve, not only its minimum. The extra default guard requires complex RMSE <= 0.02 and
-30 dB-floor magnitude RMSE <= 0.5 dB on an identical frequency grid. The standalone
`fairbeam.planar.compare_responses` helper accepts explicit alternative limits. Energy and local
mesh checks remain required. A matching magnitude with a different phase fails this guard.
These defaults are numerical admission thresholds, not a universal physical accuracy claim.

## Reproducible feed controls

Run one case at a time in a native openEMS environment, with `PYTHONPATH=python`:

```text
python scripts/validate-planar-native.py --kind microstrip --level 0 --engine cpu --out /new/control/msl-m0
python scripts/validate-planar-native.py --kind cpw --level 0 --engine cpu --out /new/control/cpw-m0
```

Use fresh output directories and repeat at levels 1 and 2; CPU and GPU are separate qualification
targets. The script retains raw probes, generator snapshot, settings, complex curves, and pass/fail
metrics. It compares a synthetic through line with zero-thickness quasi-static references;
finite ground width, dynamic dispersion, and staircasing limit those approximations. Passing one
control does not establish correctness for other cross-sections. Failed levels must remain in
the record. No antenna-specific tuning, hidden line extension, or fitted phase correction is used.

Dielectric loss must be checked independently: `Simulation.dielectric(tan_d=...,tan_d_freq=...)`
uses constant conductivity, not a constant loss tangent over the band. Use an explicitly recorded
dispersive model when a different frequency-dependent loss law is required.
