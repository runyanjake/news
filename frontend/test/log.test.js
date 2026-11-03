import assert from 'node:assert/strict';
import { it } from 'node:test';

import { createLogger } from '../src/log.js';

it('writes the shared "<ISO time> <LEVEL> <name>: <message>" format', () => {
  const out = [];
  const err = [];
  const log = createLogger('renderer', {
    stdout: { write: (line) => out.push(line) },
    stderr: { write: (line) => err.push(line) },
  });
  log.info('job started');
  log.warn('retrying');
  assert.match(out[0], /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z INFO renderer: job started\n$/);
  assert.match(err[0], / WARNING renderer: retrying\n$/);
});
