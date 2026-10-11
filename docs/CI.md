# CI and website publishing

PRs remain the qualification point. Application changes run the web checks and full EN/TR
Windows browser suite, Python suite and one real coarse openEMS smoke. Native, dependency,
script and unknown-path changes use the same full qualification. Documentation/site-only PRs run
web/documentation/site checks without starting a Windows runner. Manual CI runs always use
the full suite. The web checks finish before Windows starts, so a manifest or static-check
failure does not consume a native runner at the same time.

After a normal merge, the planner compares the final merge tree with the PR head tree and
checks GitHub for a successful `ci.yml` PR run at that exact head. Only when both match are
the duplicate jobs skipped. A direct push, changed merge tree, missing token, API failure,
or missing successful run falls back to full qualification. A summary records the decision;
the final `gate` job fails when any required job fails or is cancelled.

The openEMS download archive is cached by OS/architecture, Python ABI and upstream tag;
installation still runs each time. npm uses its download cache, never shared node_modules.
Failure artifacts and smoke bundles are retained for seven days.

## Publish the website once per reviewed batch

Git-triggered Vercel deployments are disabled in `vercel.json`. The existing live site stays
online; merging application/backend work no longer creates a deployment. Vercel's ignored
build command is deliberately not used: skipped builds still count toward deployment quotas.

From a clean, reviewed `main` checkout after a successful CI run (or a verified reused gate):

```powershell
git pull --ff-only origin main
vercel link --project fairbeam --scope ismailakdags-projects
vercel pull --yes --environment=production --scope ismailakdags-projects
vercel build --prod --scope ismailakdags-projects
# Windows CLI 59.16 packaging workaround (see below):
Copy-Item api/_ping-schema.json .vercel/output/functions/api/ping.func/api/_ping-schema.json
vercel deploy --prebuilt --prod --skip-domain --scope ismailakdags-projects
```

Use Node 24 LTS with its matching npm. `vercel build` runs the configured `npm ci`
and site checks locally, then packages the static site **and** `api/ping` into
`.vercel/output`. `--prebuilt` uploads that output without a remote build. A deployment
still counts as a deployment; serving pages and API requests still uses the hosting plan.
Application-only changes do not need a site deployment. Batch site changes before publishing.

On Windows, Vercel CLI 59.16 can lose the command search path when its child environment
contains both `Path` and `PATH` (`spawn cmd.exe ENOENT`). Normalize the key in the current
PowerShell session before `vercel build`; this does not change the machine's saved environment:

```powershell
$buildPath = $env:Path
Remove-Item Env:Path
$env:PATH = $buildPath
```

Check `node --version` and `npm version` first. Both must use the intended Node installation;
an older npm launcher can select a different Node executable even when `node` is current.
Do not share `node_modules` between checkouts. Keep `.vercel/` and downloaded environment
files ignored. Production secrets that `vercel pull` reports as `[SENSITIVE]` remain managed
on Vercel; never replace production secret values with those placeholders. The static site
does not require the install-count Redis credentials at build time.

Windows CLI 59.16 also declared the ping schema in `filePathMap` but omitted the file from
the local function output in our check. Copy the repository's exact `api/_ping-schema.json`
into the generated function as shown above; the `api/` directory must already exist after
a successful build. Verify its SHA-256 matches the source and that importing the packaged
`api/ping.js` succeeds before uploading. Do not copy production environment files into the
function. The packaged GET handler must return 405; with no Redis credentials, POST must
return 503 without sending or recording a count. Remote runtime credentials are supplied
by the Vercel project, not embedded in this local package.

Inspect the returned deployment URL and its pages, then promote that **production** deployment:

```powershell
vercel inspect <deployment-url> --scope ismailakdags-projects
vercel promote <deployment-url> --scope ismailakdags-projects
```

Use the existing local Vercel login; never put a token in a command or commit credentials.
One local build, one staged production upload, and promotion avoid remote build work and a second
deployment. Do not promote a
preview build as though it were the tested production artifact. This publishes the site/demo,
not a desktop installer or updater release. Re-enable Git deployment explicitly if the team
later chooses automatic publishing again.

Before pushing, run `node scripts/check-ci-scope.mjs`. Workflow changes still require a full
remote qualification once; a green path-classification test alone is not enough.
