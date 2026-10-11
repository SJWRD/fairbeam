import { createMemo, Show } from "solid-js";
import LineChart, { type Series } from "../charts/LineChart";
import type { ResearchResult } from "../runner/api";
import { fmt, t } from "../i18n";
import ElmerFarfieldView from "./ElmerFarfieldView";

export default function ElmerAntennaResults(props:{result:ResearchResult}){
  const s11=createMemo<Series[]>(()=>[{id:"elmer-s11",label:"S11",color:"--al-series-1",x:props.result.frequency_hz.map(f=>f/1e9),
    y:props.result.frequency_hz.map((_,i)=>20*Math.log10(Math.max(1e-15,Math.hypot(props.result.s11_real![i],props.result.s11_imag![i]))))}]);
  const cuts=createMemo<Series[]>(()=>{
    const ff=props.result.farfield;if(!ff)return[];
    return [0,90].map((phi,k)=>({id:`elmer-ff-${phi}`,label:`φ = ${phi}°`,color:`--al-series-${k+1}`,x:ff.theta_deg,
      y:ff.directivity.map(row=>10*Math.log10(Math.max(1e-12,row[ff.phi_deg.indexOf(phi)])))}));
  });
  return <div class="stack">
    <p class="note">{t("elmerAntenna.resultHint")}</p>
    <div class="research-chart"><LineChart series={s11()} xLabel={t("research.frequency")} yLabel="S11 · dB" ariaLabel="S11" /></div>
    <Show when={props.result.farfield}>{ff=><>
      <p>{t("elmerAntenna.farfield")} · {fmt.fixed(ff().frequency_hz/1e9,3)} GHz · {fmt.fixed(ff().peak_directivity_dbi,2)} dBi</p>
      <ElmerFarfieldView field={ff()} />
      <div class="research-chart"><LineChart series={cuts()} xLabel="θ · °" yLabel={t("elmerAntenna.directivity")} ariaLabel={t("elmerAntenna.farfield")} /></div>
    </>}</Show>
  </div>;
}
