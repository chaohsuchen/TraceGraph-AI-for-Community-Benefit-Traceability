import { ApiError } from './api.mjs';

export const DEMO_COMMITMENT_ID = 'demo-harbor-electricity';

const ORGANIZATION = { id: 'demo-harbor-works', name: 'Harbor Works' };
const CONSTRUCTION_ID = 'demo-harbor-construction-jobs';
const OPERATIONS_ID = 'demo-harbor-operations-jobs';

const COMMITMENTS = [
  {
    id: DEMO_COMMITMENT_ID,
    title: 'Renewable electricity for the harbor terminal',
    description: 'Harbor Works pledged to supply at least 30% of terminal electricity from renewable sources by the end of 2026.',
    amount_value: 'At least 30% of terminal electricity',
    term_periodicity: 'By the end of 2026',
  },
  {
    id: CONSTRUCTION_ID,
    title: 'Local construction jobs',
    description: 'Harbor Works pledged to recruit local residents for terminal construction.',
  },
  {
    id: OPERATIONS_ID,
    title: 'Local operations jobs',
    description: 'Harbor Works pledged to recruit local residents for permanent terminal operations.',
  },
];

function source(id, title, publisher, publishedOn) {
  return {
    id: 'demo-source-' + id,
    title,
    url: null,
    citation: 'Synthetic demo document',
    publisher,
    source_type: null,
    published_on: publishedOn,
  };
}

const PLEDGE = source('pledge', 'Terminal energy pledge', 'Harbor Works', '2025-12-03');
const MIDYEAR = source('midyear', 'Midyear energy report', 'Harbor Works', '2026-07-15');
const QUARTERLY = source('quarterly', 'Quarterly electricity disclosure', 'Harbor Works', '2026-10-12');
const AUDIT = source('audit', 'Independent energy review', 'Port Energy Review Panel', '2026-11-02');

function finding(id, claim, document, locator, status, excerpt, explanation) {
  return {
    id: 'demo-finding-' + id,
    claim,
    supports: [{
      id: 'demo-support-' + id,
      source: document,
      status,
      explanation,
      excerpt,
      summary: null,
      locator,
      retrieved_at: '2026-11-06T15:00:00Z',
    }],
  };
}

const FINDINGS = [
  finding(
    'pledge', 'Harbor Works pledged at least 30% renewable electricity by the end of 2026.', PLEDGE, 'Energy target', 'Commitment documented',
    'At least 30% of terminal electricity will come from renewable sources by the end of 2026.',
    'This public statement establishes the target.',
  ),
  finding(
    'q1', 'Renewables supplied 18% of terminal electricity in Q1.', MIDYEAR, 'Table 1, Q1 row', 'Reported progress',
    'Renewable sources supplied 18% of terminal electricity in Q1.',
    'The midyear report gives the starting share for the comparison.',
  ),
  finding(
    'q2', 'Renewables supplied 24% of terminal electricity in Q2.', MIDYEAR, 'Table 1, Q2 row', 'Reported progress',
    'Renewable sources supplied 24% of terminal electricity in Q2.',
    'The same report shows an increase of six percentage points.',
  ),
  finding(
    'q3-disclosure', 'Harbor Works reported a 31% renewable share in Q3.', QUARTERLY, 'Quarterly energy table', 'Conflicting report',
    'The renewable share of total terminal electricity was 31% in Q3.',
    'This value conflicts with the independent review for the same quarter.',
  ),
  finding(
    'q3-review', 'The independent review calculated a 26% renewable share in Q3.', AUDIT, 'Finding 2', 'Conflicting report',
    'The renewable share of total terminal electricity was 26% in Q3.',
    'The review reports a different value for the same terminal and denominator.',
  ),
];

const RELATIONS = [
  {
    kind: 'PROGRESS',
    from_id: 'demo-finding-q1',
    to_id: 'demo-finding-q2',
    reasoning: 'The reported renewable share rose from 18% to 24% using the same denominator.',
    status: null,
    preferred_id: null,
  },
  {
    kind: 'CONFLICT',
    from_id: 'demo-finding-q3-disclosure',
    to_id: 'demo-finding-q3-review',
    reasoning: 'Two reports disagree on the renewable share for the same terminal in Q3.',
    status: 'UNRESOLVED',
    preferred_id: null,
  },
];

const ASSESSMENT = {
  label: 'CONFLICTING',
  verdict: 'Progress reported. Q3 results conflict.',
  reasoning: 'The midyear report shows improvement. The Q3 figures disagree, so the evidence leaves attainment of the year-end target unresolved.',
  verification_date: '2026-11-06',
};

function initialGraph(commitment) {
  const isMain = commitment.id === DEMO_COMMITMENT_ID;
  return {
    commitment: {
      original_text: isMain ? FINDINGS[0].supports[0].excerpt : null,
      original_promise: isMain ? {
        source: PLEDGE, locator: 'Energy target', document_id: null, char_start: null, char_end: null,
      } : null,
      amount_value: null,
      term_periodicity: null,
      geographic_scope: 'Harbor terminal',
      beneficiary: { category: null, name: null },
      responsible_entity_raw: ORGANIZATION.name,
      ...commitment,
    },
    organization: ORGANIZATION,
    assessment: isMain ? ASSESSMENT : null,
    findings: isMain ? FINDINGS : [],
    relations: isMain ? RELATIONS : [],
  };
}

function routeLookup(query) {
  const text = query.toLowerCase();
  if (/renewable|solar|electricity/.test(text)) return { outcome: 'MATCH', commitment_id: DEMO_COMMITMENT_ID };
  if (/jobs?|workforce|recruit|hiring/.test(text)) {
    if (text.includes('construction')) return { outcome: 'MATCH', commitment_id: CONSTRUCTION_ID };
    if (/operations|permanent/.test(text)) return { outcome: 'MATCH', commitment_id: OPERATIONS_ID };
    return { outcome: 'AMBIGUOUS', question: 'Do you mean construction jobs or permanent terminal jobs?' };
  }
  return null;
}

export function createDemoApi({ failRefresh = false } = {}) {
  const graphs = new Map(COMMITMENTS.map(item => [item.id, structuredClone(initialGraph(item))]));
  const jobs = new Map();
  const requests = new Map();

  function remember(scope, key, payload, create) {
    const requestKey = JSON.stringify([scope, key]);
    const existing = requests.get(requestKey);
    if (existing) {
      if (existing.payload !== payload) {
        throw new ApiError('That submission key has already been used.', { status: 409, code: 'IDEMPOTENCY_CONFLICT' });
      }
      return existing.response;
    }
    const response = create();
    requests.set(requestKey, { payload, response });
    return response;
  }

  function startJob(commitmentId) {
    const jobId = 'demo-job-' + (jobs.size + 1);
    jobs.set(jobId, { commitmentId, polls: 0 });
    return { job_id: jobId };
  }

  function graph(id) {
    const current = graphs.get(id);
    if (!current) throw new ApiError('Commitment not found.', { status: 404, code: 'NOT_FOUND' });
    return current;
  }

  function refreshAssessment(id) {
    const current = graph(id);
    graphs.set(id, {
      ...current,
      assessment: {
        ...current.assessment,
        verification_date: '2026-11-07',
        reasoning: ASSESSMENT.reasoning + ' The refresh reviewed the same source passages.',
      },
    });
    return { outcome: 'RESOLVED', commitment_id: id, run_id: null };
  }

  return {
    lookup(query, key) {
      return remember('lookup', key, query, () => routeLookup(query) ?? startJob(null));
    },
    graph,
    organization(id) {
      if (id !== ORGANIZATION.id) {
        throw new ApiError('Organization not found.', { status: 404, code: 'NOT_FOUND' });
      }
      return { organization: ORGANIZATION, commitments: COMMITMENTS };
    },
    refresh(id, key) {
      graph(id);
      return remember('refresh:' + id, key, id, () => startJob(id));
    },
    job(jobId) {
      const job = jobs.get(jobId);
      if (!job) throw new ApiError('Research job not found.', { status: 404, code: 'NOT_FOUND' });
      if (job.terminal) return job.terminal;
      if (job.polls++ < 2) return { job_id: jobId, status: 'RUNNING' };
      if (job.commitmentId && (failRefresh || !graph(job.commitmentId).assessment)) {
        job.terminal = {
          job_id: jobId,
          status: 'FAILED',
          error: {
            code: 'RESEARCH_UNAVAILABLE',
            message: failRefresh
              ? 'The simulated evidence provider is unavailable.'
              : 'The demo has no refresh evidence for this commitment.',
            retryable: failRefresh,
          },
        };
      } else {
        job.terminal = {
          job_id: jobId,
          status: 'COMPLETED',
          result: job.commitmentId ? refreshAssessment(job.commitmentId) : {
            outcome: 'NO_PUBLIC_PROMISE',
            message: 'The demo has no original public promise for that query.',
          },
        };
      }
      return job.terminal;
    },
  };
}