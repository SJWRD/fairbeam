// Conservative CI planning. Unknown files or missing evidence run the full checks.
import {execFileSync} from 'node:child_process';
import {appendFileSync, readFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';

export function classify(files) {
  const docsOnly = files.length > 0 && files.every(file =>
    /^(docs\/|landing\/|landing-src\/|design-system\/|examples\/drawings\/|examples\/reports\/)/.test(file) ||
    /^(README(?:\.[a-z]+)?\.md|CONTRIBUTING\.md|AI_POLICY\.md|LICENSE|CITATION\.cff)$/.test(file));
  const full = !docsOnly;
  // UI/TS edits can affect the Python geometry contract; keep native qualification for code.
  return {full, native: full};
}

export async function verifiedMergeReuse(head, git, hasSuccessfulPrRun) {
  const parents = git(['rev-list', '--parents', '-n', '1', head]).trim().split(/\s+/);
  if (parents.length !== 3) return false;
  const tested = parents[2];
  if (git(['rev-parse', `${head}^{tree}`]).trim() !== git(['rev-parse', `${tested}^{tree}`]).trim()) return false;
  return await hasSuccessfulPrRun(tested);
}

async function run() {
  const git = args => execFileSync('git', args, {encoding:'utf8', maxBuffer:16*1024*1024});
  const event = JSON.parse(readFileSync(process.env.GITHUB_EVENT_PATH, 'utf8'));
  const kind = process.env.GITHUB_EVENT_NAME;
  let result = {full:true, native:true, reuse:false, reason:'Manual or unknown event: full qualification'};
  try {
    if (kind === 'pull_request') {
      const base = git(['merge-base', event.pull_request.base.sha, event.pull_request.head.sha]).trim();
      const files = git(['diff', '--name-only', '-z', base, event.pull_request.head.sha, '--']).split('\0').filter(Boolean);
      result = {...classify(files), reuse:false, reason:`Pull request: ${files.length} changed paths`};
    } else if (kind === 'push') {
      const reuse = await verifiedMergeReuse(process.env.GITHUB_SHA, git, async sha => {
        if (!process.env.GITHUB_TOKEN) return false;
        const url = `https://api.github.com/repos/${process.env.GITHUB_REPOSITORY}/actions/workflows/ci.yml/runs?event=pull_request&status=success&head_sha=${sha}&per_page=100`;
        const response = await fetch(url, {headers:{Authorization:`Bearer ${process.env.GITHUB_TOKEN}`, Accept:'application/vnd.github+json'}, signal:AbortSignal.timeout(10000)});
        if (!response.ok) return false;
        const data = await response.json();
        return data.workflow_runs?.some(run => run.head_sha === sha && run.event === 'pull_request' && run.conclusion === 'success') === true;
      });
      if (reuse) result = {full:false, native:false, reuse:true, reason:'Merge tree exactly matches a successful PR CI head'};
      else result.reason = 'No identical successfully tested PR tree: full qualification';
    }
  } catch {
    result = {full:true, native:true, reuse:false, reason:'Planning evidence unavailable: full qualification'};
  }
  for (const key of ['full','native','reuse']) appendFileSync(process.env.GITHUB_OUTPUT, `${key}=${result[key]}\n`);
  console.log(JSON.stringify(result));
  if (process.env.GITHUB_STEP_SUMMARY) appendFileSync(process.env.GITHUB_STEP_SUMMARY, `### CI plan\n${result.reason}\n\nFull browser/web: ${result.full}; native Python/smoke: ${result.native}; reused checks: ${result.reuse}.\n`);
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await run();
