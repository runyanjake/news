// Site layout: index.html (newest), reports/<date>/ (newest per date), archive/<date>/<stamp>/ (all), archive/, privacy/,
// assets/ (fonts), .format (which report format rendered the site).
import { createHash } from 'node:crypto';
import { mkdir, readFile, readdir, rename, stat, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { ASSETS_DIR, renderArchive, renderPrivacy, renderReport } from './report-format/index.js';

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const STAMP = /^\d{8}T\d{6}Z$/;
const FORMAT_DIR = fileURLToPath(new URL('./report-format/', import.meta.url));

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
  await writeAtomic(path.join(siteDir, 'archive', 'index.html'), renderArchive(days, report.site_name));
}

/** Pages and files shared by every report: the privacy page (with the current source list) and assets/. */
export async function publishSite(siteDir, siteName, sources) {
  await writeAtomic(path.join(siteDir, 'privacy', 'index.html'), renderPrivacy(siteName, sources));
  const assets = fileURLToPath(ASSETS_DIR);
  for (const name of await readdir(assets)) {
    await writeAtomic(path.join(siteDir, 'assets', name), await readFile(path.join(assets, name)));
  }
}

/** Hash of everything under report-format/: any layout, style or asset change gives a new version. */
export async function formatVersion() {
  const hash = createHash('sha256');
  for (const name of (await readdir(FORMAT_DIR, { recursive: true })).sort()) {
    const file = path.join(FORMAT_DIR, name);
    if ((await stat(file)).isFile()) hash.update(name).update(await readFile(file));
  }
  return hash.digest('hex').slice(0, 16);
}

/** False when the site has no front page or was rendered by a different report format. */
export async function siteIsCurrent(siteDir) {
  try {
    await stat(path.join(siteDir, 'index.html'));
    return (await readFile(path.join(siteDir, '.format'), 'utf8')) === (await formatVersion());
  } catch (err) {
    if (err.code === 'ENOENT') return false;
    throw err;
  }
}

/** Record that every page in the site was rendered by the current report format. */
export async function markFormat(siteDir) {
  await writeAtomic(path.join(siteDir, '.format'), await formatVersion());
}
