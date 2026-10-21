// Closing the desktop window or quitting fairbeam (#99). The native shell (src-tauri/src/main.rs)
// holds every close request (the title-bar X, Alt+F4, Close Window, File > Exit, Quit / Cmd+Q)
// and asks this page with `window.fairbeamWindowClose(kind, id)`. The viewer has no IPC, so the
// shell asks again every 150 ms while the answer is "pending": the Save / Don't save / Cancel
// question (CloseProject.tsx) is open. "close" lets the window go, "cancel" keeps it.
//
// Nothing unsaved (no design open, or a clean one with no save running) closes at once. The
// decision itself is the #86 contract: Save must finish cleanly (saveBeforeLeaving), Don't save
// closes the design and drops its local backup (closeDesign). Updates reuse the question but
// preserve the draft until the native install continuation commits.
import { createSignal } from "solid-js";
import { dirty, draft, file, replaceRequest, saving } from "./store";
import { checkUnsavedWork, resetUnsavedWorkApproval } from "../lib/unsavedWork";

export type WindowLeave = "window" | "quit" | "update";
export type WindowCloseAnswer = "close" | "pending" | "cancel";

/** The open question: how the app is left, and the shell's request number. */
export const [windowClose, setWindowClose] = createSignal<{ kind: WindowLeave; id: number } | null>(null);
/** the choice made in the dialog, handed to the shell on its next question for the same request */
let decided: { id: number; answer: "close" | "cancel" } | null = null;
let updateDiscard: { id: number; snapshot: string } | null = null;
export const [updateApproved, setUpdateApproved] = createSignal<number | null>(null);
const designSnapshot = () => JSON.stringify([file()?.id, file()?.backup_scope, draft]);

/** Update consent is reversible until the native installer commits. Keep the draft and backup. */
export function approveUpdateDiscard() {
  const open = windowClose();
  if (open?.kind !== "update" || saving()) return;
  updateDiscard = { id: open.id, snapshot: designSnapshot() };
  decideWindowClose("close");
}

/** Cancellation, a failed install or an unavailable viewer releases only this update request. */
export function finishUpdate(id: number) {
  if (updateApproved() === id) setUpdateApproved(null);
  if (updateDiscard?.id === id) updateDiscard = null;
  const open = windowClose();
  if (open?.id === id && open.kind === "update") decideWindowClose("cancel");
}

/** An unsaved draft, or a save that has not finished (the server must not stop under it). */
export const unsavedAtStake = () => !!file() && (dirty() || saving());

export function answerWindowClose(kind: WindowLeave, id: number): WindowCloseAnswer {
  if (updateApproved() !== null) return updateApproved() === id && kind === "update" ? "close" : "cancel";
  const open = windowClose();
  if (open) {
    // A native Close/Quit can replace a pending update while retaining the same dialog.
    if (open.id !== id || kind !== open.kind) setWindowClose({ kind, id });
    return "pending";
  }
  const choice = decided;
  decided = null;
  if (choice?.id === id && choice.answer === "cancel") return "cancel";
  // Native destroy/exit bypasses beforeunload. Obtain separate, snapshot-bound
  // approval for Python text before allowing the shell to close the window.
  if (replaceRequest()) return "cancel";
  const extra = checkUnsavedWork(id);
  if (extra !== "clear") return extra;
  // "close" is honoured only while nothing is at stake: an edit or a save since then asks again
  const approvedDraft = kind === "update" && !saving() && updateDiscard?.id === id
    && updateDiscard.snapshot === designSnapshot();
  if (!unsavedAtStake() || approvedDraft) {
    // Synchronous signal update: the UI blocks edits before the shell sees its approval.
    if (kind === "update") setUpdateApproved(id);
    return "close";
  }
  // the open "Save changes before creating/opening…?" question has to be answered first
  if (replaceRequest()) return "cancel";
  setWindowClose({ kind, id });
  return "pending";
}

/** The dialog's outcome: after a successful Save or Don't save ("close"), or Cancel. */
export function decideWindowClose(answer: "close" | "cancel") {
  const open = windowClose();
  if (!open) return;
  if (answer === "cancel") resetUnsavedWorkApproval();
  decided = { id: open.id, answer };
  setWindowClose(null);
}
