import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const assets = new Map([
  ['/', ['./index.html', 'text/html; charset=utf-8']],
  ['/styles.css', ['./styles.css', 'text/css; charset=utf-8']],
  ['/app.mjs', ['./app.mjs', 'text/javascript; charset=utf-8']],
  ['/api.mjs', ['./api.mjs', 'text/javascript; charset=utf-8']],
  ['/demo.mjs', ['./demo.mjs', 'text/javascript; charset=utf-8']],
  ['/contracts/examples.json', ['../contracts/examples.json', 'application/json; charset=utf-8']],
]);
const backend = process.env.TRACEGRAPH_BACKEND;
const port = Number(process.env.PORT ?? 4173);

function json(response, status, code, message, retryable = false) {
  response.writeHead(status, { 'Content-Type': 'application/json' });
  response.end(JSON.stringify({ error: { code, message, retryable } }));
}

async function proxy(request, response, url) {
  if (!backend) {
    json(response, 503, 'RESEARCH_UNAVAILABLE', 'Backend is not configured. Use the fixture demo for local testing.');
    return;
  }
  const headers = { Accept: 'application/json' };
  for (const name of ['content-type', 'idempotency-key']) {
    if (request.headers[name]) headers[name] = request.headers[name];
  }
  let body;
  if (request.method === 'POST') {
    const chunks = [];
    let bytes = 0;
    for await (const chunk of request) {
      bytes += chunk.length;
      if (bytes > 65_536) {
        json(response, 413, 'INVALID_REQUEST', 'Request body is too large.');
        return;
      }
      chunks.push(chunk);
    }
    if (bytes) body = Buffer.concat(chunks);
  }
  const upstream = await fetch(new URL(url.pathname + url.search, backend), {
    method: request.method,
    headers,
    body,
    signal: AbortSignal.timeout(30_000),
  });
  const forwarded = {};
  for (const name of ['content-type', 'location']) {
    if (upstream.headers.has(name)) forwarded[name] = upstream.headers.get(name);
  }
  const data = Buffer.from(await upstream.arrayBuffer());
  response.writeHead(upstream.status, forwarded);
  response.end(data);
}

const server = createServer(async (request, response) => {
  const url = new URL(request.url, 'http://localhost');
  try {
    if (url.pathname.startsWith('/api/v1/')) {
      await proxy(request, response, url);
      return;
    }
    const asset = assets.get(url.pathname);
    if (!asset || request.method !== 'GET') {
      json(response, 404, 'NOT_FOUND', 'Resource not found.');
      return;
    }
    const [path, contentType] = asset;
    const body = await readFile(fileURLToPath(new URL(path, import.meta.url)));
    response.writeHead(200, { 'Content-Type': contentType, 'Cache-Control': 'no-store' });
    response.end(body);
  } catch {
    json(response, 503, 'RESEARCH_UNAVAILABLE', 'TraceGraph could not load this resource.', true);
  }
});
server.listen(port, '127.0.0.1', () => {
  console.log('TraceGraph fixture demo at http://127.0.0.1:' + server.address().port + '/?demo=1');
});
