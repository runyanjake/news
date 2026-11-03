/**
 * Same line format as the backend (backend/app/logs.py):
 *   2026-11-03T08:00:00.123Z INFO renderer.scheduler: Daily render job started
 */
export function createLogger(name, { stdout = process.stdout, stderr = process.stderr } = {}) {
  const write = (stream, level) => (message) =>
    stream.write(`${new Date().toISOString()} ${level} ${name}: ${message}\n`);
  return { info: write(stdout, 'INFO'), warn: write(stderr, 'WARNING'), error: write(stderr, 'ERROR') };
}
