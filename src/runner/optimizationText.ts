import { fmt, t } from "../i18n";
import type { ParamSpec } from "./api";

/** The optimizer's stop reasons; unknown server diagnostics remain visible verbatim. */
const REASONS: Record<string, string> = {
  "goals met": "opt.reason.goalsMet",
  "max evaluations": "opt.reason.maxEvals",
  "simplex converged": "opt.reason.simplexConverged",
  "evaluation budget exhausted": "opt.reason.budgetExhausted",
  "trust region converged": "opt.reason.trustRegionConverged",
  "no resonance in the frequency range": "opt.reason.noResonance",
  "target outside the parameter bounds": "opt.reason.targetOutside",
  "parameter resolution reached": "opt.reason.resolution",
  "secant steps exhausted": "opt.reason.secantExhausted",
  done: "opt.reason.done",
};
export const reasonText = (reason: string): string => REASONS[reason] ? t(REASONS[reason]) : reason;

export function optimizationValues(values: Record<string, number>, specs: readonly Pick<ParamSpec, "key" | "unit">[] = []): string {
  return Object.entries(values).map(([key, value]) => {
    const unit = specs.find(spec => spec.key === key)?.unit;
    return `${key}=${fmt.num(value, 4)}${unit ? ` ${unit}` : ""}`;
  }).join("; ");
}
