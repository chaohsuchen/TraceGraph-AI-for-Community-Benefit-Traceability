import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { test } from 'node:test';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.TRACEGRAPH_PLAYWRIGHT_PACKAGE ?? 'playwright');

test('a partial job shows a question graph while keeping evidence, subsequent research, and quit available', {
  timeout: 40_000,
}, async () => {
  const server = spawn(process.execPath, ['frontend/dev.mjs'], { env: { ...process.env, PORT: '0' },
    stdio: ['ignore', 'pipe', 'inherit'], windowsHide: true });
  let browser;
  try {
    const [banner] = await once(server.stdout, 'data');
    const port = /127\.0\.0\.1:(\d+)/.exec(banner.toString())[1];
    browser = await chromium.launch({ executablePath: process.env.TRACEGRAPH_CHROME || undefined, headless: true });
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const frozen = process.env.TRACEGRAPH_PARTIAL_CASE
      ? JSON.parse(await readFile(process.env.TRACEGRAPH_PARTIAL_CASE, 'utf8')) : null;
    if (frozen) assert.equal(frozen.outcome, 'PARTIAL_RESEARCH');
    const query = frozen?.query ?? 'Did Acorn commit to exceed 10 percent?';
    const facts = frozen?.job_result.facts ?? [{ source_title: 'Original filing',
      source_url: 'https://example.org/filing', excerpt: 'Acorn plans to meet a minimum of 10 percent.' }];
    const { excerpt, source_url: url } = facts[0];
    const queries = [];
    const unexpected = [];
    await page.route('**/api/v1/**', async route => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/api/v1/lookup') {
        queries.push(route.request().postDataJSON().query);
        return route.fulfill({ json: { outcome: 'RESEARCH_STARTED', job_id: 'job-' + queries.length } });
      }
      if (path.startsWith('/api/v1/research-jobs/')) {
        const hasFact = path.endsWith('job-1');
        if (hasFact && frozen) return route.fulfill({ json: { status: 'COMPLETED', result: frozen.job_result } });
        return route.fulfill({ json: { status: 'COMPLETED', result: { outcome: 'PARTIAL_RESEARCH',
          message: 'Research is incomplete; no commitment was saved.',
          facts: hasFact ? facts : [],
          unmatched_records: [{ source_title: 'Other program', source_url: url, excerpt: 'Different undertaking.' }],
          gaps: ['Whether the target exceeds 10 percent.'],
          suggestions: [{ kind: 'WEB_LEAD', title: 'Original filing', source_url: url }] } } });
      }
      unexpected.push(path);
      return route.fulfill({ status: 404, json: {} });
    });
    await page.goto('http://127.0.0.1:' + port);
    await page.getByRole('searchbox').fill(query);
    await page.getByRole('button', { name: 'Search', exact: true }).click();
    await page.getByRole('heading', { name: 'Research evidence graph' }).waitFor();
    const canvas = page.locator('#graph .evidence-canvas');
    const nodes = () => canvas.evaluate(el => el._cyreg.cy.nodes().map(n => n.id()).sort());
    const graphNodes = [...facts.map((_, i) => 'finding:excerpt-' + (i + 1)), 'question:job-1'].sort();
    assert.deepEqual(await nodes(), graphNodes);
    assert.equal(await page.locator('#refresh').isVisible(), false);
    assert.equal(await page.locator('#verdict').textContent(), 'Partial researchDelivery verdict unavailable.');
    await page.locator('#graph summary').click();
    await page.locator('[data-graph-item="finding:excerpt-1"]').focus();
    await page.keyboard.press('Enter');
    const inspector = page.locator('#inspector');
    assert.equal(await inspector.locator('blockquote').textContent(), excerpt);
    assert.equal(await inspector.getByRole('link', { name: 'Open source' }).getAttribute('href'), url);
    await inspector.getByRole('button', { name: 'Focus branch' }).click();
    assert.deepEqual(await nodes(), ['finding:excerpt-1']);
    await page.getByRole('button', { name: 'Back', exact: true }).click();
    assert.deepEqual(await nodes(), graphNodes);
    await page.getByRole('button', { name: 'Continue research', exact: true }).click();
    await page.locator('#research-empty').waitFor();
    assert.equal(queries[1], query);
    await page.locator('#conversation-input').fill('Please check the follow-up filing.');
    await page.locator('#conversation-submit').click();
    await page.locator('#research-empty').waitFor();
    assert.equal(await page.locator('#workspace').isVisible(), false);
    assert.equal(await page.locator('#view-research-graph').isVisible(), false);
    assert.equal(await page.getByRole('button', { name: /Research this lead/ }).count(), 1);
    await page.waitForFunction(() => !document.querySelector('#conversation-submit').disabled);
    assert.ok(queries[2].includes(query) && queries[2].includes('follow-up filing'));
    await page.getByRole('button', { name: 'Quit', exact: true }).click();
    assert.equal(await page.locator('#conversation').isVisible(), false);
    assert.deepEqual(unexpected, []);
    assert.deepEqual(errors, []);
  } finally {
    await browser?.close();
    server.kill();
  }
});
