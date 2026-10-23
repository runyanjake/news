// Site layout: index.html (latest), reports/<date>/, archive/ and privacy/, each an index.html nginx serves.
import { mkdir, readdir, rename, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { renderArchive, renderPrivacy, renderReport } from './report-format/index.js';

const DATE = /^\d{4}-\d{2}-\d{2}$/;

async function writeAtomic(file, contents) {
  await mkdir(path.dirname(file), { recursive: true });
  const tmp = `${file}.tmp`;
  await writeFile(tmp, contents);
  await rename(tmp, file);
}

async function renderedDates(siteDir) {
  try {
    const names = await readdir(path.join(siteDir, 'reports'));
    return names.filter((n) => DATE.test(n)).sort().reverse();
  } catch (err) {
    if (err.code === 'ENOENT') return [];
    throw err;
  }
}

/** Render one report into history; refresh the front page if it is the newest. */
export async function publishReport(siteDir, report) {
  if (!DATE.test(report.date)) throw new Error(`Invalid report date: ${report.date}`);
  const html = renderReport(report);
  await writeAtomic(path.join(siteDir, 'reports', report.date, 'index.html'), html);

  const dates = await renderedDates(siteDir);
  if (dates[0] === report.date) await writeAtomic(path.join(siteDir, 'index.html'), html);
  await writeAtomic(path.join(siteDir, 'archive', 'index.html'), renderArchive(dates));
  await writeAtomic(path.join(siteDir, 'privacy', 'index.html'), renderPrivacy());
}
