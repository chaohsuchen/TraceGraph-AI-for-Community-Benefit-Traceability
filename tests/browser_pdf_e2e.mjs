import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.TRACEGRAPH_PLAYWRIGHT_PACKAGE ?? 'playwright');

test('an imported PDF opens on first lookup and the next lookup reuses the completed assessment', async () => {
  const browser = await chromium.launch({ executablePath: process.env.TRACEGRAPH_CHROME, headless: true });
  try {
    const page = await browser.newPage();
    await page.goto(process.env.TRACEGRAPH_E2E_URL);
    async function lookup() {
      const response = page.waitForResponse(r => r.url().endsWith('/api/v1/lookup'));
      await page.locator('#query').fill('What about the overnight electric shuttle?');
      await page.getByRole('button', {name:'Search', exact:true}).click();
      return (await response).json();
    }
    const first = await lookup();
    assert.equal(first.outcome, 'MATCH');
    assert.ok(first.job_id);
    const source = page.getByRole('button', {name:'transit-plan.pdf', exact:true});
    await page.locator('#verdict').getByText(/^Verified /).waitFor();
    await page.getByText('Evidence and relations', {exact:true}).click();
    await source.click();
    const link = page.getByRole('link', {name:'transit-plan.pdf · page 1'});
    const pdf = await page.request.get(new URL(await link.getAttribute('href'), page.url()).href);
    assert.equal(pdf.headers()['content-type'], 'application/pdf');
    assert.equal((await pdf.body()).subarray(0,4).toString(), '%PDF');
    await page.getByRole('button', {name:'Focus branch', exact:true}).click();
    await page.getByRole('button', {name:'Back', exact:true}).click();
    assert.deepEqual(await lookup(), {outcome:'MATCH',commitment_id:first.commitment_id});
  } finally {
    await browser.close();
  }
});
