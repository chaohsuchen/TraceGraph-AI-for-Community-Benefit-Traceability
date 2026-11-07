import { createHttpApi, waitForJob } from './api.mjs';
import { createDemoApi } from './demo.mjs';

const $ = id => document.getElementById(id);
const demo = new URLSearchParams(location.search);
const state = { graph: null, organization: null, organizationExpanded: false, selected: null, request: null };
let api;

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

async function loadGraph(id, runId, signal) {
  const next = await api.graph(id, runId, signal);
  signal?.throwIfAborted();
  if (state.graph?.organization?.id !== next.organization?.id) {
    state.organization = null;
    state.organizationExpanded = false;
  }
  state.graph = next;
  state.selected = null;
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
    await loadGraph(job.result.commitment_id, null, signal);
    notice(isRefresh ? 'Research updated. Earlier results are available in the run selector.' : '');
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
      await loadGraph(result.commitment_id, null, signal);
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
  }, 'Starting a new research run…', () => refresh(id, key));
}

function openGraph(id, runId = null) {
  return action(async signal => {
    await loadGraph(id, runId, signal);
    notice();
  }, 'Loading the evidence graph…', () => openGraph(id, runId));
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

function graphNode(kind, title, click) {
  const node = click ? button('', click, 'node') : element('div', '', 'node');
  node.append(element('span', kind, 'eyebrow'), element('strong', title));
  return node;
}

function renderGraph(graph) {
  const company = element('div', '', 'company-column');
  const organization = graphNode(
    'Company',
    graph.organization?.name ?? graph.commitment.responsible_entity_raw ?? 'Organization unresolved',
    graph.organization ? toggleOrganization : null,
  );
  if (graph.organization) {
    organization.setAttribute('aria-expanded', String(state.organizationExpanded));
    organization.append(element('span', state.organizationExpanded ? 'Collapse commitments' : 'Expand commitments', 'node-hint'));
  }
  company.append(organization);
  if (state.organizationExpanded) {
    const siblings = element('ul', '', 'commitment-list');
    for (const item of state.organization.commitments) {
      const row = element('li');
      const link = button(item.title ?? item.description, () => openGraph(item.id));
      if (item.id === graph.commitment.id) link.setAttribute('aria-current', 'true');
      row.append(link);
      siblings.append(row);
    }
    company.append(siblings);
  }
  const commitment = graphNode('Commitment', graph.commitment.title ?? graph.commitment.description, () => select(null));
  commitment.classList.add('commitment-node');
  commitment.append(element('span', graph.commitment.id, 'node-hint'));
  commitment.setAttribute('aria-pressed', String(state.selected === null));
  const sources = element('div', '', 'sources-column');
  if (!graph.source_edges.length) {
    sources.append(element('p', 'No sources for this research view.', 'source-empty'));
  }
  for (const edge of graph.source_edges) {
    const row = element('div', '', 'source-edge');
    const status = element('span', edge.evidence[0]?.status ?? 'Evidence', 'edge-status');
    const source = graphNode('Source', edge.source.title, () => select(edge));
    source.setAttribute('aria-pressed', String(state.selected?.source.id === edge.source.id));
    row.append(status, source);
    sources.append(row);
  }
  $('graph').replaceChildren(company, commitment, sources);
}

function select(edge) {
  state.selected = edge;
  renderGraph(state.graph);
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
  if (state.selected) {
    const { source, evidence } = state.selected;
    aside.replaceChildren(element('p', 'Source evidence', 'eyebrow'), element('h3', source.title));
    if (source.publisher) aside.append(element('p', source.publisher, 'muted'));
    if (source.published_on) aside.append(element('p', 'Published ' + source.published_on, 'muted'));
    else if (source.citation) aside.append(element('p', source.citation, 'muted'));
    const link = sourceLink(source);
    if (link) aside.append(link);
    for (const item of evidence) {
      const block = element('section', '', 'evidence');
      block.append(element('h4', item.status ?? 'Evidence'));
      if (item.locator) block.append(element('p', item.locator, 'muted'));
      if (item.excerpt) block.append(element('blockquote', item.excerpt));
      if (item.summary) block.append(element('p', item.summary));
      if (item.explanation) block.append(element('p', item.explanation));
      if (item.retrieved_at) block.append(element('p', 'Retrieved ' + item.retrieved_at.slice(0, 10), 'muted'));
      aside.append(block);
    }
    return;
  }
  const allRuns = [graph.latest_completed_run, ...graph.earlier_completed_runs].filter(Boolean);
  const viewed = allRuns.find(run => run.id === graph.viewed_run_id);
  aside.replaceChildren(element('p', 'Commitment detail', 'eyebrow'), element('h3', graph.commitment.description));
  if (viewed) aside.append(element('h4', viewed.verdict), element('p', viewed.reasoning));
  else aside.append(element('p', 'This commitment has no completed research run.'));
  const facts = element('dl');
  for (const [label, value] of [
    ['Amount', graph.commitment.amount_value],
    ['Timing', graph.commitment.term_periodicity],
    ['Scope', graph.commitment.geographic_scope],
    ['Beneficiary', graph.commitment.beneficiary.name ?? graph.commitment.beneficiary.category],
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

function render() {
  const graph = state.graph;
  $('workspace').hidden = !graph;
  $('empty').hidden = Boolean(graph);
  if (!graph) return;
  const latest = graph.latest_completed_run;
  const badge = element('span', latest?.verdict ?? 'No completed research', 'verdict');
  const date = element('span', latest?.verification_date ? 'Verified ' + latest.verification_date : 'Verification date unavailable', 'muted');
  const runs = [latest, ...graph.earlier_completed_runs].filter(Boolean);
  $('verdict').replaceChildren(badge, date);
  if (runs.length > 1) {
    const label = element('label', 'View run ', 'run-picker');
    const options = document.createElement('select');
    runs.forEach((run, index) => {
      const name = index === 0 ? 'Latest' : 'Earlier run ' + index;
      const option = element('option', run.verification_date ? name + ' · ' + run.verification_date : name);
      option.value = run.id;
      options.append(option);
    });
    options.value = graph.viewed_run_id;
    options.addEventListener('change', () => openGraph(graph.commitment.id, options.value));
    label.append(options);
    $('verdict').append(label);
  }
  renderGraph(graph);
  renderInspector(graph);
}

async function init() {
  api = createHttpApi();
  if (demo.has('demo')) {
    $('environment').hidden = false;
    try {
      const response = await fetch('/contracts/examples.json');
      if (!response.ok) throw new Error('Could not load demo fixtures.');
      api = createDemoApi(await response.json(), { failRefresh: demo.has('fail-refresh') });
      await openGraph('TG-GOLD-H008');
    } catch (error) {
      notice(error.message, 'error');
      return;
    }
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
