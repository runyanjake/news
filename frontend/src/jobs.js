import { publishReport } from './publish.js';

/** Daily job: have the backend build today's report, then render it to static HTML. */
export async function generateAndPublish(backend, siteDir) {
  const report = await backend.generate();
  await publishReport(siteDir, report);
  return report;
}

/** Render the newest stored report if it isn't `renderedAt`; returns the generated_at now on the site. */
export async function publishLatestIfNew(backend, siteDir, renderedAt) {
  const report = await backend.getReport('latest');
  if (report.generated_at === renderedAt) return renderedAt;
  await publishReport(siteDir, report);
  return report.generated_at;
}

/** Re-render every stored report, e.g. after changing the report format. */
export async function rebuildAll(backend, siteDir) {
  const dates = await backend.listDates();
  for (const date of [...dates].reverse()) {
    await publishReport(siteDir, await backend.getReport(date));
  }
  return dates.length;
}
