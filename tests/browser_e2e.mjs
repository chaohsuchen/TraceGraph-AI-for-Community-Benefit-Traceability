import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.TRACEGRAPH_PLAYWRIGHT_PACKAGE ?? 'playwright');
const base = process.env.TRACEGRAPH_E2E_URL;
const publicLive = process.env.TRACEGRAPH_E2E_MODE === 'public-live';
const fixtureQuery = 'What did Example Transit Authority promise about an overnight electric shuttle pilot?';
const publicQuery = "Find NASA's official Artemis program commitment to land the first woman and first person of color on the Moon.";

async function clickForGraph(page, name) {
  const requests = [];
  const record = response => {
    const pathname = new URL(response.url()).pathname;
    if (pathname.startsWith('/api/v1/') || response.status() >= 400) {
      requests.push(`${response.request().method()} ${pathname} ${response.status()}`);
    }
  };
  page.on('response', record);
  try {
    const [response] = await Promise.all([
      page.waitForResponse(next =>
        next.request().method() === 'GET' &&
        /^\/api\/v1\/commitments\/[^/]+$/.test(new URL(next.url()).pathname),
      { timeout: publicLive ? 230_000 : 15_000 }),
      page.getByRole('button', { name, exact: true }).click(),
    ]);
    assert.equal(response.status(), 200);
    return response.json();
  } catch (error) {
    const notice = await page.locator('#notice-text').textContent({ timeout: 1_000 }).catch(() => '');
    throw new Error(`No commitment graph after "${name}". Notice: ${notice || '(empty)'}. Requests: ${requests.join(', ') || '(none)'}`, { cause: error });
  } finally {
    page.off('response', record);
  }
}

async function search(page, query) {
  await page.getByRole('searchbox').fill(query);
  return clickForGraph(page, 'Search');
}

function refresh(page) {
  return clickForGraph(page, 'Research again');
}

test(publicLive ? 'browser with public-only live research and pgvector' : 'browser with PostgreSQL fixture evidence', {
  timeout: publicLive ? 510_000 : 45_000,
}, async () => {
  assert.ok(base, 'Run the test with python -m tests.e2e_runner');
  const browser = await chromium.launch({
    executablePath: process.env.TRACEGRAPH_CHROME || undefined,
    headless: true,
  });
  try {
    const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
    page.setDefaultTimeout(publicLive ? 25_000 : 7_000);
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error));
    await page.goto(base);

    const first = await search(page, publicLive ? publicQuery : fixtureQuery);
    assert.ok(first.commitment.original_promise?.source?.url);
    assert.ok(first.assessment?.label);
    await page.locator('#graph canvas').first().waitFor();
    const id = first.commitment.id;
    const inspector = page.locator('#inspector');
    await inspector.getByRole('heading', { name: first.commitment.title, exact: true }).waitFor();
    const assessment = inspector.locator('details').filter({ hasText: 'Assessment details' });
    const reasoning = assessment.locator('p').last();
    assert.equal(await reasoning.isVisible(), false);
    assert.ok(!(await page.locator('#verdict').textContent()).includes(first.assessment.verdict));
    await assessment.locator('summary').click();
    await reasoning.waitFor({ state: 'visible' });
    assert.equal(await reasoning.textContent(), first.assessment.reasoning);
    const recordToggle = inspector.getByText('Full commitment record', { exact: true });
    const recordId = inspector.getByText(id, { exact: true });
    assert.equal(await recordId.isVisible(), false);
    await recordToggle.click();
    await recordId.waitFor({ state: 'visible' });

    if (publicLive) {
      const updated = await refresh(page);
      assert.equal(updated.commitment.id, id);
      assert.ok(updated.assessment?.label);
      assert.ok(first.findings.every(prior => updated.findings.some(next => prior.id === next.id)));
    } else {
      assert.equal(first.assessment.label, 'UNVERIFIED');
      assert.equal(first.findings.length, 2);
      const updated = await refresh(page);
      assert.equal(updated.commitment.id, id);
      assert.equal(updated.assessment.label, 'PARTIALLY_CORROBORATED');
      assert.equal(updated.findings.length, 4);
      const conflict = updated.relations.find(relation => relation.kind === 'CONFLICT');
      assert.equal(conflict?.status, 'RESOLVED');
      const preferred = updated.findings.find(finding => finding.id === conflict.preferred_id);
      assert.match(preferred?.claim, /audit records two shuttles/i);
      assert.equal(preferred.supports.length, 2);

      const summary = page.locator('details > summary').filter({ hasText: 'Evidence and relations' });
      await summary.waitFor();
      if (!await summary.evaluate(element => element.parentElement.open)) await summary.click();
      await page.getByRole('button', { name: 'Conflict · resolved', exact: true }).click();
      await inspector.getByRole('heading', { name: 'Conflicting evidence', exact: true }).waitFor();
      await inspector.getByText(/vehicle register corroborates the audit/i).waitFor();
      if (process.env.TRACEGRAPH_E2E_SCREENSHOT) {
        await page.screenshot({ path: process.env.TRACEGRAPH_E2E_SCREENSHOT, fullPage: true });
      }

      const repeated = await refresh(page);
      assert.deepEqual(repeated.findings, updated.findings);
      assert.deepEqual(repeated.relations, updated.relations);
      const mobile = await browser.newPage({ viewport: { width: 390, height: 844 } });
      await mobile.goto(base);
      assert.equal((await search(mobile, fixtureQuery)).commitment.id, id);
      await mobile.locator('#graph canvas').first().waitFor();
      assert.equal(await mobile.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    }
    assert.deepEqual(pageErrors, []);
  } finally {
    await browser.close();
  }
});
