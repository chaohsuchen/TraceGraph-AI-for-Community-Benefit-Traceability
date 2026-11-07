import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.TRACEGRAPH_PLAYWRIGHT_PACKAGE ?? 'playwright');
const base = process.env.TRACEGRAPH_E2E_URL;
const publicLive = process.env.TRACEGRAPH_E2E_MODE === 'public-live';
const live = publicLive || process.env.TRACEGRAPH_E2E_MODE === 'live';

async function publicDiscovery(page) {
  const query = "Find NASA's official Artemis program commitment to land the first woman and first person of color on the Moon.";
  await page.getByRole('searchbox').fill(query);
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await page.waitForFunction(() =>
    !document.querySelector('#workspace').hidden ||
    document.querySelector('#notice').dataset.kind === 'error' ||
    /^No publicly documented/.test(document.querySelector('#notice-text').textContent),
  null, { timeout: 230_000 });
  assert.equal(await page.locator('#workspace').isVisible(), true, await page.locator('#notice-text').innerText());

  const id = await page.locator('.commitment-node .node-hint').innerText();
  const graphPath = base + '/api/v1/commitments/' + encodeURIComponent(id);
  const first = await page.request.get(graphPath).then(response => response.json());
  assert.ok(first.commitment.original_promise?.source?.url);
  assert.ok(first.latest_completed_run?.id);
  await page.locator('.source-edge .node').first().click();
  await page.getByRole('link', { name: 'Open source', exact: true }).first().waitFor();

  const lookup = page.waitForResponse(response => response.url().endsWith('/api/v1/lookup') &&
    response.request().method() === 'POST');
  await page.getByRole('searchbox').fill(first.commitment.description);
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  const matched = await lookup;
  assert.equal(matched.status(), 200);
  assert.deepEqual(await matched.json(), { outcome: 'MATCH', commitment_id: id });
  await page.getByRole('button', { name: 'Research again', exact: true }).click();
  await page.waitForFunction(() =>
    document.querySelector('#notice').dataset.kind === 'error' ||
    /^Research updated/.test(document.querySelector('#notice-text').textContent),
  null, { timeout: 230_000 });
  assert.match(await page.locator('#notice-text').innerText(), /^Research updated/);

  const refreshed = await page.request.get(graphPath).then(response => response.json());
  assert.equal(refreshed.commitment.id, id);
  assert.notEqual(refreshed.latest_completed_run.id, first.latest_completed_run.id);
  assert.ok(refreshed.earlier_completed_runs.some(run => run.id === first.latest_completed_run.id));
  assert.ok(refreshed.source_edges.length);
  await page.getByLabel('View run').selectOption(first.latest_completed_run.id);
  await page.locator('.source-edge .node').first().waitFor();
}

test(live ? 'browser with live Claude research and pgvector' : 'browser with PostgreSQL and fixture research', {
  timeout: live ? 510_000 : 35_000,
}, async () => {
  assert.ok(base, 'Run the test with python -m tests.e2e_runner');
  const browser = await chromium.launch({
    executablePath: process.env.TRACEGRAPH_CHROME || undefined,
    headless: true,
  });
  try {
    const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
    page.setDefaultTimeout(live ? 25_000 : 7_000);
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error));
    const button = name => page.getByRole('button', { name, exact: typeof name === 'string' });
    const value = text => page.getByText(text, { exact: typeof text === 'string' }).first();
    await page.goto(base);
    if (publicLive) {
      await publicDiscovery(page);
      assert.deepEqual(pageErrors, []);
      return;
    }
    await button('Public safety funding').click();
    await value('TG-GOLD-H008').waitFor();
    await button(/GFLB Selection Report/).click();
    await page.locator('#inspector').getByText(/p\.\s*13/).first().waitFor();
    await button(/Expand commitments/).click();
    await button(/70% of construction/).waitFor();
    await button('Research again').click();
    if (live) {
      await page.waitForFunction(
        () => !document.querySelector('#notice').hidden &&
          (/^Research updated/.test(document.querySelector('#notice-text').textContent) ||
           document.querySelector('#notice').dataset.kind === 'error'),
        null,
        { timeout: 450_000 },
      );
      assert.match(await page.locator('#notice-text').innerText(), /^Research updated/);
      assert.ok(await page.locator('.source-edge').count(), 'Completed research has cited evidence');
    } else {
      await value(/Research updated/).waitFor();
    }
    assert.ok(await page.locator('.commitment-node').innerText().then(text => text.includes('TG-GOLD-H008')));
    const historical = page.getByLabel('View run');
    await historical.selectOption({ index: 1 });
    await page.locator('#inspector').getByText(/Commitment confirmed|Delivery unverified/).first().waitFor();

    if (!live) {
      await button('Bronx hiring').click();
      await value(/Do you mean permanent operations or construction/).waitFor();
      await button('Renewable electricity').click();
      await value('TG-GOLD-H010').waitFor();
      await value('Target documented').waitFor();
      const prior = await page.locator('#verdict .verdict').innerText();
      await button('Research again').click();
      await value('The research could not be completed.').waitFor();
      assert.equal(await page.locator('#verdict .verdict').innerText(), prior);
      await page.getByRole('searchbox').fill('Did Example Transit Authority promise a 2028 overnight electric shuttle pilot?');
      await button('Search').click();
      await page.locator('.commitment-node').getByText('Overnight electric shuttle pilot', { exact: true }).waitFor();
      const mobile = await browser.newPage({ viewport: { width: 390, height: 844 } });
      await mobile.goto(base);
      await mobile.getByRole('button', { name: 'Public safety funding', exact: true }).click();
      await mobile.getByText('TG-GOLD-H008', { exact: true }).waitFor();
      assert.equal(await mobile.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    }
    assert.deepEqual(pageErrors, []);
  } finally {
    await browser.close();
  }
});
