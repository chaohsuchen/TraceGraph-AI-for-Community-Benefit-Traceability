import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.TRACEGRAPH_PLAYWRIGHT_PACKAGE ?? 'playwright');
const base = process.env.TRACEGRAPH_E2E_URL;
const live = process.env.TRACEGRAPH_E2E_MODE === 'live';

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
