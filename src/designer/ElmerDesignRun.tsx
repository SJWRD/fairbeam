import { createSignal, For, onCleanup, Show } from "solid-js";
import { unwrap } from "solid-js/store";
import { api } from "../runner/api";
import { openResearch, researchPath } from "../runner/researchState";
import { refreshRuns } from "../runner/store";
import { conflict, dirty, draft, file, save } from "./store";
import { t } from "../i18n";
import NumberField from "../components/NumberField";

/** Separate form: Elmer never modifies openEMS engine settings or applies its preflight rules. */
export default function ElmerDesignRun(props: { close: () => void }) {
  const [domain, setDomain] = createSignal(["", "", "", "", "", ""]);
  const [cell, setCell] = createSignal("25");
  const [target, setTarget] = createSignal("1.8");
  const [path, setPath] = createSignal(researchPath("elmer"));
  const [ack, setAck] = createSignal(false);
  const [busy, setBusy] = createSignal(false);
  const [error, setError] = createSignal("");
  let alive = true;
  onCleanup(() => { alive = false; });
  const invalid = () => !ack() || domain().some(v => !v.trim() || !Number.isFinite(Number(v)))
    || [0, 1, 2].some(i => Number(domain()[i]) >= Number(domain()[i + 3]))
    || !Number.isFinite(Number(cell())) || Number(cell()) <= 0
    || !Number.isFinite(Number(target())) || Number(target()) < .001 || Number(target()) > 1000;
  const submit = async (event: Event) => {
    event.preventDefault();
    if (busy() || invalid() || !file()) return;
    setBusy(true); setError("");
    const identity = file()!.id;
    try {
      if (dirty() || conflict()) await save();
      if (!alive) return;
      if (file()?.id !== identity || dirty() || conflict()) throw new Error(t("run.err.notSaved"));
      const job = await api.researchStart({ backend: "elmer", path: path().trim(),
        design: structuredClone(unwrap(draft)), settings: { closed_pec: true,
          domain_mm: domain().map(Number), max_cell_mm: Number(cell()), target_hz: Number(target()) * 1e9 } });
      void refreshRuns();
      if (alive) { props.close(); openResearch(job.id, "elmer", true); }
    } catch (e) { if (alive) setError((e as Error).message); }
    finally { if (alive) setBusy(false); }
  };
  return <form class="rd-body" onSubmit={submit} data-testid="elmer-design-form">
    <p class="note">{t("elmerDesign.scope")}</p>
    <p class="note">{t("elmerDesign.domainHint")}</p>
    <fieldset disabled={busy()} class="stack">
      <legend>{t("elmerDesign.domain")}</legend>
      <div class="rd-grid">
        <For each={[0, 3, 1, 4, 2, 5]}>{i => {
          const label = `${"XYZ"[i % 3]} ${i < 3 ? "min" : "max"}`;
          return (
          <label class="dz-field"><span class="dz-label">{label} · mm</span>
            <NumberField class="rp-input dz-input mono" required value={domain()[i]} aria-label={label}
              onInput={e => { const value = e.currentTarget.value; setDomain(v => v.map((x, k) => k === i ? value : x)); }} />
          </label>);
        }}</For>
      </div>
      <div class="rd-grid">
        <label class="dz-field"><span class="dz-label">{t("elmerDesign.cell")}</span><NumberField class="rp-input dz-input mono" required min="0.000001" value={cell()} onInput={e => setCell(e.currentTarget.value)} /></label>
        <label class="dz-field"><span class="dz-label">{t("elmerDesign.target")}</span><NumberField class="rp-input dz-input mono" required min="0.001" max="1000" value={target()} onInput={e => setTarget(e.currentTarget.value)} /></label>
      </div>
      <label class="dz-field"><span class="dz-label">{t("research.path.install")}</span><input class="rp-input dz-input" autocomplete="off" value={path()} onInput={e => setPath(e.currentTarget.value)} /><span class="note">{t("elmerDesign.path")}</span></label>
      <label class="gs-check"><input type="checkbox" checked={ack()} onChange={e => setAck(e.currentTarget.checked)} />{t("elmerDesign.pec")}</label>
    </fieldset>
    <Show when={error()}><p class="status-block status-critical" role="alert">{error()}</p></Show>
    <div class="dialog-actions">
      <button class="btn btn-ghost" type="button" disabled={busy()} onClick={() => { props.close(); openResearch(undefined, "elmer"); }}>{t("elmerDesign.setup")}</button>
      <button class="btn btn-primary" type="submit" disabled={busy() || invalid() || !file()}>{busy() ? t("run.starting") : t("run.saveAndRun")}</button>
    </div>
  </form>;
}
