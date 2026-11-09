import { createHttpApi, waitForJob } from './api.mjs';
import { createDemoApi, DEMO_COMMITMENT_ID } from './demo.mjs';
import { createEvidenceGraph, evidenceBranch, findingNodeId, relationEdgeId } from './evidence-graph.mjs';

const $ = id => document.getElementById(id);
const demo = new URLSearchParams(location.search);
const state = {
  graph: null, organization: null, organizationExpanded: false, selected: null, request: null,
  branchRoot: null, branchHistory: [],
};
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
  let previous;
  if (state.graph?.commitment.id !== id) {
    state.selected = null;
    state.branchRoot = null;
    state.branchHistory = [];
  } else if (state.branchRoot && !canRootBranch(next, state.branchRoot)) {
    previous = popGraphView(next);
    state.branchRoot = previous?.root ?? null;
    state.selected = previous?.selected ?? null;
  }
  state.graph = next;
  render();
  if (previous) evidenceView.renderer.restore(previous.viewport);
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

function commitmentItem(graph) {
  return { id: 'commitment:' + graph.commitment.id, kind: 'commitment', label: 'Current commitment' };
}

function organizationItem(graph) {
  return {
    id: 'organization:' + (graph.organization?.id ?? 'unresolved'), kind: 'organization',
    label: graph.organization?.name ?? graph.commitment.responsible_entity_raw ?? 'Organization unresolved',
  };
}

function canRootBranch(graph, item) {
  if (item.kind === 'finding') return graph.findings.some(finding => finding.id === item.key);
  if (item.kind === 'commitment') return item.id === commitmentItem(graph).id;
  return item.kind === 'organization' && item.id === organizationItem(graph).id;
}

function showGraphView(root, selected, viewport) {
  state.branchRoot = root;
  state.selected = selected;
  renderGraph(state.graph);
  renderInspector(state.graph);
  if (viewport) evidenceView.renderer.restore(viewport);
}

function focusBranch(item) {
  if (item.id === state.branchRoot?.id || !canRootBranch(state.graph, item)) return;
  state.branchHistory.push({
    root: state.branchRoot, selected: state.selected, viewport: evidenceView.renderer.snapshot(),
  });
  showGraphView(item, item);
}

function focusInspectorHeading() {
  const heading = $('inspector').querySelector('h3');
  heading.tabIndex = -1;
  heading.focus({ preventScroll: true });
}

function popGraphView(graph) {
  let previous = state.branchHistory.pop();
  while (previous?.root && !canRootBranch(graph, previous.root)) previous = state.branchHistory.pop();
  return previous;
}

function backToPreviousView() {
  const previous = popGraphView(state.graph);
  if (!previous) return;
  showGraphView(previous.root, previous.selected, previous.viewport);
  if (!previous.root) focusInspectorHeading();
}

function openFullGraphConflict(edge) {
  const overview = state.branchHistory[0];
  state.branchHistory = [];
  const id = relationEdgeId(edge);
  showGraphView(null, { id, kind: 'relation', key: id }, overview?.viewport);
  focusInspectorHeading();
}

function renderGraph(graph) {
  const id = graph.commitment.id;
  if (evidenceView?.id !== id) {
    evidenceView?.renderer.destroy();
    const canvas = element('div', '', 'evidence-canvas');
    canvas.setAttribute('role', 'img');
    canvas.setAttribute('aria-label', 'Interactive evidence graph. Double-click a node to focus its branch. Evidence and relations can also be selected below.');
    const navigation = element('div', '', 'branch-navigation');
    const branchTitle = element('span', '', 'branch-title');
    branchTitle.setAttribute('aria-live', 'polite');
    const context = element('div', '', 'branch-context');
    context.append(element('span', 'Focused branch', 'branch-kicker'), branchTitle);
    navigation.append(button('Back', backToPreviousView), context);
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
      element('span', '┄ Conflict', 'legend-conflict'), element('span', 'Double-click a node to focus'));
    $('graph').replaceChildren(navigation, controls, canvas, legend, details);
    evidenceView = {
      id, renderer: createEvidenceGraph(canvas, selectGraphItem, focusBranch),
      items, count, navigation, branchTitle,
    };
  }

  const root = state.branchRoot;
  const branch = evidenceBranch(graph, root);
  evidenceView.renderer.update(branch, root);
  evidenceView.visibleFindingIds = new Set(branch.findings.map(finding => finding.id));
  const findings = branch.findings.length;
  const conflicts = branch.relations.filter(edge => edge.kind === 'CONFLICT');
  evidenceView.count.textContent = findings + (findings === 1 ? ' finding' : ' findings') +
    (conflicts.length ? ' · ' + conflicts.length + (conflicts.length === 1 ? ' conflict' : ' conflicts') : '');
  const options = [
    ...(root?.kind !== 'finding' ? [commitmentItem(graph)] : []),
    ...(!root || root.kind === 'organization' ? [organizationItem(graph)] : []),
    ...branch.findings.map(finding => ({
      id: findingNodeId(finding.id), kind: 'finding', key: finding.id, label: finding.claim,
    })),
    ...branch.relations.map(edge => ({
      id: relationEdgeId(edge), kind: 'relation', key: relationEdgeId(edge),
      label: edge.kind === 'CONFLICT' ? 'Conflict · ' + (edge.status === 'RESOLVED' ? 'resolved' : 'unresolved') : 'Progress relation',
    })),
  ];
  const rootOption = options.find(option => option.id === root?.id);
  evidenceView.navigation.hidden = !root;
  evidenceView.branchTitle.textContent = root?.kind === 'commitment'
    ? graph.commitment.title ?? graph.commitment.description
    : rootOption?.label ?? '';
  if (state.selected && !options.some(option => option.id === state.selected.id)) state.selected = root;
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
  const id = state.selected?.id ?? state.branchRoot?.id ?? commitmentItem(state.graph).id;
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

function appendFocusButton(aside, item) {
  if (item.id === state.branchRoot?.id) return;
  aside.append(button('Focus branch', () => {
    focusBranch(item);
    focusInspectorHeading();
  }));
}

function appendOutsideConflicts(aside, graph, finding) {
  const root = state.branchRoot;
  if (root?.kind !== 'finding') return;
  const visible = evidenceView.visibleFindingIds;
  const conflicts = graph.relations.filter(edge => edge.kind === 'CONFLICT' &&
    visible.has(edge.from_id) !== visible.has(edge.to_id) &&
    (finding.id === root.key || finding.id === edge.from_id || finding.id === edge.to_id));
  if (!conflicts.length) return;
  const section = element('section', '', 'branch-conflicts');
  section.append(element('h4', conflicts.length === 1
    ? 'Conflict outside this branch' : 'Conflicts outside this branch'));
  for (const edge of conflicts) {
    const side = visible.has(edge.from_id) ? edge.from_id : edge.to_id;
    const claim = graph.findings.find(item => item.id === side)?.claim;
    const row = element('div', '', 'branch-conflict');
    if (claim && side !== finding.id) row.append(element('p', claim));
    row.append(button('View in full graph', () => openFullGraphConflict(edge)));
    section.append(row);
  }
  aside.append(section);
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
  if (selected?.kind === 'organization') {
    const organization = organizationItem(graph);
    aside.replaceChildren(element('p', 'Organization', 'eyebrow'), element('h3', organization.label));
    appendFocusButton(aside, organization);
    if (!graph.organization) return;
    aside.append(button(state.organizationExpanded ? 'Collapse commitments' : 'Expand commitments', toggleOrganization));
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
      appendFocusButton(aside, { id: findingNodeId(finding.id), kind: 'finding', key: finding.id });
      appendOutsideConflicts(aside, graph, finding);
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
  const { commitment, assessment } = graph;
  const title = commitment.title || commitment.description;
  aside.replaceChildren(element('p', 'Commitment detail', 'eyebrow'), element('h3', title));
  appendFocusButton(aside, commitmentItem(graph));
  const facts = element('dl');
  for (const [label, value] of [
    ['Amount', commitment.amount_value],
    ['Timing', commitment.term_periodicity],
    ['Scope', commitment.geographic_scope],
    ['Beneficiary', commitment.beneficiary?.name ?? commitment.beneficiary?.category],
  ]) {
    if (value) facts.append(element('dt', label), element('dd', value));
  }
  if (facts.children.length) aside.append(facts);
  const original = commitment.original_promise;
  if (original) {
    aside.append(element('h4', 'Original promise'));
    if (original.locator) aside.append(element('p', original.locator, 'muted'));
    const link = sourceLink(original.source);
    if (link) aside.append(link);
  }
  if (assessment) {
    const details = element('details', '', 'inspector-disclosure');
    details.append(element('summary', 'Assessment details'), element('p', assessment.verdict, 'verdict'),
      element('p', assessment.reasoning));
    aside.append(details);
  }
  const record = element('details', '', 'inspector-disclosure');
  record.append(element('summary', 'Full commitment record'));
  if (commitment.description !== title) record.append(element('p', commitment.description));
  record.append(element('p', commitment.id, 'muted'));
  aside.append(record);
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
  const label = latest?.label?.toLowerCase().replaceAll('_', ' ');
  const status = label ? label[0].toUpperCase() + label.slice(1) :
    latest ? 'Assessment available' : 'No completed assessment';
  const date = element('span', latest?.verification_date ? 'Verified ' + latest.verification_date : 'Verification date unavailable', 'muted');
  $('verdict').replaceChildren(element('span', status, 'assessment-label'), date);
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
