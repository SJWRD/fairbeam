// Evidence before a page exists. Never retry launch or change its options, timeout or cleanup.
import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

const LIMIT = 64;
const text = (value, limit = 4000) => {
  if (typeof value !== 'string') return '[not a string]';
  return value.slice(0, 100000)
    .replace(/^.*(?:-EncodedCommand\b|-enc\s|\bCommandLine\s*[:=]|\bLaunching\s).*$/gim, '[command omitted]')
    .replace(/\b[a-z][a-z\d+.-]*:\/\/[^\s<>"']+/gi, value => {
      try {
        const url = new URL(value);
        if (!['http:', 'https:', 'ws:', 'wss:', 'file:'].includes(url.protocol)) return '[URL omitted]';
        url.username = ''; url.password = ''; url.search = ''; url.hash = '';
        return url.href;
      } catch { return '[URL omitted]'; }
    })
    .replace(/\bBearer\s+\S+/gi, 'Bearer [redacted]')
    .replace(/((?:["']?)(?:[\w-]*(?:token|password|secret|authorization)|api[_-]?key)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;]+)/gi, '$1[redacted]')
    .slice(0, limit);
};
const errorText = error => {
  try { return text(typeof error === 'string' ? error : error?.stack ?? error?.message ?? '[unknown error]'); }
  catch { return '[unreadable error]'; } // Evidence must not replace even an unusual thrown object.
};
const pid = value => Number.isSafeInteger(value) && value > 0 ? value : null;
const knownArgs = new Set(['--no-sandbox', '--enable-unsafe-swiftshader', '--use-angle=swiftshader']);

async function installedVersion(name) {
  try {
    const data = JSON.parse(await readFile(new URL(`../../node_modules/${name}/package.json`, import.meta.url), 'utf8'));
    return typeof data.version === 'string' && /^[\d][\w.+-]{0,40}$/.test(data.version) ? data.version : null;
  } catch { return null; }
}

/** Returns the exact browser or rethrows the exact launch error. The logger accepts no payloads. */
export async function launchWithEvidence(puppeteer, options, { directory, stack, report = console.error } = {}) {
  const started = Date.now(), lifecycle = [];
  let ownedPid = null, dropped = 0, outputDirectory;
  const logger = channel => channel === 'puppeteer:browsers:launcher' ? message => {
    if (typeof message !== 'string') return;
    const match = /^(?:Launched (\d+)|Trying to kill (\d+)|Browser process (\d+) (onExit|exists))$/.exec(message);
    if (!match) return; // In particular, discard launch arguments, environment objects and protocol traffic.
    const processId = pid(Number(match[1] ?? match[2] ?? match[3]));
    if (!processId) return;
    if (match[1] && ownedPid === null) ownedPid = processId;
    if (processId !== ownedPid) return;
    if (lifecycle.length === LIMIT) { dropped++; return; }
    lifecycle.push({ atMs: Date.now() - started, pid: processId,
      event: match[1] ? 'spawn' : match[2] ? 'cleanup-requested' : match[4] === 'onExit' ? 'exit' : 'process-present' });
  } : undefined;
  const warn = error => { try { report(`Could not save browser launch evidence: ${errorText(error)}`); } catch { /* evidence is secondary */ } };
  const write = async (name, record) => {
    try {
      if (!outputDirectory) {
        await mkdir(directory, { recursive: true });
        outputDirectory = await mkdtemp(join(directory, 'browser-launch-'));
      }
      await writeFile(join(outputDirectory, name), JSON.stringify(record,
        (_key, value) => typeof value === 'string' ? text(value) : value, 2), { flag: 'wx' });
    } catch (error) { warn(error); }
  };
  const intent = {
    schema: 'fairbeam.browser-launch-evidence/1', phase: 'before-launch', startedAt: new Date().toISOString(),
    nodeVersion: process.version, platform: process.platform,
    installedPackages: { puppeteerCore: await installedVersion('puppeteer-core'), browsers: await installedVersion('@puppeteer/browsers') },
    puppeteerModuleOverride: Boolean(process.env.FAIRBEAM_PUPPETEER),
    executablePath: text(options.executablePath), headless: options.headless,
    approvedArguments: (options.args ?? []).filter(arg => knownArgs.has(arg)).slice(0, LIMIT),
    // Report whether defaults apply; never copy an arbitrary profile, argument or environment payload.
    explicitProfile: typeof options.userDataDir === 'string' || (options.args ?? []).some(arg => typeof arg === 'string' && arg.startsWith('--user-data-dir')),
    explicitTimeout: Object.hasOwn(options, 'timeout'), explicitTransport: Object.hasOwn(options, 'pipe'),
  };
  await write('prelaunch.json', intent);
  try {
    const browser = await puppeteer.launch({ ...options, logger });
    await write('launched.json', { schema: intent.schema, phase: 'launched', elapsedMs: Date.now() - started, ownedPid, lifecycle, dropped });
    return browser;
  } catch (error) {
    const failure = { schema: intent.schema, phase: 'launch-failed', elapsedMs: Date.now() - started,
      error: errorText(error), ownedPid, lifecycle, dropped, processes: [], captureErrors: [] };
    try {
      const children = stack?.diagnostics?.() ?? [];
      if (!Array.isArray(children)) throw new Error('Stack diagnostics did not return a process list');
      failure.processes = children.slice(0, 4).map(child => ({
        name: text(child.name, 80), pid: pid(child.pid), exited: child.exited === true,
        exitCode: Number.isInteger(child.exitCode) ? child.exitCode : null,
        signalCode: typeof child.signalCode === 'string' ? text(child.signalCode, 40) : null,
        output: text(child.output),
      }));
    } catch (captureError) { failure.captureErrors.push(errorText(captureError)); }
    await write('failure.json', failure); // Separate from intent: a failed write cannot erase prelaunch evidence.
    throw error;
  }
}
