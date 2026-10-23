import { setTimeout as sleep } from 'node:timers/promises';

/** Thin client for the FastAPI backend. */

export function createBackend({ baseUrl, adminToken, fetchImpl = fetch, pollMs = 10_000, maxWaitMs = 60 * 60_000 }) {
  async function call(pathname, init = {}) {
    const res = await fetchImpl(`${baseUrl}${pathname}`, init);
    if (!res.ok) throw new Error(`${init.method ?? 'GET'} ${pathname} -> ${res.status} ${await res.text()}`);
    return res.json();
  }

  const getReport = (date) => call(`/api/reports/${encodeURIComponent(date)}`);

  /** Start a generation job and wait for it; generation can take minutes with a local LLM. */
  async function generate() {
    await call('/api/reports/generate', { method: 'POST', headers: { 'X-Admin-Token': adminToken } });
    for (let waited = 0; waited <= maxWaitMs; waited += pollMs) {
      const job = await call('/api/jobs/generate');
      if (!job.running) {
        if (job.last_error) throw new Error(`Generation failed: ${job.last_error}`);
        return getReport(job.last_date);
      }
      await sleep(pollMs);
    }
    throw new Error(`Generation did not finish within ${maxWaitMs / 60_000} min`);
  }

  return { generate, getReport, listDates: () => call('/api/reports') };
}
