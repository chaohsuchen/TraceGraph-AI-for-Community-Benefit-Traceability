import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import { ApiError, createHttpApi, waitForJob } from './api.mjs';
import { createDemoApi } from './demo.mjs';

const fixtures = JSON.parse(await readFile(new URL('../contracts/examples.json', import.meta.url), 'utf8'));

test('lookup and refresh preserve the caller’s idempotency keys and contract bodies', async () => {
  const calls = [];
  const api = createHttpApi('/api/v1', async (url, options) => {
    calls.push({ url, ...options });
    return Response.json({ job_id: 'fixture-job' }, { status: 202 });
  });
  await api.lookup('Known commitment', 'lookup-key');
  await api.refresh('TG-GOLD-H008', 'refresh-key');
  assert.equal(calls[0].url, '/api/v1/lookup');
  assert.equal(calls[0].method, 'POST');
  assert.equal(calls[0].headers['Idempotency-Key'], 'lookup-key');
  assert.deepEqual(JSON.parse(calls[0].body), { query: 'Known commitment' });
  assert.equal(calls[1].url, '/api/v1/commitments/TG-GOLD-H008/research-runs');
  assert.equal(calls[1].headers['Idempotency-Key'], 'refresh-key');
  assert.equal(calls[1].body, undefined);
});

test('HTTP errors expose the backend’s message and retry flag', async () => {
  const api = createHttpApi('/api/v1', async () => Response.json({
    error: { code: 'RESOLUTION_UNAVAILABLE', message: 'The model is unavailable.', retryable: true },
  }, { status: 503 }));
  await assert.rejects(api.lookup('Example', 'key'), error =>
    error instanceof ApiError && error.code === 'RESOLUTION_UNAVAILABLE' && error.retryable);
});

test('H008 and H010 share a source while showing commitment-specific evidence', () => {
  const api = createDemoApi(fixtures);
  const funding = api.graph('TG-GOLD-H008');
  const renewable = api.graph('TG-GOLD-H010');
  assert.equal(funding.source_edges[0].source.id, renewable.source_edges[0].source.id);
  assert.notEqual(funding.source_edges[0].evidence[0].status, renewable.source_edges[0].evidence[0].status);
  assert.equal(api.lookup('Bally’s Bronx hiring promise', 'key').outcome, 'AMBIGUOUS');
  assert.equal(api.lookup('Bally’s Bronx construction jobs', 'key').commitment_id, 'TG-GOLD-H009');
});

test('refresh is idempotent and leaves earlier research accessible', async () => {
  const api = createDemoApi(fixtures);
  const id = 'TG-GOLD-H008';
  const previous = api.graph(id).viewed_run_id;
  const { job_id: jobId } = api.refresh(id, 'refresh-key');
  assert.equal(api.refresh(id, 'refresh-key').job_id, jobId);
  const seen = [];
  const job = await waitForJob(api, jobId, { interval: 1, onProgress: status => seen.push(status) });
  assert.deepEqual(seen, ['RUNNING', 'RUNNING']);
  assert.equal(job.result.commitment_id, id);
  assert.equal(api.graph(id).viewed_run_id, job.result.run_id);
  assert.equal(api.graph(id, previous).viewed_run_id, previous);
  assert.equal(api.graph(id, previous).latest_completed_run.id, job.result.run_id);
});

test('failed refresh preserves the prior verdict', async () => {
  const api = createDemoApi(fixtures, { failRefresh: true });
  const id = 'TG-GOLD-H008';
  const original = api.graph(id);
  const { job_id: jobId } = api.refresh(id, 'refresh-key');
  assert.equal((await waitForJob(api, jobId, { interval: 1 })).status, 'FAILED');
  assert.equal(api.graph(id), original);
});
