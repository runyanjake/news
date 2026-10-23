// Site layout: index.html (newest), reports/<date>/ (newest per date), archive/<date>/<stamp>/ (all), archive/, privacy/.
import { mkdir, readdir, rename, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { renderArchive, renderPrivacy, renderReport } from './report-format/index.js';

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const STAMP = /^\d{8}T\d{6}Z$/;

/** UTC generated_at as YYYYMMDDTHHMMSSZ, matching the backend's generation stamps. */
export function generationStamp(report) {
  return new Date(report.generated_at).toISOString().replace(/\.\d+Z$/, 'Z').replace(/[-:]/g, '');
}

async function writeAtomic(file, contents) {
  await mkdir(path.dirname(file), { recursive: true });
  const tmp = `${file}.tmp`;
  await writeFile(tmp, contents);
  await rename(tmp, file);
}

async function list(dir, pattern) {
  try {
    return (await readdir(dir)).filter((n) => pattern.test(n)).sort().reverse();
  } catch (err) {
    if (err.code === 'ENOENT') return [];
    throw err;
  }
}

/** Rendered dates, newest first, each with its generation stamps newest first. */
async function renderedHistory(siteDir) {
  const dates = new Set([
    ...(await list(path.join(siteDir, 'reports'), DATE)),
    ...(await list(path.join(siteDir, 'archive'), DATE)),
  ]);
  const days = [];
  for (const date of [...dates].sort().reverse()) {
    days.push({ date, stamps: await list(path.join(siteDir, 'archive', date), STAMP) });
  }
  return days;
}

/** Render one generation into history; make it current for its date and the front page if it is the newest. */
export async function publishReport(siteDir, report) {
  if (!DATE.test(report.date)) throw new Error(`Invalid report date: ${report.date}`);
  const stamp = generationStamp(report);
  const html = renderReport(report);
  await writeAtomic(path.join(siteDir, 'archive', report.date, stamp, 'index.html'), html);

  const days = await renderedHistory(siteDir);
  const day = days.find((d) => d.date === report.date);
  if (day.stamps[0] === stamp) {
    await writeAtomic(path.join(siteDir, 'reports', report.date, 'index.html'), html);
    if (days[0] === day) await writeAtomic(path.join(siteDir, 'index.html'), html);
  }
  await writeAtomic(path.join(siteDir, 'archive', 'index.html'), renderArchive(days));
  await writeAtomic(path.join(siteDir, 'privacy', 'index.html'), renderPrivacy());
}
