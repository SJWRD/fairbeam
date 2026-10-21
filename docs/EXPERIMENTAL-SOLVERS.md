# Experimental solvers

openEMS is the default solver. **Elmer FEM — Experimental eigenmodes** and **Elmer FEM · Experimental antenna** are opt-in research paths with narrower geometry and result support. Installing a backend or completing a run does not establish numerical accuracy for your design.

## Setup in the desktop app

Open **Simulation › Experimental solvers**, or use **Elmer setup and history** in the Run dialog. Choose Elmer and check availability before submitting a run.

On Windows, **Install Elmer** downloads the portable package pinned by Fairbeam after you request it. The package is checked and stored in the app's data folder, separately from the openEMS runtime. It is not a system-wide installation. Leave the path blank to use the managed copy. A folder you enter, `FAIRBEAM_ELMER_ROOT`, or Elmer on `PATH` takes precedence.

On macOS and Linux, install Elmer yourself. Select an installation containing `bin/ElmerSolver` and `bin/ElmerGrid`, or its binary directory. Availability checks locate the executables; they do not qualify the physical model or every native dependency. No backend is installed automatically.

If an installed app reports `No module named fairbeam_elmer`, update or repair Fairbeam itself. Installing Elmer binaries alone does not provide Fairbeam's Python adapter. Source contributors should use the separate [research setup and evidence](RESEARCH-SOLVERS.md).

## Eigenmodes of the current design

Save the design, open **Run**, and select **Elmer FEM — Experimental eigenmodes**. Review the enclosure bounds in millimeters, maximum cell size and target frequency. Explicitly accept the closed PEC enclosure: all six faces are conducting walls, not an open radiation boundary.

This path accepts axis-aligned box volumes with constant, positive, lossless isotropic dielectric properties or PEC. It rejects overlaps, ports, lumped elements, sheets, curves, transforms, cuts, loss and dispersion. The solver searches for two modes near the target frequency; these need not be the lowest modes.

The bounded solve allows up to 4,000 elements and 10,000 nodes, one CPU thread and 120 seconds of native execution. Results are eigenfrequencies and retained fields/logs, not antenna S parameters, gain or a radiation pattern. Completion is recorded as `results_exported`, not `results_validated`.

## Driven antenna experiments

Choose **Elmer FEM · Experimental antenna** for a design made of axis-aligned dielectric boxes and PEC sheets, with one ideal line discrete port. Accept the finite rectangular FEM terminal width and review air padding, mesh size and frequency samples. Both terminal ends must contact PEC sheets. These settings belong to the FEM snapshot; the original design and openEMS setup remain unchanged.

The open region uses first-order absorbing boundaries, not PML. Finite-conductivity metals are approximated as PEC in the FEM snapshot, so conductor loss is omitted and affected materials are recorded. Curves, transforms, cuts, additional lumped components and multiple ports are rejected.

Limits are 10,000 cells, 3–21 sweep samples plus the requested far-field frequency, one CPU thread and 180 seconds per frequency. Results include complex S11, a normalized 3D pattern and directivity cuts; gain and efficiency are not provided. Exported results are not a convergence certificate. Review the [recorded experiments and outstanding qualification](RESEARCH-SOLVERS.md) before interpreting a new model.

## Queue, results and cancellation

Experimental runs use the shared serial queue. Inspect failures, raw inputs, settings, meshes and logs in solver history. Closing the panel does not cancel a run; use the queue's cancellation control. Interrupted work is retained rather than automatically restarted.

History keeps the input snapshot and hashes with each run. Experimental results do not replace the current openEMS antenna result in the viewer. Compare mesh levels and appropriate physical references yourself before drawing an engineering conclusion.

## Other experimental workflows

**Coaxial TEM (experimental)** is an openEMS port workflow with specific geometry and release limits; see [Coaxial ports](COAXIAL-PORTS.md). It is not an Elmer installation option.

The periodic-cell experiment requires a separately built research backend. Stock openEMS and the normal GPU engine are not substitutes. Its supported scope is normal incidence, zero x/y phase shift and co-polar fundamental reflection/transmission below the first vacuum diffraction threshold. General Floquet ports, oblique incidence and higher-order modes are not implemented. See the [research notes](RESEARCH-SOLVERS.md#periodic-unit-cell) for prerequisites and retained numerical evidence.
