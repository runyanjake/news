/**
 * Entrypoint.
 *   node src/main.js            run the daily scheduler (default RENDER_TIME 08:00, local TZ)
 *   node src/main.js generate   generate + render once now
 *   node src/main.js rebuild    re-render all stored reports with the current format
 */
import { existsSync } from 'node:fs';
import path from 'node:path';
import { createBackend } from './backend.js';
import { generateAndPublish, rebuildAll } from './jobs.js';
import { msUntilNext } from './schedule.js';

const env = process.env;
const siteDir = env.SITE_DIR ?? new URL('../../data/site', import.meta.url).pathname;
const renderTime = env.RENDER_TIME ?? '08:00';
const retryMinutes = Number(env.RENDER_RETRY_MINUTES ?? 10);
const maxAttempts = Number(env.RENDER_MAX_ATTEMPTS ?? 3);
const backend = createBackend({
  baseUrl: env.BACKEND_URL ?? 'http://localhost:8000',
  adminToken: env.ADMIN_TOKEN ?? '',
});

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const log = (...args) => console.log(new Date().toISOString(), ...args);

async function runWithRetries() {
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      const report = await generateAndPublish(backend, siteDir);
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
