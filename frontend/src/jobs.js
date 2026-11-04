import { markFormat, publishReport, publishSite } from './publish.js';

const publishShared = async (backend, siteDir, report) =>
  publishSite(siteDir, report.site_name, await backend.listSources());

/** Daily job: have the backend build today's report, then render it to static HTML. */
export async function generateAndPublish(backend, siteDir) {
  const report = await backend.generate();
  await publishReport(siteDir, report);
  await publishShared(backend, siteDir, report);
  return report;
}

/** Render the newest stored report if it isn't `renderedAt`; returns the generated_at now on the site. */
export async function publishLatestIfNew(backend, siteDir, renderedAt) {
  const report = await backend.getReport('latest');
  if (report.generated_at === renderedAt) return renderedAt;
  await publishReport(siteDir, report);
  await publishShared(backend, siteDir, report);
  return report.generated_at;
}

/** Re-render every stored generation, oldest first, e.g. after changing the report format. Returns the date count. */
export async function rebuildAll(backend, siteDir) {
  const dates = await backend.listDates();
  let newest;
  for (const date of [...dates].reverse()) {
    const stamps = await backend.listGenerations(date);
    if (!stamps.length) await publishReport(siteDir, newest = await backend.getReport(date)); // stored before history existed
    for (const stamp of [...stamps].reverse()) {
      await publishReport(siteDir, newest = await backend.getGeneration(date, stamp));
    }
  }
  if (newest) await publishShared(backend, siteDir, newest);
  await markFormat(siteDir);
  return dates.length;
}
