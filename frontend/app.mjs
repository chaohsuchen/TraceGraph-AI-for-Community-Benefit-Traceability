import { createHttpApi, waitForJob } from './api.mjs';
import { createDemoApi, DEMO_COMMITMENT_ID } from './demo.mjs';
import { createEvidenceGraph, evidenceBranch, findingNodeId, relationEdgeId } from './evidence-graph.mjs';
import { graphRoot, partialGraph } from './graph-view.mjs';

const $ = id => document.getElementById(id);
const demo = new URLSearchParams(location.search);
const state = {
  graph: null, organization: null, organizationExpanded: false, selected: null, request: null,
  branchRoot: null, branchHistory: [], conversation: null,
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
  $('conversation-submit').disabled = state.request !== null;
  $('continue-research').disabled = state.request !== null;
  $('workspace').setAttribute('aria-busy', String(state.request !== null));
}

async function action(task, message, retryAction, conversation = null) {
  state.request?.abort();
  const request = new AbortController();
  state.request = request;
  if (conversation) conversation.request = request;
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
  if (state.graph?.commitment?.id !== id) {
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

function addConversationMessage(author, text) {
  const message = element('div', '', 'conversation-message');
  message.dataset.author = author;
  message.append(element('strong', author === 'user' ? 'You' : 'TraceGraph', 'conversation-author'), element('p', text));
  const log = $('conversation-log');
  log.append(message);
  log.scrollTop = log.scrollHeight;
}

function showRelatedOptions(suggestions) {
  const choices = $('conversation-choices');
  choices.replaceChildren();
  const seen = new Set();
  for (const suggestion of suggestions) {
    const saved = suggestion.kind === 'SAVED_COMMITMENT';
    const id = saved ? suggestion.commitment_id : suggestion.source_url + '\n' + suggestion.description;
    if (!id || seen.has(id)) continue;
    seen.add(id);
    const option = element('li');
    const title = saved ? suggestion.title : suggestion.source_title || suggestion.source_url;
    const category = saved ? ['Saved commitment', suggestion.organization_name].filter(Boolean).join(' · ') : 'Web research lead';
    option.append(element('span', category, 'muted'), element('strong', title));
    if (suggestion.description && suggestion.description !== suggestion.title) {
      option.append(element(saved || suggestion.excerpt === '' ? 'p' : 'blockquote', suggestion.description));
    }
    if (!saved) {
      const link = sourceLink({ url: suggestion.source_url });
      if (link) option.append(link);
    }
    const label = saved ? 'Use this commitment' : 'Research this lead';
    const choose = button(label, () => chooseSuggestion(suggestion));
    choose.setAttribute('aria-label', `${label} ${title}`);
    option.append(choose);
    choices.append(option);
  }
  $('conversation-options').hidden = !choices.children.length;
}

function continueConversation(query, message, suggestions = [], results = null, jobId = null) {
  if (!state.conversation) {
    state.conversation = { query, request: null };
    $('conversation').hidden = false;
    $('conversation-log').after($('notice'));
    addConversationMessage('user', query);
  }
  notice();
  $('conversation-title').textContent = results ? 'Research results' : 'Commitment search';
  $('conversation-form').querySelector('label').textContent = results ? 'Continue research (optional)' : 'Add a detail or source';
  $('research-results').hidden = !results;
  $('research-facts').replaceChildren();
  $('research-gaps').replaceChildren();
  $('research-unmatched-records').replaceChildren();
  $('research-unmatched').hidden = !results?.unmatched_records?.length;
  if (results) {
    for (const fact of results.facts ?? []) {
      const item = element('li');
      item.append(element('strong', fact.source_title), element('blockquote', fact.excerpt));
      const link = sourceLink({ url: fact.source_url });
      if (link) item.append(link);
      $('research-facts').append(item);
    }
    for (const gap of results.gaps ?? []) $('research-gaps').append(element('li', gap));
    for (const record of results.unmatched_records ?? []) {
      const item = element('li');
      item.append(element('strong', record.source_title), element('blockquote', record.excerpt));
      const link = sourceLink({ url: record.source_url });
      if (link) item.append(link);
      $('research-unmatched-records').append(item);
    }
    $('research-empty').hidden = Boolean(results.facts?.length);
  }
  if (results || state.graph?.question) {
    state.graph = results && jobId ? partialGraph(jobId, query, results) : null;
    state.branchRoot = state.selected = null;
    state.branchHistory = [];
    render();
  }
  $('view-research-graph').hidden = !state.graph?.question;
  addConversationMessage('system', message);
  showRelatedOptions(suggestions);
  if (!results) $('conversation-input').focus();
}

function quitConversation() {
  if (state.graph?.question) {
    state.graph = null;
    render();
  }
  if (state.conversation?.request && state.conversation.request === state.request) {
    state.request.abort();
    state.request = null;
    updateBusy();
  }
  state.conversation = null;
  $('conversation').before($('notice'));
  $('conversation').hidden = true;
  $('conversation-log').replaceChildren();
  showRelatedOptions([]);
  $('conversation-input').value = '';
  if (!state.request) notice();
  $('query').focus();
}

function announceGraph(conversation) {
  notice();
  if (!conversation) return;
  $('research-results').hidden = true;
  $('conversation-title').textContent = 'Commitment search';
  showRelatedOptions([]);
  const commitment = state.graph.commitment;
  addConversationMessage('system', `Opened ${commitment.title || commitment.description}.`);
  $('conversation-input').focus();
}

function chooseSuggestion(suggestion) {
  const conversation = state.conversation;
  if (!conversation || state.request) return;
  showRelatedOptions([]);
  if (suggestion.kind === 'SAVED_COMMITMENT') {
    addConversationMessage('user', `Use ${suggestion.title}.`);
    const select = () => action(async signal => {
      await loadGraph(suggestion.commitment_id, signal);
      conversation.query = state.graph.commitment.description || suggestion.title;
      announceGraph(conversation);
    }, 'Loading the selected commitment…', select, conversation);
    select();
  } else {
    addConversationMessage('user', `Research this lead: ${suggestion.title}`);
    const excerpt = suggestion.excerpt ?? suggestion.description;
    const nextQuery = `${conversation.query}\nAdditional source lead (verify against the original question): ${suggestion.source_url}`
      + (excerpt ? `\nSelected source excerpt:\n${excerpt}` : '');
    if (nextQuery.length > 4000) {
      notice('The combined query is too long. Enter a shorter query in the main search box to continue.', 'error');
      return;
    }
    conversation.query = nextQuery;
    lookup(conversation.query);
  }
}

function submitSearch(query) {
  if (state.conversation) {
    state.conversation.query = query;
    addConversationMessage('user', query);
    showRelatedOptions([]);
  }
  lookup(query);
}

async function followJob(id, signal, { query, suggestions = [], retry, isRefresh = false }) {
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
    if (isRefresh) notice('Evidence graph updated.');
    else announceGraph(state.conversation);
  } else if (!isRefresh && job.result.outcome === 'AMBIGUOUS') {
    continueConversation(query, job.result.question, job.result.suggestions ?? suggestions);
  } else if (!isRefresh && ['PARTIAL_RESEARCH', 'NO_PUBLIC_PROMISE'].includes(job.result.outcome)) {
    continueConversation(query, job.result.message, [...suggestions, ...(job.result.suggestions ?? [])], job.result, id);
  } else {
    notice(job.result.message);
  }
}

function lookup(query, key = crypto.randomUUID()) {
  const conversation = state.conversation;
  return action(async signal => {
    const result = await api.lookup(query, key, signal);
    signal.throwIfAborted();
    if (result.outcome === 'MATCH') {
      await loadGraph(result.commitment_id, signal);
      announceGraph(conversation);
    } else if (result.outcome === 'AMBIGUOUS') {
      continueConversation(query, result.question, result.suggestions);
    } else if (result.job_id) {
      await followJob(result.job_id, signal, { query, suggestions: result.suggestions, retry: () => lookup(query) });
    } else {
      throw new Error('TraceGraph returned an unexpected lookup result.');
    }
  }, 'Finding the commitment…', () => lookup(query, key), conversation);
}

function refresh(id, key = crypto.randomUUID()) {
  return action(async signal => {
    const accepted = await api.refresh(id, key, signal);
    signal.throwIfAborted();
    await followJob(accepted.job_id, signal, { isRefresh: true, retry: () => refresh(id) });
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
  return graphRoot(graph);
}

function organizationItem(graph) {
  return {
    id: 'organization:' + (graph.organization?.id ?? 'unresolved'), kind: 'organization',
    label: graph.organization?.name ?? graph.commitment.responsible_entity_raw ?? 'Organization unresolved',
  };
}

function canRootBranch(graph, item) {
  if (item.kind === 'finding') return graph.findings.some(finding => finding.id === item.key);
  if (item.kind === 'commitment' || item.kind === 'question') return item.id === commitmentItem(graph).id;
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
  const id = graphRoot(graph).id;
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
    legend.append(element('span', '— Related'));
    if (!graph.question) legend.append(element('span', '→ Progress', 'legend-progress'),
      element('span', '┄ Conflict', 'legend-conflict'));
    legend.append(element('span', 'Double-click a node to focus'));
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
    ...(!graph.question && (!root || root.kind === 'organization') ? [organizationItem(graph)] : []),
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
  evidenceView.branchTitle.textContent = ['question', 'commitment'].includes(root?.kind)
    ? graphRoot(graph).title
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

function assessmentReasoning(text, graph) {
  const paragraph = element('p', '', 'assessment-reasoning');
  const sources = new Map(graph.findings.flatMap(finding => finding.supports)
    .map(support => [support.source.url, support.source]));
  let start = 0;
  for (const match of text.matchAll(/https?:\/\/[^\s]+/g)) {
    const link = sourceLink(sources.get(match[0]));
    if (!link) continue;
    paragraph.append(document.createTextNode(text.slice(start, match.index)));
    link.textContent = match[0];
    paragraph.append(link);
    start = match.index + match[0].length;
  }
  paragraph.append(document.createTextNode(text.slice(start)));
  return paragraph;
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
  if (graph.question) {
    aside.replaceChildren(element('p', 'Research question', 'eyebrow'), element('h3', graph.question));
    appendFocusButton(aside, graphRoot(graph));
    if (graph.gaps.length) {
      const list = element('ul');
      for (const gap of graph.gaps) list.append(element('li', gap));
      aside.append(element('h4', 'Evidence still needed'), list);
    }
    return;
  }
  const { commitment, assessment } = graph;
  const title = commitment.title || commitment.description;
  aside.replaceChildren(element('p', 'Commitment detail', 'eyebrow'), element('h3', title));
  appendFocusButton(aside, commitmentItem(graph));
  const facts = element('dl');
  for (const [label, value] of [
    ['Amount', commitment.amount_value],
    ['Timing', commitment.term_periodicity],
    ['Recorded promise scope', commitment.geographic_scope],
    ['Intended beneficiary', commitment.beneficiary?.name ?? commitment.beneficiary?.category],
  ]) {
    if (value) facts.append(element('dt', label), element('dd', value));
  }
  if (facts.children.length) aside.append(facts);
  const original = commitment.original_promise;
  if (original) {
    aside.append(element('h4', 'Original promise'));
    if (commitment.original_text) aside.append(element('p', commitment.original_text));
    if (original.locator) aside.append(element('p', original.locator, 'muted'));
    const link = sourceLink(original.source);
    if (link) aside.append(link);
  }
  if (assessment) {
    aside.append(element('p', assessment.verdict, 'verdict'));
    const details = element('details', '', 'inspector-disclosure');
    details.append(element('summary', 'Assessment details'), assessmentReasoning(assessment.reasoning, graph));
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
  if (!graph) {
    evidenceView?.renderer.destroy();
    evidenceView = null;
    $('graph').replaceChildren();
    $('inspector').replaceChildren();
    return;
  }
  $('workspace-title').textContent = graph.question ? 'Research evidence graph' : 'Commitment graph';
  $('graph').setAttribute('aria-label', graph.question ? 'Research question and cited findings'
    : 'Organization, commitment, evidence and relations');
  $('refresh').hidden = Boolean(graph.question);
  if (graph.question) {
    $('verdict').replaceChildren(element('span', 'Partial research', 'assessment-label'),
      element('span', 'Delivery verdict unavailable.', 'muted'));
    renderGraph(graph);
    renderInspector(graph);
    return;
  }
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
    if (query) submitSearch(query);
  });
  $('conversation-form').addEventListener('submit', event => {
    event.preventDefault();
    const conversation = state.conversation;
    const input = $('conversation-input');
    const detail = input.value.trim();
    if (!conversation || !detail || state.request) return;
    const query = `${conversation.query}\n\nAdditional detail from the user: ${detail}`;
    if (query.length > 4000) {
      notice('The combined query is too long. Enter a shorter query in the main search box to continue here.', 'error');
      return;
    }
    input.value = '';
    conversation.query = query;
    addConversationMessage('user', detail);
    showRelatedOptions([]);
    lookup(query);
  });
  $('conversation-quit').addEventListener('click', quitConversation);
  $('continue-research').addEventListener('click', () => {
    if (!state.conversation || state.request) return;
    addConversationMessage('user', 'Continue research with the same target.');
    lookup(state.conversation.query);
  });
  for (const example of document.querySelectorAll('[data-query]')) {
    example.addEventListener('click', () => {
      $('query').value = example.dataset.query;
      submitSearch(example.dataset.query);
    });
  }
  $('refresh').addEventListener('click', () => refresh(state.graph.commitment.id));
}

void init();
