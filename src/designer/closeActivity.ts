import { createSignal } from "solid-js";

type Answer = "clear" | "pending" | "cancel";
type Phase = "initial" | "final";
type Request = {
  id: number; checked: Set<Phase>; checking: boolean; canceled: boolean;
  approved: Set<string>; unknownApproved: boolean; activity: string[] | null;
};
let query: (() => Promise<string[]>) | null = null;
let request: Request | null = null;
export const [closeActivity, setCloseActivity] = createSignal<{ id: number; count: number | null } | null>(null);

/** The mounted desktop close host supplies a fresh, server-wide list, never scoped history. */
export function registerCloseActivity(read: () => Promise<string[]>) {
  query = read;
  return () => {
    if (query !== read) return;
    query = null;
    request = null;
    setCloseActivity(null);
  };
}

/** Check once before unsaved-work questions and once immediately before authorizing exit.
 * A run started during Save/Don't save still needs consent; finished runs need no second question. */
export function checkCloseActivity(id: number, phase: Phase): Answer {
  if (!query) return "clear";
  if (!request || request.id !== id) {
    request = { id, checked: new Set(), checking: false, canceled: false,
      approved: new Set(), unknownApproved: false, activity: [] };
    setCloseActivity(null);
  }
  const current = request;
  if (current.canceled) return "cancel";
  if (current.checking || closeActivity()) return "pending";
  if (current.checked.has(phase)) return "clear";
  current.checking = true;
  void query().then(ids => {
    if (request !== current) return;
    current.activity = ids;
    if (ids.some(id => !current.approved.has(id))) setCloseActivity({ id, count: ids.length });
  }, () => {
    if (request !== current) return;
    current.activity = null;
    // A failed status request is not evidence that no simulations are running.
    if (!current.unknownApproved) setCloseActivity({ id, count: null });
  }).finally(() => {
    current.checking = false;
    current.checked.add(phase);
  });
  return "pending";
}

export function decideCloseActivity(proceed: boolean) {
  if (!request || closeActivity()?.id !== request.id) return;
  if (!proceed) request.canceled = true;
  else if (request.activity === null) request.unknownApproved = true;
  else for (const id of request.activity) request.approved.add(id);
  setCloseActivity(null);
}

/** A replacement dialog can cancel exit while the status request is still in flight. */
export function cancelCloseActivity(id: number) {
  if (request?.id !== id) return;
  request = null;
  setCloseActivity(null);
}

/** A later edit or save question invalidates only the final status check, not run consent. */
export function requireFinalActivityCheck(id: number) {
  if (request?.id === id) request.checked.delete("final");
}
