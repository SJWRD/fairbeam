// Actual chart components with deterministic stored quantities; no solver or app state mutation.
import { render } from "solid-js/web";
import LineChart from "../../src/charts/LineChart";
import PolarChart from "../../src/charts/PolarChart";
import SmithChart from "../../src/charts/SmithChart";
import { fmt, t } from "../../src/i18n";
import { compareCuts } from "../../src/compare/series";
import { sweep } from "../../src/lib/rf";
import type { Bundle } from "../../src/types";

export function mountChart(host: HTMLElement, kind: string, bundle: Bundle) {
  const label = "Probe 1,2";
  if (kind === "line") return render(() => <LineChart
    series={[undefined, "6 4", "2 3"].map((dash, i) => ({
      id: `cmp-${i}`, label: `${label} · S${i + 1}1`, dash,
      color: `--al-series-${i + 1}`, x: [2.4, 2.45, 2.5], y: [-5 - i, -24 + i, -7 - i],
    }))}
    xLabel={t("chart.frequencyGHz")} yLabel="|S| (dB)" ariaLabel="Export regression line chart"
    bands={[{ x0: 2.42123, x1: 2.47876 }]} markers={[{ x: 2.45, label: `${fmt.fixed(2.45, 3)} GHz` }]}
    inspection={{key:"export-regression",kind:"reflection"}}
    points={[{id:"sample",label:"Point 5,6",color:"--al-series-4",x:[2.44],y:[-3]}]}
  />, host);
  if (kind === "polar") return render(() => <PolarChart
    series={compareCuts([{label,bundle,sweep:sweep(bundle)!}],bundle.results!.farfield[0].f/1.1,0)}
    directLabels={true} max={15} range={30} half={false} unit="dBi" ariaLabel="Export regression polar chart"
  />, host);
  return render(() => <SmithChart
    f={[2.4e9, 2.45e9, 2.5e9]} re={[0.2, 0.1, 0.2]} im={[-0.2, 0, 0.2]}
    zRe={[45, 50, 55]} zIm={[-5, 0, 5]} zRef={50.25}
    zRefF={kind === "smith-variable" ? [49.5, 50.25, 51.1] : undefined}
    markers={[{ f: 2.45e9, label: `${fmt.fixed(2.45, 3)} GHz` }]}
    label={label} overlays={[{label:"Reference 3,4",color:"--al-series-2",re:[0.3,0.15,0.3],im:[-0.1,0,0.1]}]}
    ariaLabel="Export regression Smith chart"
  />, host);
}
