# Releases and updates

Source stays in the public repository [ismailakdag/fairbeam](https://github.com/ismailakdag/fairbeam).
Release files go to a second public repository,
[ismailakdag/fairbeam-releases](https://github.com/ismailakdag/fairbeam-releases): the desktop
installers, the updater feed and the runtime components the app downloads, such as the macOS openEMS
pack. It holds release files and the issue tracker for app users, and no source.

## Updater

- The desktop app uses `tauri-plugin-updater`. After the viewer has loaded, it reads
  `https://github.com/ismailakdag/fairbeam-releases/releases/latest/download/latest.json` and, when
  a newer version exists, offers it in a native dialog (`src-tauri/src/updater.rs`). Installing stops
  the server and restarts the app. Debug builds and `FAIRBEAM_NO_UPDATE_CHECK=1` skip the check.
- The dialog appears only when there is an update; every check leaves a line in
  `<app data>/logs/shell.log` (skipped, up to date, offered and the answer, install failed, check
  failed with the error). See "Shell" in [DESKTOP.md](DESKTOP.md).
- Every updater artifact referenced by `latest.json` is signed with the Fairbeam updater key. The
  public key is in `src-tauri/tauri.conf.json` (`plugins.updater.pubkey`).
- The **private key** is `~/.tauri/fairbeam-updater.key` on the machine that builds the release.
  It was made once with `npx tauri signer generate -w ~/.tauri/fairbeam-updater.key`. It has no
  password, is outside the repository, and must never be committed, printed, logged or pasted
  anywhere. It goes only into the environment of the build shell. **Back it up**, for example in a
  password manager. Without it, the installed apps can no longer be updated.
- Windows installs updates in passive mode, with a small progress window and no questions.
- The dialog shows `latest.json`'s `notes`: a short plain-text summary (`publish-release.mjs
  --summary "..."`, else the notes' first paragraph without Markdown) and a link to the release page,
  which has the full notes. A native message box cannot scroll, so the app also strips Markdown and
  caps the text at 480 characters (`updater.rs` `dialog_notes`).

### Updates on macOS

- **Not in Applications:** macOS runs an app opened from Downloads or a disk image without moving
  it from a temporary, read-only copy (App Translocation). Such an app cannot replace itself.
  Before anything is downloaded, the app says so: "Move Fairbeam to Applications". The dialog
  names the user's own copy and offers Show in Finder.
- **Read-only volume:** a disk image or a read-only disk gets the same advice.
- **Another writable volume** (an external disk): `tauri-plugin-updater` moves the running app
  into the system temp folder, and that move fails across file systems ("Cross-device link (os
  error 18)"). The shell therefore installs the verified archive beside the app instead
  (`src-tauri/src/macos_update.rs`). It unpacks on the same volume and swaps the bundles
  atomically (`renamex_np` with `RENAME_SWAP`). It never deletes the only copy of the app.
- **After a failed install:** the startup check does not offer the same update again in that
  session.

## Publishing a version

Build only from a tag on `main` of the source repository (`git tag v<version>`), after the checks in
[CONTRIBUTING.md](../CONTRIBUTING.md) pass.

When example conversion changes, also run `python scripts/check-gallery-designs.py --out gallery-check.json`
with an openEMS/CSXCAD Python environment. This opt-in check copies every actual gallery selection
into a temporary workspace and verifies preview, editable Design creation and native geometry
admission. It runs no field propagation and does not establish numerical convergence. Keep the
receipt outside the checkout or remove the generated receipt before building the tagged release.

1. Bump the version in `package.json`, `src-tauri/tauri.conf.json`, `src-tauri/Cargo.toml` and
   `python/fairbeam/_meta.py` and `python/pyproject.toml`. The runtime refreshes its copy of the Python package when the bundled
   version changes.
2. Regenerate the third-party inventory with `npm run licenses`, review any changed licenses
   and exceptions in `scripts/licenses/exceptions.json`, and run `npm run check:licenses`.
   Commit `THIRD-PARTY-NOTICES.md` and `scripts/licenses/inventory.json` before tagging. The
   app bundles the notices, and `scripts/publish-release.mjs` attaches the same file as a release
   asset and includes it in `SHA256SUMS.txt`. Retain exact corresponding source archives and
   solver build inputs with the release; upstream links alone do not replace that retention.
3. Build each platform on its own machine with `npm run desktop:release` and the key in the
   environment. The release build adds `src-tauri/tauri.release.conf.json`
   (`createUpdaterArtifacts`), so it writes the updater artifact and its `.sig` next to the
   installer. `npm run desktop:build` stays a plain build that needs no key:

   ```bash
   # macOS (arm64)
   export TAURI_SIGNING_PRIVATE_KEY="$(cat ~/.tauri/fairbeam-updater.key)" TAURI_SIGNING_PRIVATE_KEY_PASSWORD=""
   npm run desktop:release
   #   -> bundle/macos/Fairbeam.app.tar.gz (+ .sig)   updater artifact
   #   -> bundle/dmg/Fairbeam_<v>_aarch64.dmg         manual download
   ```

   ```powershell
   # Windows (x64): the same key file, copied securely to %USERPROFILE%\.tauri\fairbeam-updater.key
   $env:TAURI_SIGNING_PRIVATE_KEY = Get-Content -Raw "$env:USERPROFILE\.tauri\fairbeam-updater.key"
   $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = ""
   npm run desktop:release
   #   -> bundle/nsis/Fairbeam_<v>_x64-setup.exe (+ .sig)   installer and updater artifact
   ```

   On macOS the release is also signed with a Developer ID and notarized. The build reads the
   identity and an App Store Connect API key from the environment, never from the repository:

   ```bash
   export APPLE_SIGNING_IDENTITY="Developer ID Application: <name> (<team id>)"   # in the keychain
   export APPLE_API_KEY=<key id> APPLE_API_ISSUER=<issuer id>
   export APPLE_API_KEY_PATH=~/.appstoreconnect/private_keys/AuthKey_<key id>.p8  # chmod 600
   npm run desktop:release   # signs, notarizes and staples the .app
   xcrun notarytool submit bundle/dmg/Fairbeam_<v>_aarch64.dmg --key "$APPLE_API_KEY_PATH" \
     --key-id "$APPLE_API_KEY" --issuer "$APPLE_API_ISSUER" --wait
   xcrun stapler staple bundle/dmg/Fairbeam_<v>_aarch64.dmg
   spctl --assess --type execute -v bundle/macos/Fairbeam.app   # "source=Notarized Developer ID"
   ```

   Without the key, `desktop:release` stops once it has built the bundle. Everything is built,
   but no updater artifact is written. You can sign a file afterwards with
   `npx tauri signer sign -f ~/.tauri/fairbeam-updater.key -p "" <file>`.
4. Publish from each machine. The script creates or updates the `v<version>` release in
   `ismailakdag/fairbeam-releases`, uploads the files, and merges `latest.json` and
   `SHA256SUMS.txt`. Entries of platforms already published are kept. Publish with
   `--latest=false` first:

   ```bash
   node scripts/publish-release.mjs --version <version> --notes-file notes.md --latest=false \
     --asset darwin-aarch64=src-tauri/target/release/bundle/macos/Fairbeam.app.tar.gz \
     --file "src-tauri/target/release/bundle/dmg/Fairbeam_<version>_aarch64.dmg=fairbeam_<version>_macos-arm64.dmg"
   node scripts/publish-release.mjs --version <version> --latest=false \
     --asset windows-x86_64=src-tauri/target/release/bundle/nsis/Fairbeam_<version>_x64-setup.exe
   ```

   `--dry-run` shows what would be uploaded. The published files are named `fairbeam_<v>_...`.
   The updater looks at the release that is marked as the latest one. With `--latest=false` the
   release is created without that mark, so no installed app is offered it yet. Check the real
   assets first: download the DMG and the installer, open them, and check the update feed
   (`latest.json`, its signatures and `SHA256SUMS.txt`). Then mark the release:

   ```bash
   gh release edit v<version> -R ismailakdag/fairbeam-releases --latest
   ```

   The mark is a property of the release, so a later publish to the same version leaves it as it
   is. Never mark an older release as the latest again: the updater does not downgrade.
5. Once both platforms are verified and the release is promoted, update the website download
   links (`landing/index.html`, section `#download`: file names, sizes and minimum OS versions)
   through the repository workflow. Git-triggered website deployments are disabled. Publish one
   locally built, reviewed batch using the prebuilt workflow in [CI.md](CI.md).

## Issues and feedback

The public repository's issue tracker takes the reports of app users. The app's **Send feedback**
links open its issue forms; "Report a problem" fills in the version and the OS through query
parameters named by the form's field ids (`version`, `os`). The forms are kept here in
`release-repo/.github/ISSUE_TEMPLATE/` (`bug.yml`, `feature.yml`, and `config.yml` without blank
issues); copy that folder to the public repository's `.github/ISSUE_TEMPLATE/`. Keep the ids when
editing them, or the prefill stops working.

## Runtime components

- **Windows openEMS:** the pinned MSVC package includes the native CPU and time-domain HDF5
  batching patches. Its URL, SHA-256 and build provenance are recorded in `runtime/pins.json`;
  retain corresponding sources and notices with the runtime release.
- **macOS openEMS pack:** built with `scripts/build-openems-macos-pack.py` (docs/DESKTOP.md), then
  released under its own tag. The pack contains its license texts and `NOTICE.md`, and the NOTICE is
  also a separate asset. Create it with `--latest=false` so that it never becomes the latest
  release, which is where the updater feed lives:

  ```bash
  gh release create openems-macos-arm64-<version> <pack>.tar.gz NOTICE.md \
    -R ismailakdag/fairbeam-releases --latest=false --title "openEMS pack for macOS arm64 (...)" --notes-file notes.md
  ```

  Then pin its URL, SHA-256, size and `macos_min` in `runtime/pins.json`.

  The pack ships the native CPU patches ([CPU-OPTIMIZATION.md](CPU-OPTIMIZATION.md#macos-shipped-and-on-by-default)):
  `scripts/install-openems-macos.sh` applies them to openEMS `12cd91de2` (`NATIVE_CPU=0` skips them),
  the pack script records them in `openems-pack.json` and `NOTICE.md` and names the pack
  `...-ncpu1`. Check before publishing that the build printed `native CPU patches: yes`.

## Licensing of what is published

- openEMS is GPL-3.0-or-later; CSXCAD and fparser are LGPL-3.0-or-later. The macOS pack ships
  their license texts and a NOTICE that points to the exact source commits, together with a written
  offer of the complete corresponding source.
- Fairbeam declares GPL-3.0-or-later (`python/pyproject.toml`, `src-tauri/Cargo.toml`) and loads
  openEMS in the same process. Distributing the app therefore means offering Fairbeam's
  corresponding source to its recipients. The source of every version is the tag `v<version>` of the
  public source repository, and [NOTICE.md](../NOTICE.md) carries the written offer, which goes
  through the issues of the release repository. Tag the source before you publish its release.

## Changelog

One entry per release, newest first. The installers are on the
[release page](https://github.com/ismailakdag/fairbeam-releases/releases) of each version.

### 0.8.1, 2026-10-18

- Fix new-design creation staying on Start after the initial native `.fb` save changes
  the workspace identity. Creation now enters Design after its own successful save;
  an unrelated document opened while saving still takes precedence.
- The Windows first-use Open With limitation documented for 0.8.0 remains unchanged.

### 0.8.0, 2026-10-18

- Portable `.fb` documents with native Open, Save and Save As, while keeping support for
  existing JSON designs. Saving a JSON design as `.fb` preserves the source JSON.
- Completed runs are saved automatically beside the document in separate `runs/*-v2`
  result directories, with checksummed packages. Unsaved design changes are not silently saved.
- macOS document saves preserve Finder extended attributes and ACLs; Unicode-normalized
  searches match decomposed file names and paths.
- Updated Windows CPU runtime and Windows-only HDF5 time-domain batching. macOS keeps its
  existing CPU runtime and GPU behavior. Elmer and coax remain experimental; this release
  does not establish convergence or accuracy for arbitrary designs.
- **Known Windows limitation:** on some installations Fairbeam may not appear in the initial
  Open With picker. Choose **Open With > Choose another app > Choose an app on your PC** and
  select `%LOCALAPPDATA%\Fairbeam\fairbeam.exe`, then choose **Always**. Cold double-click,
  Open With and delivery to an already running app were verified after this explicit choice;
  automatic first-install discovery remains unresolved. Opening `.fb` from within Fairbeam
  is also available.

### 0.7.8, 2026-10-17

- **Simulation reliability:** serialize claims on simulation directories so competing CLI processes cannot overwrite a live run. Process captured stdout/stderr before finalizing results, preserving delayed final bundle messages and reporting incomplete capture.
- **Runtime maintenance:** after a successful verified install or refresh, remove obsolete download archives and orphan partial files. Preserve the pinned engine and uv archives for repair; keep CPU/GPU caches separate and skip linked paths. Cleanup failures do not fail an otherwise successful installation.
- **Research and documentation:** add the opt-in thin-PEC coupled-stripline test fixture and its bounded lossless/constant-conductivity comparison record. Fix rejected-cohort status reporting and the Turkish validation link. The fixture is not a gallery model or a new solver capability; finite copper and general antenna convergence remain outside its scope.

### 0.7.7, 2026-10-17

- **Geometry and mesh:** fix extrusion axes after composed rotations; include dispersive geometry and whole-band pole bounds in automatic dielectric meshing. Materials without a safe automatic bound require an explicit validated mesh.
- **Numerical results:** use the full incident-wave basis for embedded element patterns, canonical matrix reflection for S11 consumers, and a singular-value passivity check. Incomplete data no longer produce a definite passivity result.
- **Optimization:** native candidates require established energy convergence for every driven port. Missing or nonfinite goal metrics cannot become a successful or best candidate.
- **Editor and display:** keep delayed source/history responses attached to their document, convert field-plane display/export coordinates to mm, and stop presenting unavailable realized gain as a valid quantity.
- **Existing results:** rerun models affected by composed extrusion or dispersive automatic meshing; re-evaluate old optimizer winners and regenerate matched-basis element patterns where needed. See [the review follow-up](REVIEW-2026-10-17.md) for reproductions and limits. These checks do not establish general antenna accuracy or mesh convergence.

### 0.7.6, 2026-10-15

- **Editable examples:** convert tilted cylinders and tubes, native coaxial ports and ideal curve wires into Design without replacing their geometry. The helix example can now open as an editable design; ideal wires retain zero radius. Conversion still reports parameters whose expressions cannot be preserved.
- **Geometry export:** ideal zero-radius wires have no solid surface, so STL, GLB and Blender solid exports now explain that limitation instead of exporting their display-only thickness as physical geometry.
- **Design appearance:** save material/part opacity and the opening camera view. Coaxial display metadata survives native previews and Python round trips. Overlapping port labels are stacked, including dense arrays.
- **Larger designs:** preview and save design documents up to 32 MiB; Python source and unrelated request limits remain unchanged. Manual coaxial mesh checks require axial endpoints, without incorrectly requiring a transverse centerline mesh line.
- **Coaxial workflow:** Python model conversion, native time-axis evaluation and compatible waveguide-port macro export include the fixes merged since 0.7.5. CPU-only support and curved-conductor mesh qualification warnings remain.
- **Runtime and setup:** macOS upgrades remove the obsolete runtime libs distribution and use the renamed r2 pack. Missing-Elmer guidance points to its setup controls; openEMS remains the default and Elmer is not installed automatically.
- **Known limits:** Elmer patch S11/mesh qualification, general periodic/Floquet support and curved-PEC coaxial accuracy remain research work. This release does not establish new solver accuracy or convergence claims.

### 0.7.5, 2026-10-11

- **Optional experimental Elmer workflows:** bounded cavity and single-port patch runs from Design Run. Driven results use the ordinary Results tree, S11, impedance, Smith and directivity views. Elmer must be installed separately; openEMS remains the default and its simulation path is unchanged.
- **Known limitation:** Elmer patch S11 does not yet agree with the openEMS comparison, mesh convergence has not been established, and FEM runs can be slow. This is a research/development option, not a validated general antenna solver. Directivity is not gain or efficiency. See [RESEARCH-SOLVERS.md](RESEARCH-SOLVERS.md).
- **Design materials:** select built-in and user-library materials directly while creating a shape; the selected material is added to the design.
- **FEM setup and results:** explicit finite-feed/PEC approximation preserves the original design, stale-server guidance, adjustable substrate layers, consistent complex-phase convention and persistent results.

### 0.7.4, 2026-10-11

- Include the standalone Elmer adapter in desktop resources and managed runtime updates, fixing capability checks in installed apps. A staged-package regression verifies imports without the source checkout.
- Explain the fixed Elmer cavity workflow and the separate experimental periodic CPU build requirement in English and Turkish, with setup instructions in the panel.
- Align the folded Mesh menu as a readable vertical action list.

### 0.7.3, 2026-10-11

- **Experimental coaxial TEM ports (CPU):** editable native openEMS ports, material references, generated geometry, saved projects and Python export. Mesh/probe/PML guards reject invalid setups. The editor and Checks warn that curved-conductor meshing can shift phase and impedance and require refinement checks. Same-model parity was verified on a synthetic air line; general accuracy and GPU support are not claimed. See [COAXIAL-PORTS.md](COAXIAL-PORTS.md).
- **Simulation quality:** feed/mesh and convergence checks improved; the unconverged Blade gallery example was removed. Existing results still require independent verification.
- **Results and projects:** fixes for stale result selection, physical port mapping, reference sweeps, Smith/Touchstone references and local viewer file identity. Compare and array controls fit narrow result panes more reliably.
- **Documentation and delivery:** privacy documentation aligned with optional install counts; validation limits retained; redundant hosted CI and automatic website deployments reduced.

### 0.7.2, 2026-10-07

- **Install counts (optional):** on first start Fairbeam asks whether it may count installs. If you allow it, it sends at most once a week a random install ID (created on your computer, not derived from it), the app version, the operating system and the CPU architecture; nothing about your designs, files or results. You can turn it off or reset the ID in Settings at any time. The server keeps only weekly totals. See the [privacy page](https://fairbeam.org/privacy.html) and [TELEMETRY.md](TELEMETRY.md).

### 0.7.1, 2026-10-07

- **VBA macro import:** degree trig functions (sind, cosd, tand, atnd and inverses), ThickenSheetAdvanced and Loft between two planar profiles; unsupported items are reported by name instead of being dropped. A tetrahedral or surface mesh setting no longer sets the FDTD density.
- **Mesh:** new setting Refine fine features (Simulation settings › Mesh, `mesh.refine_features`) refines the automatic mesh locally across narrow gaps, notches, feed gaps and slanted thin strips. It is off by default for every design, because it adds cells and can shift the input impedance of wire antennas; the Checks list suggests it when fine features are under-resolved. The UAV blade example turns it on. See [MESHING.md](MESHING.md).
- **Results:** a Figure menu exports the current plot as PNG or SVG; figure, CSV and Touchstone files share one naming scheme. Matched bands show their Center (band middle) and Best match (|S11| minimum) separately, also in the Summary, the reports and the CSV exports (`f_center_GHz` now means the band middle, `f_best_GHz` the minimum; the sweep CSV has `band_center_ghz` and `band_best_ghz`).
- **Simulation:** mesh mode names agree between Settings and the Run dialog, the threads setting reaches the Run dialog, and the live run panel no longer reports the excitation as still running on short runs.
- **Interface and docs:** Turkish texts reviewed, Turkish documentation, third-party license notices in the app (About).


### 0.7.0 — first release of Fairbeam, 2026-10-06

Fairbeam is a desktop app for macOS (Apple silicon) and Windows x64 that designs, simulates and
documents antennas on the open-source openEMS FDTD solver. It installs its own runtime on first
start and needs no admin rights.

- **Designer.** A ribbon-based 3D designer: parametric solids and sheets, Boolean operations,
  ports and lumped elements, materials and an automatic mesh, with checks that explain what to fix.
  English and Turkish interface.
- **Simulation.** Runs on the CPU or, when the GPU runtime is installed, on the GPU
  engine. Parameter sweeps, an optimizer, mesh convergence studies, multi-port structures and
  arrays, and dispersive materials.
- **Results.** S-parameters, impedance, matched bands, far field (directivity, gain, realized gain,
  efficiency), E and H field planes, summary tables and run comparison overlays.
- **Python models.** Models written as small Python files, run from the app or with the `fairbeam`
  command. The run server and the Run panel start from the same files.
- **Files and export.** Self-describing project bundles, Touchstone, CSV, STL, glTF, Blender
  scenes (rendered images require Blender), preview Gerber and Excellon fabrication output
  requiring independent review, and PCB artwork import for the documented file subsets.
- **Coming from the previous app.** Fairbeam is a separate app. On its first start it can import your
  settings, workspace and Python models and then help remove the old app; files the old app wrote
  still open ([MIGRATING-FROM-ANTENLAB.md](MIGRATING-FROM-ANTENLAB.md)).
- **Updates.** By default, the app checks GitHub for updates on startup and offers installation with
  progress for download, installation and restart. Startup checks can be disabled.
- Optional sign-in ([ACCOUNTS.md](ACCOUNTS.md)) and pseudonymous usage counts
  ([TELEMETRY.md](TELEMETRY.md)) are built but switched off in this release.
