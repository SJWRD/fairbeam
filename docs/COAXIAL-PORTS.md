# Experimental coaxial TEM ports

This implementation is experimental CPU support, not a generally qualified port model. It uses native
`openEMS.AddCoaxialPort`; it never substitutes a discrete port. Windows construction and bounded
air-line controls have been exercised. macOS, GPU engines, dielectric-filled lines and general
RF structures still require qualification before release.

The run API currently rejects GPU runs containing coaxial ports. Availability of CUDA or Metal
does not establish parity for weighted radial excitation or transmission-line probes. The CPU
path is experimental too; native API presence is a capability check, not accuracy qualification.

## Release scope

For 0.7.3, the maintainer approved the native CPU integration with a visible mesh-convergence
warning in the port editor and design checks. Same-model parity was demonstrated on the
retained synthetic air-line control only. This release decision does not turn the failed
analytical gates below into passes. No staircase geometry or fitted phase correction is shipped.
GPU remains explicitly rejected; other platforms require their own runtime qualification.

## Contract

An editable `coaxial` port stores its axis, centerline endpoints, inner-conductor radius `r_i`,
shield inner/outer radii `r_o`/`r_os`, PEC material reference, optional dielectric material
reference, excitation flag, feed offset, measurement offset and `open`/`short` termination.
Lengths use the design's millimeters. Require `0 < r_i < r_o < r_os`; endpoints must share an
axis-aligned centerline. Offsets are measured from start toward stop, including reversed lines.

The port creates conductor/shield and dielectric geometry. It stages that geometry for meshing,
then constructs the native transmission-line probes on the final mesh. At least three cells
across the annulus and two per inner radius are required; this is a refusal threshold, not a
convergence guarantee. Three neighboring measurement planes must lie inside the port and outside
PML, with the excitation before the measurement plane and outside PML. The bundle records the
actual snapped measurement plane.

The native square current loop must fit inside the shield: require
`sqrt(2) * (r_i + 0.1 * (r_o - r_i)) < r_o`. A narrower annulus can enclose return current
and is rejected. The three axial probe planes must be equally spaced because the native TL
estimator uses symmetric differences and an arithmetic current average. A physical short
termination must also remain outside axial PML. These checks do not replace mesh convergence.

Finite feed resistance is unsupported upstream and rejected. `open` and `short` describe the
physical feed termination. They do not define an absorbing matched load: a through line normally
continues into an absorbing boundary. Dispersive filling, finite-conductivity conductor references,
cylindrical meshes, grouped feeds and custom complex reference impedance are outside this draft.
Missing native API support produces an error rather than a different source model.

Output waves use the positive real lossless TEM reference
`Zref = eta0/(2*pi) * sqrt(mu_r/eps_r) * ln(r_o/r_i)` independently of the feed termination.
Native differential-TL complex impedance is retained in `native_tem_z_re`/`native_tem_z_im`.
This separates physical loading, numerical impedance estimation and output normalization.

CST-compatible VBA macro export retains the generated conductor and filling geometry and writes the
native coaxial port as a one-mode waveguide port on its start (feed) plane. Its free-coordinate face is
a square to the shield's outer radius, so it holds the whole cross-section, and it is oriented toward
the stop end. Its reference plane is moved into the line to the openEMS measurement plane. The
openEMS feed termination is not modelled: the macro's port terminates the line itself. The macro
carries the exact port in a `fairbeam-data` record and warns that the TEM mode, orientation and
reference plane should be checked after running it. No macro execution or cross-solver equivalence
has been established.

## Reproducible controls

`scripts/validate-coaxial-native.py` builds a synthetic air-filled PEC coax with radii 1/3/3.5 mm,
a 100 mm length, 1–2 GHz band, CPU with two threads, and a -50 dB energy criterion. It retains
the run manifest, raw solve directory and complex CSV values. Use a new output directory per run:

```sh
python scripts/validate-coaxial-native.py --case through --step .25 --out /path/to/new/run
```

Cases are through, open (PMC end plane) and short (PEC end plane). Compare successive transverse
refinements and the known TEM line phase before interpreting a result. `--reuse-solve` reprocesses
retained probes and records their directory without starting another solver.

The first through refinement reduced complex error against the analytical line, but residual
phase and impedance error remains material. These observations are not a release acceptance claim.
The checkpoint below separates refinement, reference-plane, boundary and filled-line evidence.
Test passivity and convergence separately from solver completion.

## Qualification checkpoint: Windows CPU, October 11, 2026

**Historical numerical checkpoint; not a general accuracy qualification.** Local tests found no difference between
Fairbeam's port construction and the installed native API on the same air-line control, but
the analytical and mesh-convergence gates below are not all satisfied. Solver energy decay
does not certify S-parameter accuracy.

The predeclared limits were 0.05 absolute complex response error, 1% magnitude error, 1% power
excess, 0.025 complex change between the last two transverse meshes and 0.005 for nuisance
controls. Complex errors are absolute differences, **not percentage accuracy**.

| Synthetic control | Transverse step (mm) | Max. complex error | Max. phase error |
| --- | ---: | ---: | ---: |
| Air through | 0.25 | 0.11469 | 6.574 degrees |
| Air through | 0.125 | 0.06211 | 3.559 degrees |
| Air through | 0.0625 | 0.03389 | 1.942 degrees |
| Air open, PMC end plane | 0.125 | 0.14181 | 8.134 degrees |
| Air short, PEC end plane | 0.125 | 0.18929 | 10.859 degrees |
| Homogeneous dielectric through, epsilon = 2.1 | 0.125 | 0.08285 | 4.748 degrees |

The finest through run meets the single-run response limit, but its change from the previous
mesh is 0.02823, above the 0.025 convergence limit. Halving the axial spacing from 1 to 0.5 mm
changes the response by only 0.000189. Bypassing Fairbeam and calling `AddCoaxialPort` directly
produces a maximum complex difference of zero against the coarse wrapped control.

Increasing axial PML from 8 to 10 cells changes the response by 0.00002714; rotating the line
from z to x and reversing it changes it by 0.00000136. Transporting the measurement plane
using analytical beta leaves a 0.01230 difference; the measured numerical beta reduces it
to 0.000130. The latter is a numerical self-consistency check, not an accuracy correction.
The complete two-drive `B A^-1` matrix has a maximum power eigenvalue of 1.000110 (0.0110%
excess, below the 1% allowance) and reciprocity error 0.00000133. Its transmission complex
error remains 0.06195, so full-matrix extraction does not remove the propagation error.

[Compact results and hashes](benchmarks/coaxial-tem-windows-20261011/summary.json),
[predeclared study contract](benchmarks/coaxial-tem-windows-20261011/study-contract.json) and
[complex full S matrix](benchmarks/coaxial-tem-windows-20261011/full-s.csv) are retained with
the individual complex CSV files and the two raw incident/reflected wave arrays. The same
folder includes a convergence plot. These timings are not a performance benchmark.

A diagnostic grid-aligned PEC staircase reduces coarse through phase error to 0.249 degrees,
but changes the estimated impedance to 59.88–59.90 ohms instead of the circular-line reference
65.87 ohms. It changes the geometry and is **not a production correction**. Together with the
transverse refinement trend, it implicates the numerical representation of curved PEC
boundaries; it does not establish a general solver fix. No fitted phase or impedance correction
has been applied. An ideal PMC open-plane control is not a radiating open coax aperture.

The runner also accepts `--axial-step`, `--eps`, `--axis`, `--reverse`, `--measurement`, `--pml`,
`--drive 2` and diagnostic `--native` / `--staircase-control` options. Two through drives retain
incident and reflected waves for a full `S = B A^-1` passivity check. `--cell-constant` tests
material averaging separately; it did not remove the coarse air-line phase error. Original
probes, the generated model, exact runner copy and complex samples are retained per run.

Do not extrapolate these Windows CPU controls to macOS, GPU, lossy filling, arbitrary devices
or all coax dimensions. The next engineering decision is a consistent, converged curved-PEC
discretization that preserves both propagation and impedance, followed by the same controls.

Inspected runtime: openEMS Python `0.37.0rc3`, `ports.py` SHA-256
`0daec44373d6a06395f1c47b252b004df4e99ee2fca5525b486e934451dcc1c1`.
The version string alone does not establish API availability for another packaged runtime.

Upstream API: <https://docs.openems.de/en/latest/python/openEMS/ports.html#coaxial-port>.

### Follow-up: placement and consistent PEC cross-section

Four additional local controls are retained in [the follow-up record](benchmarks/coaxial-tem-windows-20261011/followup/summary.json).
Translating the transverse mesh by half or quarter of a 0.25 mm cell, without moving the physical
circle or ports, gives 6.148 and 8.276 degrees of through-phase error. A favorable mesh offset
therefore does not establish a general correction.

The diagnostic staircase at 0.125 and 0.0625 mm gives 0.259 and 0.263 degrees of phase error.
Its maximum complex impedance error relative to 65.871 ohms decreases from 5.36% to 2.42%,
but still misses the separately predeclared 2% impedance gate. This is evidence of refinement
toward the circular reference, not permission to replace production geometry. The finer run
used six CPU threads instead of two; no performance comparison or thread-parity claim is made.
Original open/short, dielectric and reference-plane qualification remains unresolved.

The loaded Windows `openEMS.dll` and `CSXCAD.dll` were identified in the running process and
their SHA-256 hashes matched the retained original distribution. The follow-up record contains
both hashes. The runner now records actual loaded DLL identities, supports bounded
`--transverse-offset` diagnostics and an explicit positive `--threads` count. No runtime binary,
production meshing strategy, acceptance threshold or published release was changed.
