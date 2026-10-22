import { publishReport } from './publish.js';

/** Daily job: have the backend build today's report, then render it to static HTML. */
export async function generateAndPublish(backend, siteDir) {
  const report = await backend.generate();
  await publishReport(siteDir, report);
  return report;
}

/** Re-render every stored report, e.g. after changing the report format. */
export async function rebuildAll(backend, siteDir) {
  const dates = await backend.listDates();
  for (const date of [...dates].reverse()) {
    await publishReport(siteDir, await backend.getReport(date));
  }
  return dates.length;
}
