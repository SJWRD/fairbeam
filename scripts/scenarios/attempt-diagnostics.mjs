// Preserve evidence for every failed attempt, including one followed by a successful CI retry.
// This wrapper does not decide whether a scenario passes or whether it should be retried.
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { safeDiagnosticText, startupDiagnostics } from './startup-diagnostics.mjs';

export const scenarioAttemptDirectory = (root, scenario, lang, attempt) =>
  join(root, `${scenario}-${lang}`, `attempt-${attempt}`);

export async function withScenarioAttemptDiagnostics(page, { directory, stack, scenario, lang, attempt, results }, run) {
  const diagnostics = startupDiagnostics(page, { directory, stack });
  let original;
  try { return await run(); }
  catch (error) { original = error; throw error; }
  finally {
    try {
      if (original || results.some(result => !result.ok)) {
        await mkdir(directory, { recursive: true });
        // Save the outcome before requesting screenshots: a capture failure must not erase it.
        const record = { scenario, lang, attempt, steps: results };
        await writeFile(join(directory, 'attempt.json'), JSON.stringify(record,
          (_key, value) => typeof value === 'string' ? safeDiagnosticText(value) : value, 2));
        await diagnostics.capture(original ?? new Error(`Scenario ${scenario}/${lang} attempt ${attempt} has failed steps`));
        console.error(`Scenario attempt diagnostics: ${directory}`);
      }
    } catch (error) {
      console.error(`Could not save scenario attempt diagnostics: ${safeDiagnosticText(error.message)}`);
    } finally { diagnostics.dispose(); }
  }
}
