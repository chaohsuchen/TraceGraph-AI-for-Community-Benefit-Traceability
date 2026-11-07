import { ApiError } from './api.mjs';

export function createDemoApi(fixtures, { failRefresh = false } = {}) {
  const sample = Object.fromEntries(fixtures.map(item => [item.name, item.response.body]));
  const funding = sample.graph_h008_latest_after_failed_refresh;
  const operations = sample.graph_h001_without_completed_run;
  const energy = sample.graph_h010_same_source_different_evidence;
  const broad = sample.expand_ballys.commitments.find(item => item.id === 'TG-GOLD-H009');
  const jobsGraph = {
    ...operations,
    commitment: {
      ...operations.commitment,
      ...broad,
      original_text: null,
      original_promise: null,
      amount_value: '70 percent of construction and facility jobs',
      term_periodicity: 'construction and operations',
      beneficiary: { category: 'local residents / workforce', name: null },
    },
  };
  const latest = new Map([funding, operations, jobsGraph, energy].map(graph => [graph.commitment.id, graph]));
  const histories = new Map();
  const jobs = new Map();
  const requests = new Map();

  function start(key, id, payload) {
    const existing = requests.get(key);
    if (existing) {
      if (existing.payload !== payload) throw new ApiError('That submission key has already been used.', { status: 409 });
      return { job_id: existing.jobId };
    }
    const jobId = crypto.randomUUID();
    jobs.set(jobId, { id, polls: 0 });
    requests.set(key, { jobId, payload });
    return { job_id: jobId };
  }

  function lookup(query, key) {
    const text = query.toLowerCase();
    if (/renewable|solar|electricity|nyserda/.test(text)) {
      return { outcome: 'MATCH', commitment_id: energy.commitment.id };
    }
    if (/substation|public safety|nypd/.test(text)) return sample.lookup_known_h008;
    if (/bronx/.test(text) && /jobs?|workforce|hiring|70\s*%/.test(text)) {
      if (text.includes('construction')) return { outcome: 'MATCH', commitment_id: broad.id };
      if (text.includes('permanent')) return { outcome: 'MATCH', commitment_id: operations.commitment.id };
      return sample.lookup_bronx_jobs_ambiguous;
    }
    return start('lookup:' + key, null, query);
  }

  function graph(id, runId) {
    const current = latest.get(id);
    if (!current) throw new ApiError('Commitment not found.', { status: 404 });
    if (!runId || runId === current.viewed_run_id) return current;
    const previous = histories.get(id)?.get(runId);
    if (!previous) throw new ApiError('Research run not found.', { status: 404 });
    return { ...current, viewed_run_id: runId, source_edges: previous.source_edges };
  }

  function finishRefresh(id, jobId) {
    const current = graph(id);
    const run = {
      id: 'demo-run-' + jobId,
      verification_date: '2026-11-07',
      verdict: current.latest_completed_run?.verdict ?? 'Delivery remains unverified.',
      reasoning: 'Simulated refresh using the TraceGraph contract fixtures.',
    };
    const previous = current.latest_completed_run;
    if (previous) {
      if (!histories.has(id)) histories.set(id, new Map());
      histories.get(id).set(previous.id, current);
    }
    const sourceEdges = current.source_edges.map(edge => ({
      ...edge,
      evidence: edge.evidence.map(item => ({ ...item, id: item.id + '-' + jobId })),
    }));
    latest.set(id, {
      ...current,
      latest_completed_run: run,
      earlier_completed_runs: previous ? [previous, ...current.earlier_completed_runs] : [],
      viewed_run_id: run.id,
      source_edges: sourceEdges,
    });
    return { outcome: 'RESOLVED', commitment_id: id, run_id: run.id };
  }

  return {
    lookup,
    graph,
    organization(id) {
      if (id !== sample.expand_ballys.organization.id) {
        throw new ApiError('Organization not found.', { status: 404 });
      }
      return sample.expand_ballys;
    },
    refresh(id, key) {
      graph(id);
      return start('refresh:' + id + ':' + key, id, id);
    },
    job(jobId) {
      const job = jobs.get(jobId);
      if (!job) throw new ApiError('Research job not found.', { status: 404 });
      if (job.terminal) return job.terminal;
      if (job.polls++ < 2) return { job_id: jobId, status: 'RUNNING' };
      if (job.id && failRefresh) {
        job.terminal = { ...sample.poll_refresh_failed, job_id: jobId };
      } else {
        job.terminal = {
          job_id: jobId,
          status: 'COMPLETED',
          result: job.id ? finishRefresh(job.id, jobId) : sample.poll_discovery_no_documented_promise.result,
        };
      }
      return job.terminal;
    },
  };
}
