import { publishReport } from './publish.js';

/** Daily job: have the backend build today's report, then render it to static HTML. */
export async function generateAndPublish(backend, siteDir) {
  const report = await backend.generate();
  await publishReport(siteDir, report);
  return report;
}

/**
 * Render the newest stored report unless it is the one already rendered, so reports generated
 * outside the renderer (e.g. a direct API call) reach the site. Returns the rendered generated_at.
 */
export async function publishLatestIfNew(backend, siteDir, renderedAt) {
  const report = await backend.getReport('latest');
  if (report.generated_at === renderedAt) return renderedAt;
  await publishReport(siteDir, report);
  return report.generated_at;
}

/** Re-render every stored generation, oldest first, e.g. after changing the report format. Returns the date count. */
export async function rebuildAll(backend, siteDir) {
  const dates = await backend.listDates();
  for (const date of [...dates].reverse()) {
    const stamps = await backend.listGenerations(date);
    if (!stamps.length) await publishReport(siteDir, await backend.getReport(date)); // stored before history existed
    for (const stamp of [...stamps].reverse()) {
      await publishReport(siteDir, await backend.getGeneration(date, stamp));
    }
  }
  return dates.length;
}
