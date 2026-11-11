import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ApiError, createHttpApi, waitForJob } from './api.mjs';
import { DEMO_COMMITMENT_ID, createDemoApi } from './demo.mjs';

test('lookup and refresh preserve the caller’s idempotency keys and contract bodies', async () => {
  const calls = [];
  const api = createHttpApi('/api/v1', async (url, options) => {
    calls.push({ url, ...options });
    return Response.json({ job_id: 'fixture-job' }, { status: 202 });
  });
  await api.lookup('Known commitment', 'lookup-key');
  await api.refresh(DEMO_COMMITMENT_ID, 'refresh-key');
  assert.equal(calls[0].url, '/api/v1/lookup');
  assert.equal(calls[0].method, 'POST');
  assert.equal(calls[0].headers['Idempotency-Key'], 'lookup-key');
  assert.deepEqual(JSON.parse(calls[0].body), { query: 'Known commitment' });
  assert.equal(calls[1].url, '/api/v1/commitments/' + DEMO_COMMITMENT_ID + '/research-runs');
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

test('the standalone demo opens with cumulative findings, progress and conflict', () => {
  const api = createDemoApi();
  const graph = api.graph(DEMO_COMMITMENT_ID);
  assert.equal(graph.assessment.label, 'CONFLICTING');
  assert.equal(graph.findings.length, 5);
  assert.deepEqual(graph.relations.map(edge => edge.kind), ['PROGRESS', 'CONFLICT']);
  const findings = new Map(graph.findings.map(finding => [finding.id, finding]));
  for (const { from_id, to_id } of graph.relations) {
    assert.ok(findings.has(from_id));
    assert.ok(findings.has(to_id));
  }
  const progress = graph.relations[0];
  assert.equal(
    findings.get(progress.from_id).supports[0].source.id,
    findings.get(progress.to_id).supports[0].source.id,
  );
  assert.equal(graph.relations[1].status, 'UNRESOLVED');
  assert.ok(graph.findings.every(finding => typeof finding.claim === 'string' && finding.supports[0].source.url === null));
});

test('the demo supports clarification and organization expansion', () => {
  const api = createDemoApi();
  assert.equal(api.lookup('Harbor Works renewable electricity', 'key1').commitment_id, DEMO_COMMITMENT_ID);
  const ambiguous = api.lookup('Harbor Works local hiring', 'key2');
  assert.equal(ambiguous.outcome, 'AMBIGUOUS');
  assert.deepEqual(ambiguous.suggestions.map(option => option.title), ['Local construction jobs', 'Local operations jobs']);
  assert.throws(() => api.lookup('Harbor Works construction jobs', 'key2'), { status: 409 });
  const clarified = api.lookup('Harbor Works construction jobs', 'key3');
  assert.equal(clarified.outcome, 'MATCH');
  const organizationId = api.graph(clarified.commitment_id).organization.id;
  const expansion = api.organization(organizationId);
  assert.equal(expansion.organization.id, organizationId);
  assert.ok(expansion.commitments.some(commitment => commitment.id === DEMO_COMMITMENT_ID));
  assert.ok(expansion.commitments.some(commitment => commitment.id === clarified.commitment_id));
});

test('refresh reuses finding, support and source IDs and updates the assessment', async () => {
  const api = createDemoApi();
  const original = api.graph(DEMO_COMMITMENT_ID);
  const { job_id: jobId } = api.refresh(DEMO_COMMITMENT_ID, 'refresh-key');
  assert.equal(api.refresh(DEMO_COMMITMENT_ID, 'refresh-key').job_id, jobId);
  const seen = [];
  const job = await waitForJob(api, jobId, { interval: 1, onProgress: status => seen.push(status) });
  assert.deepEqual(seen, ['RUNNING', 'RUNNING']);
  assert.equal(job.result.commitment_id, DEMO_COMMITMENT_ID);
  const refreshed = api.graph(DEMO_COMMITMENT_ID);
  assert.deepEqual(refreshed.findings, original.findings);
  assert.deepEqual(refreshed.relations, original.relations);
  assert.equal(refreshed.assessment.label, 'CONFLICTING');
  assert.notEqual(refreshed.assessment.verification_date, original.assessment.verification_date);
  assert.deepEqual(api.job(jobId), job);
});

test('failed refresh preserves the prior assessment and graph', async () => {
  const api = createDemoApi({ failRefresh: true });
  const original = api.graph(DEMO_COMMITMENT_ID);
  const { job_id: jobId } = api.refresh(DEMO_COMMITMENT_ID, 'refresh-key');
  const job = await waitForJob(api, jobId, { interval: 1 });
  assert.equal(job.status, 'FAILED');
  assert.equal(job.error.code, 'RESEARCH_UNAVAILABLE');
  assert.deepEqual(api.graph(DEMO_COMMITMENT_ID), original);
});

test('an unknown demo lookup completes without creating a commitment', async () => {
  const api = createDemoApi();
  const { job_id: jobId } = api.lookup('A new ferry pier', 'unknown-key');
  assert.equal(api.lookup('A new ferry pier', 'unknown-key').job_id, jobId);
  const job = await waitForJob(api, jobId, { interval: 1 });
  assert.equal(job.result.outcome, 'NO_PUBLIC_PROMISE');
});
