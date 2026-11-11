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

async function openEvidencePicker(page) {
  const picker = page.locator('#graph .evidence-picker');
  if (!await picker.evaluate(details => details.open)) await picker.locator('summary').click();
  return picker;
}

async function graphFrame(canvas) {
  return canvas.evaluate(element => {
    // Cytoscape renders onto a canvas. Read its geometry to check which nodes
    // are drawn and send real pointer events to their rendered coordinates.
    const graph = element._cyreg.cy;
    return {
      zoom: graph.zoom(),
      pan: graph.pan(),
      nodes: Object.fromEntries(graph.nodes().filter(node => node.visible())
        .map(node => [node.id(), node.renderedPosition()])),
      conflicts: graph.edges('.conflict').filter(edge => edge.visible()).length,
    };
  });
}

function visibleFindings(frame) {
  return Object.keys(frame.nodes).filter(id => id.startsWith('finding:'))
    .map(id => id.slice('finding:'.length)).sort();
}

async function exerciseFocusView(page, graph, conflict, preferred) {
  const inspector = page.locator('#inspector');
  const canvas = page.locator('#graph .evidence-canvas');
  const back = page.getByRole('button', { name: 'Back', exact: true });
  const focus = inspector.getByRole('button', { name: 'Focus branch', exact: true });
  const count = page.getByTestId('evidence-count');
  const budgetLink = graph.relations.find(edge => edge.kind === 'PROGRESS' && edge.to_id === preferred.id);
  const budget = graph.findings.find(finding => finding.id === budgetLink?.from_id);
  assert.ok(budget);

  await page.getByRole('button', { name: 'Zoom out', exact: true }).click();
  await canvas.scrollIntoViewIfNeeded();
  const overview = await graphFrame(canvas);
  const position = overview.nodes['finding:' + budget.id];
  const bounds = await canvas.boundingBox();
  assert.ok(position && bounds);
  await page.mouse.dblclick(bounds.x + position.x, bounds.y + position.y);
  await back.waitFor({ state: 'visible' });
  await inspector.getByRole('heading', { name: budget.claim, exact: true }).waitFor();
  const branch = await graphFrame(canvas);
  assert.deepEqual(visibleFindings(branch), [budget.id, conflict.from_id, conflict.to_id].sort());
  assert.equal(Object.keys(branch.nodes).length, 3);
  assert.ok(branch.nodes['finding:' + budget.id].y < branch.nodes['finding:' + preferred.id].y);
  assert.equal(branch.conflicts, 1);
  assert.match(await count.textContent(), /^3 findings/);

  const picker = await openEvidencePicker(page);
  assert.equal(await picker.getByRole('button', { name: 'Current commitment', exact: true }).count(), 0);
  await picker.getByRole('button', { name: preferred.claim, exact: true }).click();
  await focus.press('Enter');
  assert.deepEqual(visibleFindings(await graphFrame(canvas)), [preferred.id]);
  assert.match(await count.textContent(), /^1 finding\b/);
  const fullGraph = inspector.getByRole('button', { name: 'View in full graph', exact: true });
  await fullGraph.waitFor({ state: 'visible' });

  const repeated = await refresh(page);
  await page.getByText('Evidence graph updated.', { exact: true }).waitFor();
  assert.deepEqual(repeated.findings, graph.findings);
  assert.deepEqual(repeated.relations, graph.relations);
  assert.deepEqual(visibleFindings(await graphFrame(canvas)), [preferred.id]);

  await back.click();
  assert.deepEqual(await graphFrame(canvas), branch);
  await back.click();
  assert.deepEqual(await graphFrame(canvas), overview);
  assert.equal(await back.isVisible(), false);

  // A single canvas click still inspects a node without entering its branch.
  await canvas.click({ position: overview.nodes['finding:' + preferred.id] });
  await inspector.getByRole('heading', { name: preferred.claim, exact: true }).waitFor();
  assert.equal(await back.isVisible(), false);
  await focus.click();
  await fullGraph.click();
  await inspector.getByRole('heading', { name: 'Conflicting evidence', exact: true }).waitFor();
  assert.deepEqual(await graphFrame(canvas), overview);

  const companyId = 'organization:' + graph.organization.id;
  const commitmentId = 'commitment:' + graph.commitment.id;
  await picker.getByRole('button', { name: graph.organization.name, exact: true }).click();
  await focus.click();
  const company = await graphFrame(canvas);
  assert.ok(company.nodes[companyId].y < company.nodes[commitmentId].y);
  await inspector.getByRole('button', { name: 'Expand commitments', exact: true }).click();
  await inspector.getByRole('button', { name: graph.commitment.title, exact: true }).waitFor();
  await picker.getByRole('button', { name: 'Current commitment', exact: true }).click();
  await focus.click();
  const commitment = await graphFrame(canvas);
  assert.equal(commitment.nodes[companyId], undefined);
  assert.deepEqual(visibleFindings(commitment), visibleFindings(overview));
  await back.click();
  assert.deepEqual(await graphFrame(canvas), company);
  await back.click();
  assert.deepEqual(await graphFrame(canvas), overview);
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
      await page.getByText('Evidence graph updated.', { exact: true }).waitFor();
      const conflict = updated.relations.find(relation => relation.kind === 'CONFLICT');
      assert.equal(conflict?.status, 'RESOLVED');
      const preferred = updated.findings.find(finding => finding.id === conflict.preferred_id);
      assert.match(preferred?.claim, /audit records two shuttles/i);
      assert.equal(preferred.supports.length, 2);

      await exerciseFocusView(page, updated, conflict, preferred);
      await openEvidencePicker(page);
      await page.getByRole('button', { name: 'Conflict · resolved', exact: true }).click();
      await inspector.getByRole('heading', { name: 'Conflicting evidence', exact: true }).waitFor();
      await inspector.getByText(/vehicle register corroborates the audit/i).waitFor();
      if (process.env.TRACEGRAPH_E2E_SCREENSHOT) {
        await page.screenshot({ path: process.env.TRACEGRAPH_E2E_SCREENSHOT, fullPage: true });
      }

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

test('uncertain lookup and research share a continuing search conversation', { skip: publicLive, timeout: 45_000 }, async () => {
  const browser = await chromium.launch({ executablePath: process.env.TRACEGRAPH_CHROME || undefined, headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    page.setDefaultTimeout(7_000);
    const errors = [];
    page.on('pageerror', error => errors.push(error));
    await page.goto(base);
    const first = await search(page, fixtureQuery);
    const panel = page.getByRole('region', { name: 'Commitment search' });
    const log = panel.getByRole('log');
    const related = panel.getByRole('list', { name: 'Related options' });
    const hint = panel.getByRole('textbox', { name: 'Add a detail or source' });
    const saved = 'Use this commitment ' + first.commitment.title;
    const lead = related.getByRole('button', { name: /^Research this lead/ });

    await page.getByRole('searchbox').fill('Which night transit service did Example Transit Authority promise?');
    await page.getByRole('button', { name: 'Search', exact: true }).click();
    await log.getByText('Do you mean the overnight electric shuttle pilot?', { exact: true }).waitFor();
    await related.getByRole('button', { name: saved, exact: true }).waitFor();
    assert.equal((await clickForGraph(page, saved)).commitment.id, first.commitment.id);
    await log.getByText(`Opened ${first.commitment.title}.`, { exact: true }).waitFor();

    const weekend = 'What did Example Transit Authority promise about weekend transit?';
    await page.getByRole('searchbox').fill(weekend);
    await page.getByRole('button', { name: 'Search', exact: true }).click();
    await lead.waitFor();
    assert.equal(await related.getByRole('button', { name: saved, exact: true }).count(), 1);
    assert.equal(await related.getByRole('link', { name: 'Open source' }).getAttribute('href'),
      'https://example.org/tracegraph-fixture/promise');

    const detail = 'I am looking for the 2028 pilot.';
    await hint.fill(detail);
    const sent = page.waitForRequest(request => request.method() === 'POST' && request.url().endsWith('/api/v1/lookup'));
    await panel.getByRole('button', { name: 'Search again', exact: true }).click();
    const { query } = (await sent).postDataJSON();
    assert.ok(query.startsWith(weekend) && query.includes(detail));
    await lead.waitFor();
    assert.equal(await log.getByText(detail, { exact: true }).count(), 1);
    assert.equal(await log.getByText('No publicly documented original promise could be located.', { exact: true }).count(), 2);
    const leadName = await lead.getAttribute('aria-label');
    assert.equal((await clickForGraph(page, leadName)).commitment.id, first.commitment.id);
    await log.getByText(`Opened ${first.commitment.title}.`, { exact: true }).last().waitFor();
    assert.equal(await panel.isVisible(), true);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);

    await panel.getByRole('button', { name: 'Quit', exact: true }).click();
    await panel.waitFor({ state: 'hidden' });
    assert.equal(await page.locator('#workspace').isVisible(), true);

    await page.goto(base + '/?demo=1');
    await page.getByRole('searchbox').fill('Harbor Works local hiring');
    await page.getByRole('button', { name: 'Search', exact: true }).click();
    await related.getByRole('button', { name: 'Use this commitment Local operations jobs', exact: true }).waitFor();
    assert.equal(await related.getByRole('button', { name: /^Use this commitment/ }).count(), 2);
    await related.getByRole('button', { name: 'Use this commitment Local operations jobs', exact: true }).click();
    await log.getByText('Opened Local operations jobs.', { exact: true }).waitFor();
    assert.equal(await panel.isVisible(), true);
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
});
