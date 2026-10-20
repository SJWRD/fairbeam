# Focused checks and prerequisites

Run checks from a clean checkout after its own `npm ci`. Individual `scripts/check-*.mjs`
files include both automated regressions and opt-in integration harnesses. Running every
file without its prerequisites is not equivalent to the required checks in
[AGENTS.md](../AGENTS.md) or [CI](../.github/workflows/ci.yml).

## Node checks

These checks do not start a browser, server or solver. They also run as isolated child
processes within the normal `npm run check:designer` gate:

```text
node --experimental-strip-types scripts/check-geometry-cache.mjs
node --experimental-strip-types scripts/check-results-tabs.mjs
node --experimental-strip-types scripts/check-save-as.mjs
```

The geometry cache check compares cached output with a fresh cache after each edit and
checks that expanded coordinates and bounds are reused. Bundle wrappers carry current
shape-selection metadata and need not retain object identity.

## App browser checks

Install Chrome or Edge, and set `FAIRBEAM_PYTHON` to the Python interpreter that can import
the checkout's Fairbeam dependencies. `FAIRBEAM_CHROME` optionally selects the browser
executable. For example, in PowerShell:

```powershell
$env:FAIRBEAM_PYTHON = 'C:/path/to/runtime/python.exe'
$env:FAIRBEAM_CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
node scripts/check-coaxial-port-ui.mjs
node scripts/check-run-profile-dialog.mjs
node scripts/check-shape-workflow.mjs
node scripts/check-telemetry-ui.mjs
```

These harnesses start an isolated development server and run server, use temporary projects
and run headless. They do not run a solver. Run them sequentially. The telemetry harness
uses a fake native telemetry interface; it does not send usage data. It also accepts
`FAIRBEAM_USAGE_URL` for an already-running development server. Docs and telemetry accept
the older `CHROME_PATH` override as well as `FAIRBEAM_CHROME`.

Set `FAIRBEAM_SCREENSHOT_DIR` to save optional docs and telemetry screenshots. No fixed
`/tmp` directory is required.

## Built documentation site

The language check needs the generated documentation site, not the app's Vite server.
Build and serve it locally:

```text
npm run build:site
python -m http.server --bind 127.0.0.1 --directory site-dist 5345
```

With that server running, use a second terminal:

```powershell
$env:FAIRBEAM_DOCS_URL = 'http://127.0.0.1:5345'
npm run check:docs-language
```

The check covers English/Turkish navigation, language persistence, anchors, light/dark
themes, narrow layouts, disabled JavaScript and unavailable browser storage. A missing
or incorrect server is a prerequisite failure, not a successful or skipped check.

## Optional real Elmer integration

`check-elmer-antenna-ui.mjs` runs actual Elmer patch simulations in English and Turkish.
It is separate from the normal no-solver browser checks and is not a native desktop
permission or file-dialog test. Keep it serial with any other solver workload.

Provide the Fairbeam Python interpreter, an existing optional Elmer installation containing
both `ElmerSolver` and `ElmerGrid`, and a browser. No solver is installed by the check.
See [experimental solvers](RESEARCH-SOLVERS.md) for the supported prototype and its limits.

```powershell
$env:FAIRBEAM_PYTHON = 'C:/path/to/runtime/python.exe'
$env:FAIRBEAM_ELMER_ROOT = 'C:/path/to/Elmer'
$env:FAIRBEAM_EVIDENCE = 'C:/path/to/new/elmer-check-evidence'
node scripts/check-elmer-antenna-ui.mjs --run-solver
```

Without `--run-solver`, the command fails before starting servers or simulations and explains
the prerequisites. Report an unrun integration check as **not run**, not as passing. The
test checks completion and result routing, not numerical accuracy or mesh convergence.

## Scenario checks

Focused checks are `npm run check:eta-presentation` and `npm run check:run-profiles`
(pure logic), plus `npm run check:modal-focus` and `npm run check:run-profile-dialog` (temporary
Vite/run-server stacks, Chrome and the Python runtime with openEMS required; no solver runs).
The opt-in `scripts/check-home-accessibility.mjs` uses intercepted API fixtures against a running
Vite server; see its header for environment variables. It covers compact layouts, the header menu,
project search/sort/favorites, failure states, and desktop panel preference restoration.

`npm run check:scenarios` drives realistic first-time tasks through the real UI in headless Chrome,
each in English and in Turkish, and stops on the first thing a user would trip over. It is heavier
than `check:designer` (about 3 minutes, one coarse solver run) and is not part of it.

- **S1** builds a patch from the empty design using only the UI: an FR4 brick, a ground sheet and a
  patch, a slot cut with right-click › Boolean › Subtract ›, a mirrored solid through the Transform dialog's
  live preview, the face-extrude preview, a discrete port and the band, Color… on a solid and on a
  component, Save and a reload, then **one coarse run** (see below) and its S-parameters and Summary.
- **S2** imports a VBA macro (`examples/cst/patch-antenna.bas`) and PCB artwork
  (`python/tests/fixtures/pcb/*.gtl, *.gko`) and checks that the solids and the report appear.
- **S3** opens the Sweep dialog (the default range is ±10 %, the submission is captured, never
  sent), the Optimizer (its validation messages) and Simulation settings (Discard).
- **S4** copies two bundled example bundles as runs of a design (one with a synthetic field map) and
  checks the Summary with Δ vs A, Copy data and CSV, the run's Properties, the Field map with Phase
  and Animate, and Retry after a failed result load.
- **S5** opens the Python panel, edits a parameter value in the script and Applies it (one undo step),
  applies a script that raises (the line is named and the design stays) and leaves Edit with unapplied
  changes (the confirmation).
- **S6** creates a Python model from Start, opens its linked Design with the source in the Python
  panel, then returns to Start and confirms **Open as Design** reuses that Design without making a
  duplicate.
- **S7** checks repeated transform previews, a single Apply and undo, component-wide transforms,
  moving parts between component folders, independent grid visibility and local drawing frames.

Every step asserts the expected state and audits the page: no element overflows its container, no
clipped text without a tooltip, no raw i18n keys, no dialog outside the window, and no console error
or warning (which includes the `[i18n]` missing-key warning). A failing step saves a screenshot
under `$TMPDIR/fairbeam-scenario-failures/`. The audit checks itself first on a page with known faults.

The runner starts Vite and a run server on free ports over a temp folder (a copy of `public/` whose
`projects/` is also the server's `--projects`, because Vite serves result files from its public
folder while the server writes into `--projects`; a folder elsewhere fails to load, see
[RUN-SERVER.md](RUN-SERVER.md)) and stops both by PID at the end. The solver run takes the
`/tmp/fairbeam-sim.lock` directory (`mkdir` takes it, `rmdir` releases it) and runs at `nice -n 10`; when
the lock is held, or with `--skip-run`, the run is skipped. Options: `S1 S3` (only those scenarios),
`--lang en|tr`, `--skip-run`; `FAIRBEAM_CHROME` and `FAIRBEAM_PYTHON` point at another Chrome or
python. The checks that a browser cannot do are in [DESKTOP-CHECKLIST.md](DESKTOP-CHECKLIST.md).

### Minimum-window scenario check

`node scripts/check-scenarios.mjs S8 S9 S10 --compact --skip-run` runs EN/TR
Appearance, visible-surface exports and RF forms at the native desktop minimum.
The Rust window fit uses 1024 × 700 logical points, reduced to 1024 × 688 on a
728-point work area after title-bar allowance. RF forms run at both heights;
Cancel/Create actions must remain inside the modal and reachable onscreen by Tab.
At these widths the navigation/properties panels are overlays: scenarios open their
real edge strips when needed. The default scenario dimensions and step count stay unchanged.
No FDTD solve is requested by this command.

`node scripts/check-scenarios.mjs S9 --compact --skip-run --ribbon-file-audit`
also checks View/Simulation icon alignment at 1024, 1280 and 1440 pixels in EN/TR,
and opens an actual result bundle through More's file chooser. The extra check is opt-in.
