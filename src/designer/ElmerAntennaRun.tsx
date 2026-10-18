import { createEffect, createSignal, For, onCleanup, Show } from "solid-js";
import { unwrap } from "solid-js/store";
import { api } from "../runner/api";
import { openResearch, researchPath } from "../runner/researchState";
import { followInDesigner } from "../runner/designRun";
import { refreshRuns } from "../runner/store";
import { conflict, dirty, draft, file, names, save } from "./store";
import { tryEvaluate } from "./expr";
import { t } from "../i18n";
import NumberField from "../components/NumberField";
import { hasDocumentResultScope } from "../lib/nativeDocuments";

export default function ElmerAntennaRun(props: { close: () => void }) {
  const number = (v: unknown, fallback: number) => Number((tryEvaluate(v as string | number, names().names).value ?? fallback).toPrecision(12));
  const [values, setValues] = createSignal({
    f_min_hz: String(number(draft.simulation?.f_min, 1.5)), f_max_hz: String(number(draft.simulation?.f_max, 3.2)),
    farfield_hz: String(number(draft.far_field?.frequencies?.[0], 2.45)), points: "11",
    cell_mm: "12", padding_mm: "40", feed_width_mm: "1", dielectric_cells: "2",
  });
  const [ack,setAck]=createSignal(false), [busy,setBusy]=createSignal(false), [error,setError]=createSignal("");
  const [diagnostic, setDiagnostic] = createSignal("");
  const setup = () => { props.close(); openResearch(undefined, "elmer"); };
  let alive=true; onCleanup(()=>{alive=false;});
  let alert: HTMLParagraphElement | undefined;
  createEffect(() => {
    if (error()) queueMicrotask(() => { if (alive) alert?.scrollIntoView({ block: "nearest" }); });
  });
  const invalid=()=>!ack() || Object.values(values()).some(v=>!v.trim() || !Number.isFinite(Number(v)) || Number(v)<=0)
    || Number(values().f_min_hz)>=Number(values().f_max_hz)
    || Number(values().farfield_hz)<Number(values().f_min_hz) || Number(values().farfield_hz)>Number(values().f_max_hz)
    || !Number.isInteger(Number(values().points)) || Number(values().points)<3 || Number(values().points)>21
    || !Number.isInteger(Number(values().dielectric_cells)) || Number(values().dielectric_cells)<2 || Number(values().dielectric_cells)>10;
  const submit=async(e:Event)=>{
    e.preventDefault(); if(invalid() || busy() || !file())return;
    setBusy(true);setError("");setDiagnostic(""); const identity=file()!.id;
    try{
      const health = await api.health();
      if (health.elmer_driven !== 2 || (hasDocumentResultScope(file()) && !health.research_source)) throw Error(t("elmerAntenna.serverUpdate"));
      if (!alive) return;
      const capability = await api.researchProbe("elmer", researchPath("elmer"));
      if (!alive) return;
      if (!capability.available) {
        setDiagnostic(capability.reason ?? "");
        throw Error(t("elmerAntenna.unavailable"));
      }
      if(dirty() || conflict())await save();
      if(!alive)return;
      if(file()?.id!==identity || dirty() || conflict())throw Error(t("run.err.notSaved"));
      const settings:Record<string,unknown>={analysis:"driven",accept_finite_feed:true,approximate_metals:true};
      for(const [key,v] of Object.entries(values()))settings[key]=Number(v)*(key.endsWith("_hz")?1e9:1);
      const saved = file()!;
      const source = health.research_source && saved.backup_scope
        ? {model_id:saved.id,workspace_hash:saved.hash,backup_scope:saved.backup_scope} : undefined;
      if (hasDocumentResultScope(saved) && !source) throw Error(t("elmerAntenna.serverUpdate"));
      const job=await api.researchStart({backend:"elmer",path:researchPath("elmer"),design:structuredClone(unwrap(draft)),settings,...(source ? {source} : {})});
      void refreshRuns();if(alive && file()?.id === saved.id && file()?.backup_scope === saved.backup_scope){props.close();followInDesigner(job);}
    }catch(e){if(alive)setError((e as Error).message);}finally{if(alive)setBusy(false);}
  };
  return <form class="rd-body" onSubmit={submit} data-testid="elmer-antenna-form">
    <p class="note">{t("elmerAntenna.scope")}</p>
    <p class="note">{t("elmerAntenna.pec")}</p>
    <div class="cluster"><p class="note">{t("elmerAntenna.setupHint")}</p>
      <button class="btn btn-ghost btn-sm" type="button" disabled={busy()} onClick={setup}>{t("elmerAntenna.setup")}</button>
    </div>
    <fieldset class="stack" disabled={busy()}><legend>{t("elmerAntenna.option")}</legend>
      <div class="rd-grid"><For each={Object.keys(values()) as (keyof ReturnType<typeof values>)[]}>{key=><label class="dz-field">
        <span class="dz-label">{t(`elmerAntenna.${key}`)}</span>
        <NumberField class="rp-input dz-input mono" required value={values()[key]} aria-label={t(`elmerAntenna.${key}`)}
          onInput={e=>{const v=e.currentTarget.value;setValues(old=>({...old,[key]:v}));}} />
      </label>}</For></div>
      <p class="note">{t("elmerAntenna.boundary")}</p>
      <label class="gs-check"><input type="checkbox" checked={ack()} onChange={e=>setAck(e.currentTarget.checked)}/>{t("elmerAntenna.accept")}</label>
    </fieldset>
    <Show when={error()}><p ref={alert} class="status-block status-critical" role="alert">{error()}</p></Show>
    <Show when={diagnostic()}><details><summary>{t("common.details")}</summary><p class="note">{diagnostic()}</p></details></Show>
    <div class="dialog-actions"><button class="btn btn-primary" type="submit" disabled={busy() || invalid()}>{busy()?t("run.starting"):t("run.saveAndRun")}</button></div>
  </form>;
}
