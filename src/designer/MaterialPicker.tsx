import { For, onMount, Show, type JSX } from "solid-js";
import { addLibraryMaterial, addUserMaterial, draft } from "./store";
import { MATERIAL_LIBRARY } from "./materials";
import { libraryLabel } from "./MaterialLibrary";
import { ensureUserMaterials, userMaterials } from "./userMaterialsStore";
import { t } from "../i18n";

/** Selecting a library entry copies it into this design, including its material properties. */
export default function MaterialPicker(props: {
  value: string; onChange: (name: string) => void; class?: string; children?: JSX.Element;
}) {
  onMount(() => { void ensureUserMaterials(); });
  const choose = (option: HTMLOptionElement) => {
    if (option.dataset.library) {
      const entry = MATERIAL_LIBRARY.find(m => m.id === option.dataset.library);
      if (entry) props.onChange(addLibraryMaterial(entry));
    } else if (option.dataset.user) {
      const entry = userMaterials().find(m => m.id === option.dataset.user);
      if (entry) props.onChange(addUserMaterial(entry));
    } else props.onChange(option.value);
  };
  return <select class={props.class ?? "rp-select dz-input"} value={props.value} onChange={e => choose(e.currentTarget.selectedOptions[0])}>
    {props.children}
    <For each={draft.materials}>{m => <option value={m.name}>{m.name} ({t(m.kind === "metal" ? "shape.materialKind.metal" : "shape.materialKind.dielectric")})</option>}</For>
    <optgroup label={t("materials.group.builtin")}><For each={MATERIAL_LIBRARY}>{m => <option value={`library:${m.id}`} data-library={m.id}>{libraryLabel(m)}</option>}</For></optgroup>
    <Show when={userMaterials().length}><optgroup label={t("materials.group.mine")}><For each={userMaterials()}>{m => <option value={`user:${m.id}`} data-user={m.id}>{m.name}</option>}</For></optgroup></Show>
  </select>;
}
