// Focused contracts for navigation labels in the introductory user guides.
// Prose emphasis and scientific quantities are not UI labels, so check named
// command contracts rather than treating every bold phrase as a translation key.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
const read = path => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const clean = text => text.replace(/\*\*/g, "").replace(/…/g, "").replace(/\s+/g, " ");
for (const lang of ["en", "tr"]) {
  const prefix = lang === "en" ? "docs/" : "docs/tr/";
  const dictionary = JSON.parse(read(`src/i18n/${lang}.json`));
  const guide = read(`${prefix}GETTING-STARTED.md`);
  const designer = read(`${prefix}DESIGNER.md`);
  const text = clean(`${guide}\n${designer}`);
  for (const key of ["ribbon.home.project", "ribbon.home.importCst", "ribbon.home.importPcb", "ribbon.home.saveAs",
    "ribbon.tab.view", "tree.components", "contextMenu.addPort", "ribbon.sim.lumped", "ribbon.post.farfield", "research.title"]) {
    assert.ok(text.includes(clean(dictionary[key])), `${prefix}: current UI label ${key} (${dictionary[key]}) is documented`);
  }
  const macroPath = lang === "en" ? "Home › Design › Import VBA macro" : "Giriş › Tasarım › VBA makrosunu içe aktar";
  assert.ok(clean(guide).includes(macroPath), `${prefix}: full macro navigation path`);
  const screenshotPath = lang === "en" ? "View › View › Screenshot" : "Görünüm › Görünüm › Ekran görüntüsü";
  assert.ok(clean(guide).includes(screenshotPath), `${prefix}: screenshot belongs to View`);
  for (const stale of ["Add a lumped port", "Home › Project", "Home › View ›", "**Components**", "**Resistor**",
    "Giriş › Proje", "Giriş › Düzenle", "Giriş › Görünüm ›", "makrosuyu"]) {
    assert.ok(!`${guide}\n${designer}`.includes(stale), `${prefix}: retired label ${stale}`);
  }
  assert.ok(guide.includes(".results") && guide.includes("FB-DOCUMENT.md"), `${prefix}: portable project workflow`);
  assert.ok(guide.includes("EXPERIMENTAL-SOLVERS.md"), `${prefix}: optional solver setup`);
  const feedback = designer.split(lang === "en" ? "## Feedback" : "## Geri bildirim")[1];
  assert.ok(feedback && !/^### /m.test(feedback), `${prefix}: modeling sections are not nested under feedback`);
  assert.ok(!/^## (Scenario checks|Senaryo kontrolleri)/m.test(designer), `${prefix}: contributor checks stay outside the user guide`);
}
const manifest = JSON.parse(read("landing/docs.json"));
const sources = manifest.groups.flatMap(group => group.pages.map(page => page.source));
for (const name of ["FB-DOCUMENT", "EXPERIMENTAL-SOLVERS", "COAXIAL-PORTS"]) {
  assert.ok(sources.includes(`docs/${name}.md`), `${name}: guide is published in navigation`);
}
console.log("check-docs-labels: EN/TR current ribbon labels, paths, portable projects, solver links and guide structure passed");
