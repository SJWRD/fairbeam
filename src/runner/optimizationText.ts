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
    const magnitude = Math.abs(value);
    // Component values may be far below one (F/H) or very large (ohms). Significant
    // digits keep the best value meaningful without changing the stored parameters.
    const number = fmt.intl(value === 0 ? 0 : value, {
      maximumSignificantDigits: 6, useGrouping: false,
      notation: magnitude > 0 && (magnitude < 1e-3 || magnitude >= 1e6) ? "scientific" : "standard",
    });
    return `${key}=${number}${unit ? ` ${unit}` : ""}`;
  }).join("; ");
}
