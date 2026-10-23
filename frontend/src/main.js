// Usage: node src/main.js [schedule (default) | generate (once, now) | rebuild (re-render all reports)]
import { existsSync } from 'node:fs';
import path from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { createBackend } from './backend.js';
import { generateAndPublish, publishLatestIfNew, rebuildAll } from './jobs.js';
import { msUntilNext } from './schedule.js';

const env = process.env;
const siteDir = env.SITE_DIR ?? new URL('../../data/site', import.meta.url).pathname;
const renderTime = env.RENDER_TIME ?? '08:00';
const retryMinutes = Number(env.RENDER_RETRY_MINUTES ?? 10);
const maxAttempts = Number(env.RENDER_MAX_ATTEMPTS ?? 3);
const watchSeconds = Number(env.RENDER_WATCH_SECONDS ?? 60);
const backend = createBackend({
  baseUrl: env.BACKEND_URL ?? 'http://localhost:8000',
  adminToken: env.ADMIN_TOKEN ?? '',
});

const log = (...args) => console.log(new Date().toISOString(), ...args);
let renderedAt = null; // generated_at of the newest report this process has rendered

async function runWithRetries() {
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      const report = await generateAndPublish(backend, siteDir);
      renderedAt = report.generated_at;
      log(`Published ${report.date} (${report.entries.length} entries)`);
      return;
    } catch (err) {
      log(`Attempt ${attempt}/${maxAttempts} failed: ${err.message}`);
      if (attempt < maxAttempts) await sleep(retryMinutes * 60_000);
    }
  }
}

/** A fresh site volume has no front page until the first daily run; render one at startup instead. */
async function ensureFrontPage() {
  if (existsSync(path.join(siteDir, 'index.html'))) return;
  let dates;
  for (let attempt = 1; !dates; attempt++) {
    try {
      dates = await backend.listDates();
    } catch (err) {
      if (attempt >= 30) return log(`No front page and backend unreachable: ${err.message}`);
      await sleep(2_000);
    }
  }
  if (dates.length) {
    log(`No front page; re-rendered ${await rebuildAll(backend, siteDir)} stored reports`);
  } else {
    log('No front page and no stored reports; generating one now');
    await runWithRetries();
  }
}

/** Pick up reports generated outside the daily run (e.g. POST /api/reports/generate). */
async function watchLoop() {
  for (;;) {
    try {
      const previous = renderedAt;
      renderedAt = await publishLatestIfNew(backend, siteDir, renderedAt);
      if (renderedAt !== previous) log(`Rendered latest report (generated ${renderedAt})`);
    } catch (err) {
      if (!err.message.includes('-> 404')) log(`Watching for new reports failed: ${err.message}`);
    }
    await sleep(watchSeconds * 1_000);
  }
}

async function schedulerLoop() {
  await ensureFrontPage();
  log(`Scheduler started; rendering daily at ${renderTime} (TZ=${env.TZ ?? 'system'})`);
  for (;;) {
    const wait = msUntilNext(new Date(), renderTime);
    log(`Next render in ${Math.round(wait / 60_000)} min`);
    await sleep(wait);
    await runWithRetries();
  }
}

const command = process.argv[2] ?? 'schedule';
if (command === 'schedule') {
  watchLoop();
  await schedulerLoop();
} else if (command === 'generate') {
  const report = await generateAndPublish(backend, siteDir);
  log(`Published ${report.date} (${report.entries.length} entries) to ${siteDir}`);
} else if (command === 'rebuild') {
  log(`Re-rendered ${await rebuildAll(backend, siteDir)} reports to ${siteDir}`);
} else {
  console.error(`Unknown command "${command}" (expected schedule | generate | rebuild)`);
  process.exit(2);
}
