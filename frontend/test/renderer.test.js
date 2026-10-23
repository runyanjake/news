import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { afterEach, beforeEach, describe, it } from 'node:test';

import { createBackend } from '../src/backend.js';
import { rebuildAll } from '../src/jobs.js';
import { publishReport } from '../src/publish.js';
import { renderReport } from '../src/report-format/index.js';
import { msUntilNext } from '../src/schedule.js';

const entry = (tagline, url = 'https://example.com/a') => ({
  topic: tagline,
  tagline,
  summary: `${tagline} summary`,
  score: 1,
  ai_generated: true,
  sources: [{ name: 'Example', title: `${tagline} title`, url, score: 1 }],
});
const report = (date, entries = [entry('Lead story'), entry('Second story')]) => ({
  date,
  generated_at: `${date}T15:00:00Z`,
  entries,
});

describe('report format', () => {
  it('renders the lead and remaining entries', () => {
    const html = renderReport(report('2026-10-22'));
    assert.match(html, /class="lead"[\s\S]*Lead story/);
    assert.match(html, /class="columns"[\s\S]*Second story/);
  });

  it('centers the lead source list as a left-aligned block', () => {
    const html = renderReport(report('2026-10-22'));
    assert.match(html, /class="lead"[\s\S]*<ul class="sources">/);
    assert.match(html, /\.lead \.sources \{[^}]*display: inline-block;[^}]*text-align: left;/);
  });

  it('escapes feed content and rejects non-http links', () => {
    const html = renderReport(report('2026-10-22', [entry('<script>x</script>', 'javascript:alert(1)')]));
    assert.ok(!html.includes('<script>x'));
    assert.ok(html.includes('&lt;script&gt;'));
    assert.ok(!html.includes('javascript:'));
  });

  it('handles an empty report', () => {
    assert.match(renderReport(report('2026-10-22', [])), /No stories/);
  });
});

describe('publishReport', () => {
  let dir;
  beforeEach(async () => { dir = await mkdtemp(path.join(tmpdir(), 'site-')); });
  afterEach(() => rm(dir, { recursive: true, force: true }));

  it('keeps history and points the front page at the newest report', async () => {
    await publishReport(dir, report('2026-10-22', [entry('Newer')]));
    await publishReport(dir, report('2026-10-21', [entry('Older')]));

    assert.match(await readFile(path.join(dir, 'index.html'), 'utf8'), /Newer/);
    assert.match(await readFile(path.join(dir, 'reports/2026-10-21/index.html'), 'utf8'), /Older/);
    const archive = await readFile(path.join(dir, 'archive/index.html'), 'utf8');
    assert.ok(archive.indexOf('2026-10-22') < archive.indexOf('2026-10-21'));
  });

  it('rejects unsafe dates', async () => {
    await assert.rejects(publishReport(dir, report('../../etc')), /Invalid report date/);
  });

  it('rebuildAll re-renders every stored report', async () => {
    const stored = { '2026-10-21': report('2026-10-21'), '2026-10-22': report('2026-10-22') };
    const backend = { listDates: async () => Object.keys(stored).reverse(), getReport: async (d) => stored[d] };
    assert.equal(await rebuildAll(backend, dir), 2);
    await readFile(path.join(dir, 'reports/2026-10-21/index.html'));
    assert.match(await readFile(path.join(dir, 'index.html'), 'utf8'), /2026-10-22/);
  });
});

describe('backend client', () => {
  const json = (body, status = 200) => new Response(JSON.stringify(body), { status });

  it('starts generation, polls the job, then fetches the report', async () => {
    const calls = [];
    const statuses = [{ running: true }, { running: false, last_date: '2026-10-22', last_error: null }];
    const fetchImpl = async (url, init = {}) => {
      calls.push(`${init.method ?? 'GET'} ${new URL(url).pathname}`);
      if (url.endsWith('/generate') && init.method === 'POST') {
        assert.equal(init.headers['X-Admin-Token'], 'tok');
        return json({ running: true }, 202);
      }
      if (url.endsWith('/api/jobs/generate')) return json(statuses.shift());
      return json(report('2026-10-22'));
    };
    const backend = createBackend({ baseUrl: 'http://b', adminToken: 'tok', fetchImpl, pollMs: 1 });

    assert.equal((await backend.generate()).date, '2026-10-22');
    assert.deepEqual(calls, [
      'POST /api/reports/generate', 'GET /api/jobs/generate', 'GET /api/jobs/generate', 'GET /api/reports/2026-10-22',
    ]);
  });

  it('surfaces job failures', async () => {
    const fetchImpl = async (url, init = {}) =>
      init.method === 'POST' ? json({}, 202) : json({ running: false, last_error: 'feeds down' });
    const backend = createBackend({ baseUrl: 'http://b', adminToken: 'tok', fetchImpl, pollMs: 1 });
    await assert.rejects(backend.generate(), /feeds down/);
  });
});

describe('msUntilNext', () => {
  it('targets later today or tomorrow', () => {
    const now = new Date(2026, 9, 22, 7, 30);
    assert.equal(msUntilNext(now, '08:00'), 30 * 60_000);
    assert.equal(msUntilNext(new Date(2026, 9, 22, 8, 0), '08:00'), 24 * 3600_000);
  });

  it('rejects malformed times', () => {
    assert.throws(() => msUntilNext(new Date(), '8am'), /Invalid time/);
  });
});
