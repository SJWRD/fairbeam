# Getting started

<!--
  The website renders this file as https://fairbeam.org/docs/getting-started.html (the app's
  Help › Getting Started Guide opens it through https://fairbeam.org/guide.html). This file is the
  only copy: edit it here.
-->

This guide is for people trying the Fairbeam desktop app: install it, then design, simulate and
read the results of a patch antenna. The full reference for the designer is
[DESIGNER.md](DESIGNER.md). To build Fairbeam from source instead, see
[Running Fairbeam from source](FROM-SOURCE.md). In the app, Help › Getting Started Guide opens the
public copy of this page.

## 1. Install

Download the installer from [fairbeam.org](https://fairbeam.org) or the
[releases page](https://github.com/ismailakdag/fairbeam-releases/releases).

- **macOS** (Apple silicon, macOS 27 or newer): open the `.dmg` and drag Fairbeam to Applications.
  The app is signed with a Developer ID and notarized by Apple.
- **Windows** (10/11, x64): run the `-setup.exe`. It installs for your user, without an
  administrator prompt. The installer is not code-signed, so SmartScreen may show "Windows protected
  your PC": choose **More info › Run anyway**. If double-clicking a `.fb` file does not offer Fairbeam, see [Opening .fb files](WINDOWS.md#known-limitations).

**First start.** Fairbeam needs its simulation runtime (Python, openEMS and the Fairbeam
package). The setup screen says "Fairbeam needs to install its runtime": choose **Install the
runtime** (the button shows the download size; duration depends on your connection and system). If
you already have an openEMS installation, **Use an existing Python…** points
Fairbeam at it instead. The app then opens on the **Start** screen. Later starts skip this step,
and newer versions are offered in the app (Help › Check for updates).

## 2. A first-design walkthrough

### Create a design

1. On **Start**, under **New design**, type a name (for example `My patch`), pick **Patch antenna
   starter** ("Probe-fed patch over a ground plane", under Printed) and click **Create and
   open the designer**. The other starting points are ready-to-run parametric designs:
   **Empty design** (a band and copper, no geometry), **Half-wave dipole**,
   **Quarter-wave monopole** and **Open-ended waveguide** (under Basic antennas), **Printed sleeve
   dipole** (867 MHz, no ground plane; under Printed) and **Microstrip line (two-port)** (50 Ω on
   FR-4, for S11 and S21; under Circuits). Each simulates in seconds; this guide follows the patch.
2. The designer opens. Take a look around:
   - the **ribbon** at the top: Home, Modeling, Transform, View, Simulation, Optimize, Post-processing;
   - the **navigation tree** on the left: the design, **Parameters** (6: `f0`, `W`, `L`, `h`, `G`,
     `feed`), **Solids** (Substrate, Ground plane, Patch, each with its shape), **Materials**,
     **Ports** (Port 1, 50 Ω) and **Results** (none yet);
   - the **3D view** in the middle: drag to orbit, scroll to zoom, right- or middle-drag to pan;
     Iso / Top / Front / Right / Bottom set the camera and the target button (or Space / F) fits
     the model;
   - **Properties** on the right: the fields of what you select;
   - the **dock** at the bottom: Checks and Parameters, and later Run (the live progress), Runs (the
     design's runs side by side) and Log. The plots of a run open as tabs beside the 3D view.
3. Click **Patch** in the tree (or on the model) to see its brick: every value can be a number or
   an expression over the parameters, such as `W/2`.
4. Right-click a solid in the tree or in the 3D view for Hide, Rename, Transform…, Duplicate,
   Delete, **Add a discrete port…**, **Move to component ›** and the Boolean operations. A solid moved
   to a component shows in a folder of the tree; right-click the folder to rename, ungroup or
   delete it.

### Simulation settings

Open the **Simulation** tab of the ribbon:

- **Frequency band**: `f min` and `f max` in GHz. The starter uses `f0 * 0.6` to `f0 * 1.3`, around
  its design frequency `f0` = 2.45 GHz.
- **Boundaries**: the six faces of the simulation box, each open (MUR or PML), an electric wall
  (PEC) or a magnetic wall (PMC). The patch starter uses MUR on all six.
- **Mesh**: **Cells / λ** sets the automatic mesh density; **Mesh settings** has the details,
  **Mesh convergence…** checks whether the mesh is fine enough (see below) and **Mesh view**
  draws the mesh in the 3D view. The status bar shows the cell count and the smallest cell.
- **Ports**: **Discrete** and **Waveguide** add a port; **Add lumped element…** adds an R/L/C load. The experimental coaxial port has separate [limits and setup](COAXIAL-PORTS.md).
- **Monitors**:
  - **Far field**: the radiation pattern, directivity and gain at the listed frequencies (the
    starter records it at `f0`).
  - **Surface current**: adds surface-current maps on the metal sheets at the frequencies you list.
  - **Efficiency**: adds **Efficiency over the band**, the radiation and total efficiency at evenly
    spaced frequencies from f min to f max (post-processing, no extra solver time).
  - **Field plane**: adds an E or H field map on a cut plane through the model (up to four planes,
    each at one to four frequencies); a new one starts just above the model.

  Each of these buttons opens **Simulation settings** at that section; **OK** keeps the changes,
  **Cancel** restores the settings from before.
- **Solver limits**: the end criterion (−60 dB in the starters; an empty project starts at −50 dB)
  and the maximum number of timesteps.

The **Checks** tab of the dock lists anything that stops a run; the status bar shows "Checks
passed" when there is nothing to fix.

### Run

Click **Run** (Simulation tab, or the Run button at the top, or Ctrl/⌘+Enter). The **Run the
simulation** dialog shows:

- **Solver**: leave it on **openEMS** for this walkthrough. The experimental Elmer options have different geometry and validation limits; see [Experimental solvers](EXPERIMENTAL-SOLVERS.md).
- **Engine**: **CPU (multi-threaded)**, or **GPU (Metal or CUDA build)** when the GPU build of openEMS is installed. CPU **Threads** defaults to **Auto**: the hint shows the choice for the mesh size and physical cores. Keep Auto unless you need a specific count;
- **S-parameter sample points** (801 by default) and an optional **Result name**;
- a solver time estimate.

Click **Run** (it reads **Save and run** while there are unsaved changes). The design is saved first; the progress (timesteps, energy decay) shows in the dock.
The starter takes seconds to a minute, depending on the machine.

### Results

When the run is done it appears under **Results** in the tree, with its time and engine:

- **1D Results › S-parameters** opens |S11| as a tab next to the 3D view. Click **Markers** on the
  plot (or press M) for **Add marker**, **Previous minimum** / **Next minimum**, **Automatic
  markers** below a threshold, and a table you can copy.
- **1D Results › Smith chart**, **Impedance**, **VSWR**, and **Efficiency** (the mismatch
  efficiency over the band, with the radiation and total efficiency from the monitor).
- **Farfields › 3D pattern (f = …)** draws the pattern on the model in the 3D view, with the
  far-field card beside it: pick **Directivity**, **Gain** or **Realized gain**, and read the
  maximum, the gain, the realized gain and the radiation and mismatch efficiency. **Farfield
  (f = …)** opens the pattern cuts as a tab.
- **2D/3D Results › Surface current** (with the monitor) shows the current map on the metal, and
  a field-plane node such as **E-field (z = … mm, … GHz)** (with the Field plane monitor) draws
  the E or H map as a heat-map plane in the 3D view, with a color bar in dB or linear; its
  **2D map** node opens it as a heat map tab with axes, a hover readout and the model's outline,
  and **Phase** / **Animate** show the phase and the field over one period.
- **Tables › Summary** (also **Summary** on the Post-processing tab) gives resonance, minimum |S11|, bandwidth, gain and efficiency when the run contains them. Missing quantities stay unavailable.
- **Tables** and **Log**.

The **Post-processing** tab of the ribbon opens the same views. Choosing a geometry tab (Home,
Modeling, …) or clicking a part takes the pattern off the 3D view and shows the design again.

### Change a parameter and compare runs

1. Click **Parameters** in the tree (it opens the Parameters tab of the dock) and change `W`, the
   patch width, for example from 32 to 30 mm. The 3D view follows at once.
2. **Run** again. Results now lists two runs, the newest first.
3. **Ctrl/⌘-click** both runs in the tree (or use **Compare** in the result tab's toolbar, or click
   their labels in the dock's **Runs** tab): the plots draw them together, one color each (up to
   eight runs).

### Is the mesh fine enough?

**Simulation › Mesh convergence…** runs the design at finer and finer automatic meshes (15, 20, 30
and 40 cells per wavelength by default) and compares each run with the previous one: the resonance
frequency, |S11| at the resonance and, with the far field on, the maximum directivity. Before it
starts, the dialog shows the number of cells and a time estimate for every density; **Start**
saves the design and queues the first run, and the next one only when the previous is done and the
results are still changing. The study stops at the first step whose changes are all below the
tolerances and reports **converged at N cells/λ**; **Apply N cells/λ to the design** sets that
density (Undo takes it back). The runs appear under Results in one **Mesh convergence** folder,
whose **Convergence report** opens the table and plots again. If it reports **not converged**,
refine further or check the model. Details: [DESIGNER.md, Mesh convergence](DESIGNER.md#mesh-convergence).

### A parameter sweep

**Optimize › Parameter sweep** opens the **Parameter sweep** dialog: **Add parameter**, choose it (for example
`Patch width (x) (W)`), then **Evenly spaced values** with **Start**, **Stop** and **Sample
count**, or a **Value list**. **Check** shows how many simulations that makes; **Start** saves the
design and queues them. The runs appear under Results in one **Sweep** folder; right-click it for
**Compare all runs**.

### The optimizer

**Optimize › Optimizer** opens **Optimize design**:

- **Vary**: the parameters to change, with a starting value and Min / Max bounds;
- **Goals**: for example **Tune resonance to** 2.45 GHz, **Match at**, **Maximize bandwidth** or
  **Directivity at least**, each with a weight;
- **Evaluations ≤**, **Threads** and the **Method** (Auto picks the secant method for one
  parameter tuned to a frequency; Nelder–Mead, Bayesian, CMA-ES and others for more).

**Start optimization** runs one simulation per evaluation, with the progress in the dock. The
optimization then appears under Results with **Optimization history**, **Open best**, **Save best
as a run** and **Apply best parameters to design**. See [OPTIMIZE.md](OPTIMIZE.md).

## 3. Import a VBA macro

On **Start**, **Import VBA macro…** (also Home › Design › Import VBA macro, or File › Import VBA
macro… in the desktop app) reads a CST-compatible VBA macro (`.bas`, `.mcs`) or a history list saved as text. Before anything is
saved it shows a report: what was created, and every command that was skipped or changed, with its line. Name the design and **Create and open** it. What is
read and what is not: [DESIGNER.md, Importing a VBA macro](DESIGNER.md#importing-a-vba-macro).

## 4. Where your files are

In the desktop app, **Save** writes `<name>.fb` where you choose. **Save as…** creates a separate project; **Open project…** opens one again. The adjacent `<name>.results/` folder holds its saved result bundles. Keep the `.fb` file and this folder together when moving, copying or sharing the project.

A completed run remains available in the workspace. Save the project to include new results when the “New results are ready” notice appears; automatic result attachment uses the saved design snapshot, never silently saving newer edits. Project file details show whether a result matches that snapshot, belongs to an earlier design, is missing or is invalid. These are identity checks, not evidence of convergence.

Removing a design normally removes its workspace copy. In the desktop removal dialog, selecting **Also remove the .fb file and its results folder from their current location** moves the project and its owned results into `Fairbeam Deleted/` beside the project. It does not permanently erase them. Recovery and limitations are described in [Project files (.fb)](FB-DOCUMENT.md).

### Workspace folder

The local working folder defaults to `Documents/Fairbeam` (Windows: `%USERPROFILE%\Documents\Fairbeam`). It also supports JSON-only designs and Python models:

| Folder | Holds |
| --- | --- |
| `models/` | Working designs (`<name>.design.json`), portable `fb_<UUID>` copies and Python models |
| `projects/` | Workspace result bundles and `index.json` |
| `templates/` | Model templates |
| `jobs/` | Run queue state and history |
| `.sim/` | Solver working data |
| `renders/<design-id>/` | Rendered images saved from the app |
| `model-history/` | Saved or removed design/Python recovery copies |

**General settings › Workspace folder** shows the location and **Open folder** opens it. The **Language** and **Decimal separator** settings affect the interface and displayed numbers. Start-screen examples are read-only originals: **Open as new design…** makes your own copy.

## 5. Report a problem

At the bottom of the **Start** screen, **Send feedback: Report a problem** (or **Suggest a
feature**) opens the issue form of the public tracker,
[fairbeam-releases/issues](https://github.com/ismailakdag/fairbeam-releases/issues), in your
browser with the app version and your operating system filled in. The speech-bubble button at
the top (**Send feedback**) and Help › Report a Problem… do the same from any screen. Fairbeam sends nothing by itself.

Helpful to include: what you did and what you expected, a screenshot (View › View › **Screenshot**
saves the visible view), the `.fb` project and its adjacent `.results` folder (or the workspace design for JSON-only work), and the run's log (Results › your run › Log).
