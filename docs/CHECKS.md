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
