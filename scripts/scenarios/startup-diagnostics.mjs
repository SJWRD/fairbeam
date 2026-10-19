// Startup evidence only: never retry navigation, alter timeouts or recover application state.
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

const LIMIT = 100;
const clipped = (value, limit = 2000) => String(value ?? '').slice(0, limit);
const urlWithoutSecrets = value => {
  try {
    const u = new URL(value);
    if (!['http:', 'https:', 'file:', 'about:'].includes(u.protocol)) return `${u.protocol}[omitted]`;
    u.username = ''; u.password = ''; u.search = ''; u.hash = '';
    return clipped(u.href, 500);
  } catch { return '[invalid URL]'; }
};
const safeText = (value, limit = 10000) => clipped(value, limit)
  .replace(/https?:\/\/[^\s<>"']+/g, urlWithoutSecrets)
  .replace(/\bBearer\s+\S+/gi, 'Bearer [redacted]')
  .replace(/((?:token|password|secret|authorization|api[_-]?key)\s*[:=]\s*)[^\s,;]+/gi, '$1[redacted]');

/** Attach before the first navigation; keep bounded request metadata, never bodies or headers. */
export function startupDiagnostics(page, { directory, stack } = {}) {
  const started = Date.now(), consoleMessages = [], pageErrors = [], failures = [], responses = [];
  const pending = new Map();
  const dropped = { pending: 0, console: 0, pageErrors: 0, failures: 0, responses: 0 };
  const add = (list, key, entry) => {
    if (list.length === LIMIT) { list.shift(); dropped[key]++; }
    list.push(entry);
  };
  const elapsed = () => Date.now() - started;
  const info = request => ({ url: urlWithoutSecrets(request.url()), method: request.method(), type: request.resourceType(), startedMs: elapsed() });
  const listeners = {
    console: message => add(consoleMessages, 'console', { atMs: elapsed(), type: message.type(), text: clipped(safeText(message.text())) }),
    pageerror: error => add(pageErrors, 'pageErrors', { atMs: elapsed(), text: clipped(safeText(error.stack ?? error.message)) }),
    request: request => {
      if (pending.size === LIMIT) { pending.delete(pending.keys().next().value); dropped.pending++; }
      pending.set(request, info(request));
    },
    requestfinished: request => pending.delete(request),
    requestfailed: request => {
      const entry = pending.get(request) ?? info(request);
      add(failures, 'failures', { ...entry, durationMs: elapsed() - entry.startedMs, error: clipped(safeText(request.failure()?.errorText)) });
      pending.delete(request);
    },
    response: response => {
      if (response.status() >= 200 && response.status() < 300) return;
      const entry = pending.get(response.request()) ?? info(response.request());
      add(responses, 'responses', { ...entry, durationMs: elapsed() - entry.startedMs, status: response.status() });
    },
  };
  for (const [event, listener] of Object.entries(listeners)) page.on(event, listener);
  const dispose = () => { for (const [event, listener] of Object.entries(listeners)) page.off(event, listener); };
  const capture = async error => {
    const evidence = {
      error: clipped(safeText(error.stack ?? error.message ?? error)), elapsedMs: elapsed(),
      url: urlWithoutSecrets(page.url()), consoleMessages, pageErrors, failures, non2xx: responses,
      outstanding: [...pending.values()].map(entry => ({ ...entry, durationMs: elapsed() - entry.startedMs })),
      dropped, captureErrors: [],
    };
    const attempt = async (name, fn) => {
      let timer;
      try { return await Promise.race([Promise.resolve().then(fn), new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error('diagnostic capture exceeded 5 seconds')), 5000);
      })]); }
      catch (e) { evidence.captureErrors.push({ operation: name, error: clipped(safeText(e.message)) }); }
      finally { clearTimeout(timer); }
    };
    await mkdir(directory, { recursive: true });
    // DOM only, no app-store imports, localStorage, headers, form values or script source.
    const dom = await attempt('DOM', () => page.evaluate(() => {
      const copy = document.body?.cloneNode(true);
      copy?.querySelectorAll('script,style,noscript,textarea,input,[contenteditable],.cm-editor,svg,canvas').forEach(el => el.remove());
      copy?.querySelectorAll('*').forEach(el => {
        for (const attr of [...el.attributes]) if (!['class', 'id', 'role', 'aria-modal', 'aria-expanded', 'aria-hidden'].includes(attr.name)) el.removeAttribute(attr.name);
      });
      return {
        readyState: document.readyState, title: document.title.slice(0, 300),
        viewport: { width: innerWidth, height: innerHeight },
        regions: Object.fromEntries(['.app', '.home', '.workspace', '.loading', '.panel-error', '.scrim'].map(selector => [selector, [...document.querySelectorAll(selector)].map(el => {
          const r = el.getBoundingClientRect();
          return { visible: r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden', text: el.textContent.slice(0, 500) };
        }).slice(0, 10)])),
        text: (copy?.textContent ?? '').slice(0, 10000), html: (copy?.outerHTML ?? '').slice(0, 60000),
      };
    }));
    if (dom) {
      const { text, html, ...state } = dom;
      evidence.dom = state;
      await attempt('body text', () => writeFile(join(directory, 'body.txt'), safeText(text)));
      await attempt('HTML', () => writeFile(join(directory, 'dom.html'), safeText(html, 60000)));
    }
    await attempt('screenshot', () => page.screenshot({ path: join(directory, 'page.png'), timeout: 5000 }));
    evidence.processes = await attempt('stack logs', () => (stack?.diagnostics?.() ?? []).map(child => ({
      ...child, output: safeText(child.output),
    })));
    await writeFile(join(directory, 'startup.json'), JSON.stringify(evidence, null, 2));
    return directory;
  };
  return { capture, dispose };
}

/** Preserve the original setup exception even if writing diagnostic artifacts also fails. */
export async function withStartupDiagnostics(page, options, run) {
  const diagnostics = startupDiagnostics(page, options);
  try { return await run(); }
  catch (error) {
    try { console.error(`Startup diagnostics: ${await diagnostics.capture(error)}`); }
    catch (captureError) { console.error(`Could not save startup diagnostics: ${clipped(safeText(captureError.message))}`); }
    throw error;
  } finally { diagnostics.dispose(); }
}
