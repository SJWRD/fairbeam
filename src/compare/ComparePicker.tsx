import { createEffect, createMemo, createSignal, For, onCleanup, onMount, Show } from "solid-js";
import { FileUp, GitCompareArrows, TriangleAlert, X } from "lucide-solid";
import { index, source } from "../state";
import { clearPinned, clearReference, compareOpen, importReferenceFile, isPinned, MAX_PINNED, pin, pinned, reference, refError, REF_KEY, restorePinned, setCompareOpen, unpin } from "./store";
import { REFERENCE_ACCEPT } from "../import/accept";
import { labelOf, projectLabels } from "../lib/projectLabels";
import { fmt, t, decimalComma } from "../i18n";

/** Dock-bar control: pin up to seven other projects as comparison traces. Self-contained so it can
 * sit next to other dock tools. */
export default function ComparePicker() {
  let root: HTMLDivElement | undefined;
  let button: HTMLButtonElement | undefined;
  const [position, setPosition] = createSignal<{ left: string; width: string; "max-height": string }>();

  // Dock tools wrap independently of viewport breakpoints. Keep this trigger-anchored panel
  // inside both its own pane and the viewport, including when the side panels are resized.
  const place = () => {
    if (!root || !button) return;
    const anchor = root.getBoundingClientRect();
    const dock = root.closest(".dock")?.getBoundingClientRect();
    const left = Math.max(0, dock?.left ?? 0) + 8;
    const right = Math.min(window.innerWidth, dock?.right ?? window.innerWidth) - 8;
    const width = Math.max(0, Math.min(340, right - left));
    const x = Math.max(left, Math.min(button.getBoundingClientRect().right - width, right - width));
    // The rendered bottom already includes the CSS gap above the trigger (including rem scaling).
    const bottom = root.querySelector(".cmp-pop")?.getBoundingClientRect().bottom ?? anchor.top;
    setPosition({ left: `${x - anchor.left}px`, width: `${width}px`, "max-height": `min(420px, 70vh, ${Math.max(0, bottom - 8)}px)` });
  };

  let refInput: HTMLInputElement | undefined;
  // labels over the whole index, so a project reads the same here as in the header's picker
  const labels = createMemo(() => projectLabels(index()));
  const candidates = () => index().filter((p) => p.simulated && p.file !== source());
  const full = () => pinned().length >= MAX_PINNED;
  onMount(restorePinned);

  createEffect(() => {
    if (!compareOpen()) return;
    const onDown = (e: PointerEvent) => {
      if (root && !root.contains(e.target as Node)) setCompareOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        setCompareOpen(false);
        button?.focus();
      }
    };
    const observer = new ResizeObserver(place);
    for (const element of [root, root?.closest(".dock"), root?.closest(".dock-bar")]) if (element) observer.observe(element);
    window.addEventListener("resize", place);
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey, true);
    queueMicrotask(() => {
      place();
      (root?.querySelector(".cmp-pop input, .cmp-pop button") as HTMLElement | null)?.focus();
    });
    onCleanup(() => {
      observer.disconnect();
      window.removeEventListener("resize", place);
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey, true);
    });
  });

  return (
    <div class="cmp" ref={root}>
      <button
        ref={button}
        class="btn btn-ghost btn-sm"
        aria-haspopup="dialog"
        aria-expanded={compareOpen()}
        aria-pressed={pinned().length > 0}
        onClick={() => setCompareOpen(!compareOpen())}
        title={t("compare.picker.title")}
      >
        <GitCompareArrows size={14} aria-hidden="true" />
        <span class="btn-label">{t("results.toolbar.compare")}</span>
        <Show when={pinned().length}>
          <span class="cmp-count mono" aria-label={t("compare.picker.pinned", { count: pinned().length })}>{pinned().length}</span>
        </Show>
      </button>
      <Show when={compareOpen()}>
        <div class="cmp-pop" style={position()} role="dialog" aria-label={t("compare.picker.aria")}>
          <div class="section-label">
            {t("compare.picker.with")}
            <Show when={pinned().length}>
              <button class="btn btn-ghost btn-sm push" onClick={clearPinned}>{t("compare.picker.clear")}</button>
            </Show>
          </div>
          <p class="cmp-hint">{t("compare.picker.hint", { count: MAX_PINNED })}</p>
          <Show when={candidates().length} fallback={<p class="cmp-hint">{t("compare.picker.none")}</p>}>
            <ul class="cmp-list">
              <For each={candidates()}>
                {(p) => {
                  const on = () => isPinned(p.file);
                  return (
                    <li>
                      <label class="toggle" classList={{ disabled: !on() && full() }} title={p.file}>
                        <input
                          type="checkbox"
                          checked={on()}
                          disabled={!on() && full()}
                          onChange={(e) => (e.currentTarget.checked ? pin(p.file) : unpin(p.file))}
                        />
                        <span class="toggle-box" aria-hidden="true" />
                        <span class="cmp-name">
                          <span class="cmp-title">{labelOf(labels(), p)}</span>
                          <span class="cmp-sub mono">
                            {p.model}
                            <Show when={p.bands.length}> · {p.bands.map((b) => fmt.fixed(b, 3)).join(decimalComma() ? "; " : ", ")} GHz</Show>
                          </span>
                        </span>
                      </label>
                    </li>
                  );
                }}
              </For>
            </ul>
          </Show>
          <div class="section-label cmp-ref-head">
            {t("compare.picker.reference")}
            <button class="btn btn-ghost btn-sm push" onClick={() => refInput?.click()} title={t("compare.picker.importTitle")}>
              <FileUp size={14} aria-hidden="true" /> {reference() ? t("compare.picker.addFile") : t("compare.picker.import")}
            </button>
          </div>
          <input
            ref={refInput}
            type="file"
            accept={REFERENCE_ACCEPT}
            hidden
            onChange={(e) => {
              const f = e.currentTarget.files?.[0];
              if (f) importReferenceFile(f);
              e.currentTarget.value = "";
            }}
          />
          <Show when={reference()} fallback={<p class="cmp-hint">{t("compare.picker.referenceHint")}</p>}>
            {(r) => (
              <p class="cmp-missing">
                <span class="mono" title={r().reference.notes.join("\n")}>{r().reference.label}</span>
                <button class="icon-btn icon-btn-sm" onClick={clearReference} aria-label={t("compare.card.remove")}>
                  <X size={14} aria-hidden="true" />
                </button>
              </p>
            )}
          </Show>
          <Show when={refError()}>
            <p class="status-block status-warn" role="alert">
              <TriangleAlert size={14} aria-hidden="true" />
              <span>{refError()}</span>
            </p>
          </Show>
          <For each={pinned().filter((p) => p.file !== REF_KEY && (p.error || p.file === source() || !index().some((q) => q.file === p.file)))}>
            {(p) => (
              <p class="cmp-missing">
                <span class="mono">{p.file}</span>
                <span class="muted">{p.error ? ` · ${p.error}` : ` · ${p.file === source() ? t("compare.picker.openNow") : t("compare.picker.notListed")}`}</span>
                <button class="icon-btn icon-btn-sm" onClick={() => unpin(p.file)} aria-label={t("compare.picker.unpin", { file: p.file })}>
                  <X size={14} />
                </button>
              </p>
            )}
          </For>
        </div>
      </Show>
    </div>
  );
}
