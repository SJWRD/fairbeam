// Kept separate from run effects: Checks can queue a field before the lazy dialog mounts.
import { batch, createSignal } from "solid-js";

const [simSettingsOpen, setOpen] = createSignal(false);
export { simSettingsOpen };
export const [simSettingsSection, setSimSettingsSection] = createSignal("freq");
export const [simSettingsFocus, setSimSettingsFocus] = createSignal<{ path: string } | null>(null);

export function setSimSettingsOpen(open: boolean) {
  batch(() => {
    if (!open) setSimSettingsFocus(null);
    setOpen(open);
  });
}

/** Ribbon entry points can add monitors; check links only reveal existing fields. */
export function openSimSettings(section = "freq") {
  batch(() => {
    setSimSettingsFocus(null);
    setSimSettingsSection(section);
    setOpen(true);
  });
}

export function focusSimSettings(path: string) {
  batch(() => {
    setSimSettingsFocus({ path });
    setOpen(true);
  });
}
