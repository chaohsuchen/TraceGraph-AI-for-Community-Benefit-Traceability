export class ApiError extends Error {
  constructor(message, { status, code, retryable = false } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.retryable = retryable;
  }
}

export function createHttpApi(base = '/api/v1', fetcher = fetch) {
  async function request(path, { signal, key, body } = {}) {
    const headers = { Accept: 'application/json' };
    if (key) headers['Idempotency-Key'] = key;
    if (body) headers['Content-Type'] = 'application/json';
    let response;
    try {
      response = await fetcher(base + path, {
        method: key ? 'POST' : 'GET',
        headers,
        body: body ? JSON.stringify(body) : undefined,
        signal,
      });
    } catch (error) {
      if (signal?.aborted) throw error;
      throw new ApiError('Could not reach TraceGraph. Try again.', { retryable: true });
    }
    const data = await response.json().catch(() => null);
    if (!response.ok || data === null) {
      throw new ApiError(data?.error?.message ?? 'TraceGraph returned an unexpected response.', {
        status: response.status,
        code: data?.error?.code,
        retryable: data?.error?.retryable ?? response.status >= 500,
      });
    }
    return data;
  }

  return {
    lookup: (query, key, signal) => request('/lookup', { key, body: { query }, signal }),
    graph: (id, runId, signal) => request(
      '/commitments/' + encodeURIComponent(id) + (runId ? '?run_id=' + encodeURIComponent(runId) : ''),
      { signal },
    ),
    organization: (id, signal) => request('/organizations/' + encodeURIComponent(id) + '/commitments', { signal }),
    refresh: (id, key, signal) => request('/commitments/' + encodeURIComponent(id) + '/research-runs', { key, signal }),
    job: (id, signal) => request('/research-jobs/' + encodeURIComponent(id), { signal }),
  };
}

function pause(ms, signal) {
  signal?.throwIfAborted();
  return new Promise((resolve, reject) => {
    function finish() {
      signal?.removeEventListener('abort', cancel);
      resolve();
    }
    function cancel() {
      clearTimeout(timer);
      reject(signal.reason);
    }
    const timer = setTimeout(finish, ms);
    signal?.addEventListener('abort', cancel, { once: true });
  });
}

export async function waitForJob(api, jobId, { signal, onProgress, interval = 1000 } = {}) {
  while (true) {
    signal?.throwIfAborted();
    const job = await api.job(jobId, signal);
    if (job.status === 'COMPLETED' || job.status === 'FAILED') return job;
    if (job.status !== 'QUEUED' && job.status !== 'RUNNING') {
      throw new ApiError('TraceGraph returned an unexpected research status.');
    }
    onProgress?.(job.status);
    await pause(interval, signal);
  }
}
