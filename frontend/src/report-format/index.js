/**
 * Report presentation — the ONE place that decides how a report looks.
 *
 * Input is the backend's Report JSON ({date, generated_at, entries: [{topic,
 * tagline, summary, score, ai_generated, sources: [{name, title, url}]}]}).
 * Change layout, ordering, sections or styling here (and in styles.css);
 * nothing else in the renderer knows about the page structure.
 */
import { readFileSync } from 'node:fs';
import { escape, safeUrl } from './html.js';

const SITE_NAME = 'PWS NEWS';
const STYLES = readFileSync(new URL('./styles.css', import.meta.url), 'utf8');

function page(title, body) {
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escape(title)}</title>
<style>${STYLES}</style>
</head>
<body>
<header><h1><a href="/">${SITE_NAME}</a></h1></header>
${body}
<footer><a href="/archive/">Archive</a> · Summaries are AI-generated from the linked sources.</footer>
</body>
</html>
`;
}

function sourceList(sources) {
  const items = sources
    .map((s) => `<li><a href="${safeUrl(s.url)}" rel="noopener">${escape(s.name)}: ${escape(s.title)}</a></li>`)
    .join('');
  return `<ul class="sources">${items}</ul>`;
}

function entryHtml(entry, cssClass) {
  const href = safeUrl(entry.sources[0]?.url);
  return `<article class="${cssClass}">
<a class="tagline" href="${href}" rel="noopener">${escape(entry.tagline)}</a>
<p>${escape(entry.summary)}</p>
${sourceList(entry.sources)}
</article>`;
}

/** Split entries round-robin into `count` columns, preserving rank order across rows. */
function columns(entries, count) {
  const cols = Array.from({ length: count }, () => []);
  entries.forEach((entry, i) => cols[i % count].push(entryHtml(entry, 'entry')));
  return cols.map((col) => `<div class="column">${col.join('\n')}</div>`).join('\n');
}

export function renderReport(report) {
  const [lead, ...rest] = report.entries;
  const date = new Date(`${report.date}T00:00:00`).toDateString();
  const body = lead
    ? `<p class="date">${escape(date)}</p>
<section class="lead">${entryHtml(lead, 'lead-entry')}</section>
<section class="columns">${columns(rest, 3)}</section>`
    : `<p class="date">No stories for ${escape(date)}.</p>`;
  return page(`${SITE_NAME} — ${report.date}`, body);
}

export function renderArchive(dates) {
  const items = dates.map((d) => `<li><a href="/reports/${escape(d)}/">${escape(d)}</a></li>`).join('\n');
  return page(`${SITE_NAME} — Archive`, `<h2>Archive</h2><ul class="archive">${items}</ul>`);
}
