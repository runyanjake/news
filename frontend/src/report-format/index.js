// All page layout lives here and in styles.css; input is the backend Report JSON (backend/app/models.py).
// Content, including the site name and report titles, comes from the backend; this only lays it out.
import { readFileSync } from 'node:fs';
import { escape, safeUrl } from './html.js';
import { shuffled } from './shuffle.js';

const STYLES = readFileSync(new URL('./styles.css', import.meta.url), 'utf8');

/** Fonts and their licenses; the publisher copies this directory to /assets/. */
export const ASSETS_DIR = new URL('./assets/', import.meta.url);

/** Change whenever the privacy policy or disclaimer text below changes. */
export const POLICY_UPDATED = '2026-11-12';

// Applied in <head> so the stored (or system) choice is in place before first paint.
const PREFS_INIT = `(function(){var d=document.documentElement;try{var s=localStorage;
d.dataset.theme=s.getItem('theme')||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');
d.dataset.font=s.getItem('font')||'serif';}catch(e){}})();`;
const PREFS_TOGGLE = `(function(){var next={font:{serif:'sans',sans:'serif'},theme:{light:'dark',dark:'light'}};
document.querySelectorAll('.prefs [data-pref]').forEach(function(b){b.addEventListener('click',function(){
var d=document.documentElement,k=b.dataset.pref,v=next[k][d.dataset[k]]||Object.values(next[k])[0];
d.dataset[k]=v;try{localStorage.setItem(k,v);}catch(e){}});});})();`;
const PREFS = `<div class="prefs" role="group" aria-label="Display options">
<button type="button" data-pref="font" title="Serif / sans-serif" aria-label="Switch between serif and sans-serif">Aa</button>
<button type="button" data-pref="theme" title="Light / dark" aria-label="Switch between light and dark mode">&#9680;</button>
</div>
<script>${PREFS_TOGGLE}</script>`;

function page(siteName, title, body) {
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escape(title)}</title>
<style>${STYLES}</style>
<script>${PREFS_INIT}</script>
</head>
<body>
<header><h1><a href="/">${escape(siteName)}</a></h1></header>
${body}
<footer><a href="/archive/">Archive</a> · <a href="/sources/">Sources</a> · <a href="/privacy/">Privacy Policy</a> · <a href="/disclaimer/">Disclaimer</a> · Headlines and summaries are AI-generated from the linked sources.</footer>
${PREFS}
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

/** Split entries round-robin into `count` columns, keeping their order across rows. */
function columns(entries, count) {
  const cols = Array.from({ length: count }, () => []);
  entries.forEach((entry, i) => cols[i % count].push(entryHtml(entry, 'entry')));
  return cols.map((col) => `<div class="column">${col.join('\n')}</div>`).join('\n');
}

export function renderReport(report) {
  const [lead, ...ranked] = report.entries;
  // The top story leads; the rest are mixed so similar topics don't cluster. Seeding keeps re-renders identical.
  const rest = shuffled(ranked, report.generated_at);
  const date = new Date(`${report.date}T00:00:00`).toDateString();
  const body = lead
    ? `<p class="date">${escape(date)}</p>
<section class="lead">${entryHtml(lead, 'lead-entry')}</section>
<section class="columns">${columns(rest, 3)}</section>`
    : `<p class="date">No stories for ${escape(date)}.</p>`;
  return page(report.site_name, report.title, body);
}

/** "20261022T233000Z" -> "23:30 UTC" */
const stampTime = (stamp) => `${stamp.slice(9, 11)}:${stamp.slice(11, 13)} UTC`;

/** `days`: [{date, stamps}] newest first; earlier generations of a date are listed under it. */
export function renderArchive(days, siteName) {
  const items = days.map(({ date, stamps }) => {
    const runs = stamps.length > 1
      ? `<ul>${stamps.map((s) => `<li><a href="/archive/${escape(date)}/${escape(s)}/">${escape(stampTime(s))}</a></li>`).join('')}</ul>`
      : '';
    return `<li><a href="/reports/${escape(date)}/">${escape(date)}</a>${runs}</li>`;
  }).join('\n');
  return page(siteName, `${siteName} — Archive`, `<h2>Archive</h2><ul class="archive">${items}</ul>`);
}

const longDate = (iso) =>
  new Date(`${iso}T00:00:00`).toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' });

const updated = () =>
  `<p class="updated">Last updated: <time datetime="${POLICY_UPDATED}">${longDate(POLICY_UPDATED)}</time></p>`;

export function renderPrivacy(siteName) {
  const name = escape(siteName);
  return page(siteName, `${siteName} — Privacy Policy`, `<article class="policy">
<h2>Privacy Policy</h2>
${updated()}
<p>${name} may collect visitor data, such as IP address, browser user agent, requested page and time of visit,
as part of standard server logging for troubleshooting and site metrics. This data is not sold to advertisers.
There are no accounts or advertising; your font and light/dark choices are stored only in your own browser.
Links to sources lead to the publishers' websites, which have their own privacy policies.</p>
</article>`);
}

export function renderDisclaimer(siteName) {
  const name = escape(siteName);
  return page(siteName, `${siteName} — Disclaimer`, `<article class="policy">
<h2>Disclaimer</h2>
${updated()}
<p>Headlines and descriptions on this site are generated by AI from the linked third-party news sources and can be
inaccurate or incomplete. The content reflects the original authors and publishers, augmented by AI, and does not
reflect the views of ${name}, PWS or Whitney.rip. Please refer to the original source for the full context.</p>
</article>`);
}

/**
 * `sources`: [{name, homepage?}] from the backend's GET /api/sources; listed once each, alphabetically,
 * linked to the publisher's homepage when one is configured.
 */
export function renderSources(siteName, sources = []) {
  const homepages = new Map();
  for (const s of sources) if (!homepages.get(s.name)) homepages.set(s.name, s.homepage);
  const names = [...homepages.keys()].sort((a, b) => a.localeCompare(b));
  const list = names
    .map((n) => {
      const home = homepages.get(n);
      return home ? `<li><a href="${safeUrl(home)}" rel="noopener">${escape(n)}</a></li>` : `<li>${escape(n)}</li>`;
    })
    .join('');
  return page(siteName, `${siteName} — Sources`, `<article class="policy">
<h2>Sources</h2>
${list ? `<ul class="source-list">${list}</ul>` : '<p>No sources are configured.</p>'}
</article>`);
}
