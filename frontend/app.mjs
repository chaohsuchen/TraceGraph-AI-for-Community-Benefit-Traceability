import { createHttpApi, waitForJob } from './api.mjs';
import { createDemoApi, DEMO_COMMITMENT_ID } from './demo.mjs';
import { createEvidenceGraph, findingNodeId, relationEdgeId } from './evidence-graph.mjs';

const $ = id => document.getElementById(id);
const demo = new URLSearchParams(location.search);
const state = { graph: null, organization: null, organizationExpanded: false, selected: null, request: null };
let api;
let evidenceView;

function element(tag, content, className) {
  const node = document.createElement(tag);
  if (content) node.textContent = content;
  if (className) node.className = className;
  return node;
}

function button(label, click, className = 'quiet') {
  const node = element('button', label, className);
  node.type = 'button';
  node.addEventListener('click', click);
  return node;
}

function notice(message = '', kind = 'info', retry) {
  $('notice').hidden = !message;
  $('notice').dataset.kind = kind;
  $('notice-text').textContent = message;
  $('retry').hidden = !retry;
  $('retry').onclick = retry ?? null;
}

function updateBusy() {
  $('refresh').disabled = state.request !== null;
  $('workspace').setAttribute('aria-busy', String(state.request !== null));
}

async function action(task, message, retryAction) {
  state.request?.abort();
  const request = new AbortController();
  state.request = request;
  notice(message);
  updateBusy();
  try {
    await task(request.signal);
  } catch (error) {
    if (!request.signal.aborted) notice(error.message, 'error', error.retryable ? retryAction : undefined);
  } finally {
    if (state.request === request) {
      state.request = null;
      updateBusy();
    }
  }
}

async function loadGraph(id, signal) {
  const next = await api.graph(id, null, signal);
  signal?.throwIfAborted();
  if (state.graph?.organization?.id !== next.organization?.id) {
    state.organization = null;
    state.organizationExpanded = false;
  }
  if (state.graph?.commitment.id !== id) state.selected = null;
  state.graph = next;
  render();
}

async function followJob(id, signal, isRefresh, retry) {
  const job = await waitForJob(api, id, {
    signal,
    interval: demo.has('demo') ? 450 : 1000,
    onProgress: status => notice(status === 'QUEUED' ? 'Research queued…' : 'Checking sources…'),
  });
  signal.throwIfAborted();
  if (job.status === 'FAILED') {
    notice(job.error.message, 'error', job.error.retryable ? retry : undefined);
    return;
  }
  if (job.result.outcome === 'RESOLVED') {
    await loadGraph(job.result.commitment_id, signal);
    notice(isRefresh ? 'Evidence graph updated.' : '');
  } else if (job.result.outcome === 'AMBIGUOUS') {
    notice(job.result.question, 'clarify');
    $('query').focus();
  } else {
    notice(job.result.message);
  }
}

function lookup(query, key = crypto.randomUUID()) {
  return action(async signal => {
    const result = await api.lookup(query, key, signal);
    signal.throwIfAborted();
    if (result.outcome === 'MATCH') {
      await loadGraph(result.commitment_id, signal);
      notice();
    } else if (result.outcome === 'AMBIGUOUS') {
      notice(result.question, 'clarify');
      $('query').focus();
    } else if (result.job_id) {
      await followJob(result.job_id, signal, false, () => lookup(query));
    } else {
      throw new Error('TraceGraph returned an unexpected lookup result.');
    }
  }, 'Finding the commitment…', () => lookup(query, key));
}

function refresh(id, key = crypto.randomUUID()) {
  return action(async signal => {
    const accepted = await api.refresh(id, key, signal);
    signal.throwIfAborted();
    await followJob(accepted.job_id, signal, true, () => refresh(id));
  }, 'Checking for new evidence…', () => refresh(id, key));
}

function openGraph(id) {
  return action(async signal => {
    await loadGraph(id, signal);
    notice();
  }, 'Loading the evidence graph…', () => openGraph(id));
}

function toggleOrganization() {
  if (state.organizationExpanded) {
    state.organizationExpanded = false;
    render();
    return;
  }
  const id = state.graph.organization.id;
  return action(async signal => {
    state.organization ??= await api.organization(id, signal);
    signal.throwIfAborted();
    state.organizationExpanded = true;
    render();
    notice();
  }, 'Loading commitments for this organization…', toggleOrganization);
}

function renderGraph(graph) {
  const id = graph.commitment.id;
  if (evidenceView?.id !== id) {
    evidenceView?.renderer.destroy();
    const canvas = element('div', '', 'evidence-canvas');
    canvas.setAttribute('role', 'img');
    canvas.setAttribute('aria-label', 'Interactive evidence graph. Evidence and relations can also be selected below.');
    const controls = element('div', '', 'evidence-controls');
    const count = element('span', '', 'evidence-count');
    count.dataset.testid = 'evidence-count';
    const zoomIn = button('+', () => evidenceView.renderer.zoom(1.25));
    const zoomOut = button('−', () => evidenceView.renderer.zoom(0.8));
    zoomIn.setAttribute('aria-label', 'Zoom in');
    zoomOut.setAttribute('aria-label', 'Zoom out');
    controls.append(count, zoomOut, zoomIn, button('Fit view', () => evidenceView.renderer.fit()));
    const details = element('details', '', 'evidence-picker');
    const items = element('ul', '', 'evidence-list');
    details.append(element('summary', 'Evidence and relations'), items);
    const legend = element('div', '', 'graph-legend');
    legend.append(element('span', '— Related'), element('span', '→ Progress', 'legend-progress'),
      element('span', '┄ Conflict', 'legend-conflict'));
    $('graph').replaceChildren(controls, canvas, legend, details);
    evidenceView = { id, renderer: createEvidenceGraph(canvas, selectGraphItem), items, count };
  }

  evidenceView.renderer.update(graph);
  const findings = graph.findings.length;
  const conflicts = graph.relations.filter(edge => edge.kind === 'CONFLICT');
  evidenceView.count.textContent = findings + (findings === 1 ? ' finding' : ' findings') +
    (conflicts.length ? ' · ' + conflicts.length + (conflicts.length === 1 ? ' conflict' : ' conflicts') : '');
  const options = [
    { id: 'commitment:' + id, kind: 'commitment', label: 'Current commitment' },
    ...(graph.organization ? [{ id: 'organization:' + graph.organization.id, kind: 'organization',
      label: graph.organization.name }] : []),
    ...graph.findings.map(finding => ({
      id: findingNodeId(finding.id), kind: 'finding', key: finding.id, label: finding.claim,
    })),
    ...graph.relations.map(edge => ({
      id: relationEdgeId(edge), kind: 'relation', key: relationEdgeId(edge),
      label: edge.kind === 'CONFLICT' ? 'Conflict · ' + (edge.status === 'RESOLVED' ? 'resolved' : 'unresolved') : 'Progress relation',
    })),
  ];
  evidenceView.items.replaceChildren(...options.map(option => {
    const row = element('li');
    const control = button(option.label, () => selectGraphItem(option));
    control.dataset.graphItem = option.id;
    row.append(control);
    return row;
  }));
  focusGraphItem();
}

function focusGraphItem() {
  const id = state.selected?.id ?? 'commitment:' + state.graph.commitment.id;
  evidenceView.renderer.focus(id);
  for (const control of evidenceView.items.querySelectorAll('button')) {
    control.setAttribute('aria-pressed', String(control.dataset.graphItem === id));
  }
}

function selectGraphItem(item) {
  state.selected = item;
  focusGraphItem();
  renderInspector(state.graph);
}

function sourceLink(source) {
  if (!source?.url) return null;
  try {
    const url = new URL(source.url);
    if (url.protocol !== 'https:' && url.protocol !== 'http:') return null;
    const link = element('a', 'Open source');
    link.href = url.href;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    return link;
  } catch {
    return null;
  }
}

function renderInspector(graph) {
  const aside = $('inspector');
  const selected = state.selected;
  if (selected?.kind === 'organization' && graph.organization) {
    aside.replaceChildren(element('p', 'Organization', 'eyebrow'), element('h3', graph.organization.name),
      button(state.organizationExpanded ? 'Collapse commitments' : 'Expand commitments', toggleOrganization));
    if (state.organizationExpanded) {
      const siblings = element('ul', '', 'commitment-list');
      for (const item of state.organization.commitments) {
        const row = element('li');
        const link = button(item.title ?? item.description, () => openGraph(item.id));
        if (item.id === graph.commitment.id) link.setAttribute('aria-current', 'true');
        row.append(link);
        siblings.append(row);
      }
      aside.append(siblings);
    }
    return;
  }
  if (selected?.kind === 'finding') {
    const finding = graph.findings.find(item => item.id === selected.key);
    if (finding) {
      aside.replaceChildren(element('p', 'Evidence finding', 'eyebrow'), element('h3', finding.claim));
      for (const support of finding.supports) aside.append(citation(support));
      return;
    }
  }
  if (selected?.kind === 'relation') {
    const relation = graph.relations.find(edge => relationEdgeId(edge) === selected.key);
    if (relation) {
      const conflict = relation.kind === 'CONFLICT';
      aside.replaceChildren(element('p', conflict ? relation.status ?? 'Conflict' : 'Progress', 'eyebrow'),
        element('h3', conflict ? 'Conflicting evidence' : 'Progress in the commitment'),
        element('p', relation.reasoning));
      for (const id of [relation.from_id, relation.to_id]) {
        const finding = graph.findings.find(item => item.id === id);
        if (!finding) continue;
        const block = element('section', '', 'evidence');
        block.append(element('h4', relation.preferred_id === id ? 'Preferred after review' : 'Finding'),
          element('p', finding.claim), button('Inspect finding', () => selectGraphItem({
            id: findingNodeId(id), kind: 'finding', key: id,
          })));
        for (const support of finding.supports) {
          const link = sourceLink(support.source);
          if (link) block.append(link);
        }
        aside.append(block);
      }
      return;
    }
  }
  aside.replaceChildren(element('p', 'Commitment detail', 'eyebrow'), element('h3', graph.commitment.description),
    element('p', graph.commitment.id, 'muted'));
  if (graph.assessment) aside.append(element('h4', graph.assessment.verdict), element('p', graph.assessment.reasoning));
  else aside.append(element('p', 'This commitment has no completed assessment.'));
  const facts = element('dl');
  for (const [label, value] of [
    ['Amount', graph.commitment.amount_value],
    ['Timing', graph.commitment.term_periodicity],
    ['Scope', graph.commitment.geographic_scope],
    ['Beneficiary', graph.commitment.beneficiary?.name ?? graph.commitment.beneficiary?.category],
  ]) {
    if (value) facts.append(element('dt', label), element('dd', value));
  }
  if (facts.children.length) aside.append(facts);
  const original = graph.commitment.original_promise;
  if (original) {
    aside.append(element('h4', 'Original promise'));
    if (original.locator) aside.append(element('p', original.locator, 'muted'));
    const link = sourceLink(original.source);
    if (link) aside.append(link);
  }
}

function citation(support) {
  const block = element('section', '', 'evidence');
  block.append(element('h4', support.source.title));
  if (support.status) block.append(element('p', support.status));
  if (support.locator) block.append(element('p', support.locator, 'muted'));
  if (support.excerpt) block.append(element('blockquote', support.excerpt));
  if (support.summary && support.summary !== support.excerpt) block.append(element('p', support.summary));
  if (support.explanation && ![support.excerpt, support.summary].includes(support.explanation)) {
    block.append(element('p', support.explanation));
  }
  if (support.retrieved_at) block.append(element('p', 'Retrieved ' + support.retrieved_at.slice(0, 10), 'muted'));
  const link = sourceLink(support.source);
  if (link) block.append(link);
  return block;
}

function render() {
  const graph = state.graph;
  $('workspace').hidden = !graph;
  $('empty').hidden = Boolean(graph);
  if (!graph) return;
  const latest = graph.assessment;
  const badge = element('span', latest?.verdict ?? 'No completed assessment', 'verdict');
  const date = element('span', latest?.verification_date ? 'Verified ' + latest.verification_date : 'Verification date unavailable', 'muted');
  $('verdict').replaceChildren(badge, date);
  if (latest?.label) {
    const label = latest.label.toLowerCase().replaceAll('_', ' ');
    $('verdict').prepend(element('span', label[0].toUpperCase() + label.slice(1), 'assessment-label'));
  }
  renderGraph(graph);
  renderInspector(graph);
}

async function init() {
  api = createHttpApi();
  if (demo.has('demo')) {
    $('environment').hidden = false;
    api = createDemoApi({ failRefresh: demo.has('fail-refresh') });
    await openGraph(DEMO_COMMITMENT_ID);
  }
  $('search-form').addEventListener('submit', event => {
    event.preventDefault();
    const query = $('query').value.trim();
    if (query) lookup(query);
  });
  for (const example of document.querySelectorAll('[data-query]')) {
    example.addEventListener('click', () => {
      $('query').value = example.dataset.query;
      lookup(example.dataset.query);
    });
  }
  $('refresh').addEventListener('click', () => refresh(state.graph.commitment.id));
}

void init();
