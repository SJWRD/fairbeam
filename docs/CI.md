# CI and website publishing

PRs remain the qualification point. Application changes run the web checks and full EN/TR
Windows browser suite, Python suite and one real coarse openEMS smoke. Native, dependency,
script and unknown-path changes use the same full qualification. Documentation/site-only PRs run
web/documentation/site checks without starting a Windows runner. Manual CI runs always use
the full suite.

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
npm ci
npm run build:site
vercel link --project fairbeam --scope ismailakdags-projects
vercel deploy --prod --skip-domain --scope ismailakdags-projects
```

Inspect the returned deployment URL and its pages, then promote that **production** deployment:

```powershell
vercel inspect <deployment-url> --scope ismailakdags-projects
vercel promote <deployment-url> --scope ismailakdags-projects
```

Use the existing local Vercel login; never put a token in a command or commit credentials.
One staged production deployment plus promotion avoids a second build. Do not promote a
preview build as though it were the tested production artifact. This publishes the site/demo,
not a desktop installer or updater release. Re-enable Git deployment explicitly if the team
later chooses automatic publishing again.

Before pushing, run `node scripts/check-ci-scope.mjs`. Workflow changes still require a full
remote qualification once; a green path-classification test alone is not enough.
