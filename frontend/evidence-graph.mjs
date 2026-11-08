import cytoscape from '/vendor/cytoscape.mjs';

const styles = [
  {
    selector: 'node',
    style: {
      shape: 'ellipse', width: 50, height: 50, 'background-fill': 'radial-gradient',
      'background-gradient-stop-colors': '#e1eddf #83a894', 'background-gradient-stop-positions': '3% 92%',
      label: 'data(label)', color: '#22362c', 'font-family': 'system-ui, sans-serif',
      'font-size': 12, 'text-valign': 'bottom', 'text-margin-y': 7, 'text-wrap': 'wrap',
      'text-max-width': 112, 'text-background-opacity': 0.91, 'text-background-color': '#fbfcf8',
      'text-background-padding': 2,
    },
  },
  {
    selector: 'node.commitment',
    style: { width: 74, height: 74, 'background-gradient-stop-colors': '#65a87b #22543a',
      'font-weight': 700, 'text-max-width': 135 },
  },
  {
    selector: 'node.organization',
    style: { width: 55, height: 55, 'background-gradient-stop-colors': '#f0ebcd #b2b88d' },
  },
  {
    selector: 'edge',
    style: { width: 1.35, 'line-color': '#aab9ad', 'curve-style': 'bezier' },
  },
  {
    selector: 'edge.progress',
    style: { 'line-color': '#568064', 'target-arrow-color': '#568064', 'target-arrow-shape': 'triangle' },
  },
  {
    selector: 'edge.conflict',
    style: { 'line-color': '#ac493c', 'line-style': 'dashed', width: 2,
      label: 'data(label)', color: '#963e32', 'font-size': 10, 'text-margin-y': -8,
      'text-background-opacity': 1, 'text-background-color': '#fbfcf8', 'text-background-padding': 2 },
  },
  { selector: 'node.focused', style: { 'border-width': 3, 'border-color': '#2c6a49' } },
  { selector: 'edge.focused', style: { width: 3.5 } },
];

export const findingNodeId = id => 'finding:' + id;
export const relationEdgeId = edge => 'relation:' + edge.kind + ':' + edge.from_id + ':' + edge.to_id;

function shortLabel(text) {
  return text.length > 41 ? text.slice(0, 39).trimEnd() + '…' : text;
}

function model(graph) {
  const commitmentId = 'commitment:' + graph.commitment.id;
  const organizationId = 'organization:' + (graph.organization?.id ?? 'unresolved');
  const title = graph.commitment.title ?? graph.commitment.description;
  const organization = graph.organization?.name ?? graph.commitment.responsible_entity_raw ?? 'Organization unresolved';
  const progress = graph.relations.filter(edge => edge.kind === 'PROGRESS');
  const findings = graph.findings;
  const known = new Set(findings.map(item => item.id));
  const parents = new Set(progress.filter(edge => known.has(edge.from_id)).map(edge => edge.to_id));
  const levels = new Map(findings.map(item => [item.id, 0]));
  for (let pass = 0; pass < findings.length; pass++) {
    let changed = false;
    for (const edge of progress) {
      if (!known.has(edge.from_id) || !known.has(edge.to_id)) continue;
      const next = levels.get(edge.from_id) + 1;
      if (levels.get(edge.to_id) < next) {
        levels.set(edge.to_id, next);
        changed = true;
      }
    }
    if (!changed) break;
  }
  const groups = new Map();
  for (const finding of findings) {
    const level = levels.get(finding.id);
    if (!groups.has(level)) groups.set(level, []);
    groups.get(level).push(finding);
  }
  const nodes = [
    { data: { id: organizationId, label: shortLabel(organization), kind: 'organization' },
      classes: 'organization', position: { x: -175, y: 0 } },
    { data: { id: commitmentId, label: shortLabel(title), kind: 'commitment' },
      classes: 'commitment', position: { x: 0, y: 0 } },
  ];
  for (const [level, group] of groups) {
    group.forEach((finding, index) => {
      nodes.push({
        data: { id: findingNodeId(finding.id), label: shortLabel(finding.claim), kind: 'finding', key: finding.id },
        classes: 'finding',
        position: { x: (index - (group.length - 1) / 2) * 166, y: 142 + level * 126 },
      });
    });
  }
  const edges = [
    { data: { id: 'belongs:' + commitmentId, source: organizationId, target: commitmentId } },
    ...findings.filter(finding => !parents.has(finding.id)).map(finding => ({
      data: { id: 'about:' + finding.id, source: commitmentId, target: findingNodeId(finding.id) },
    })),
  ];
  for (const edge of graph.relations) {
    if (!known.has(edge.from_id) || !known.has(edge.to_id)) continue;
    const kind = edge.kind === 'CONFLICT' ? 'conflict' : 'progress';
    edges.push({
      data: { id: relationEdgeId(edge), source: findingNodeId(edge.from_id), target: findingNodeId(edge.to_id),
        kind: 'relation', label: kind === 'conflict' ? 'Conflict' : '', key: relationEdgeId(edge) },
      classes: 'relation ' + kind,
    });
  }
  return { nodes, edges };
}

function freePosition(wanted, occupied) {
  let offset = 0;
  while (occupied.some(position => Math.abs(position.x - wanted.x - offset) < 130 &&
      Math.abs(position.y - wanted.y) < 65)) {
    offset = offset <= 0 ? -offset + 166 : -offset;
  }
  return { x: wanted.x + offset, y: wanted.y };
}

export function createEvidenceGraph(container, onSelect) {
  const cy = cytoscape({
    container, style: styles, elements: [], layout: { name: 'preset' }, minZoom: 0.28, maxZoom: 2.3,
    wheelSensitivity: 0.7, boxSelectionEnabled: false, autounselectify: true,
  });
  let initialized = false;
  cy.on('tap', 'node, edge.relation', event => onSelect(event.target.data()));
  const observer = new ResizeObserver(() => cy.resize());
  observer.observe(container);
  return {
    update(graph) {
      const { nodes, edges } = model(graph);
      const ids = new Set([...nodes, ...edges].map(item => item.data.id));
      const addedNodes = nodes.some(node => !cy.getElementById(node.data.id).length);
      cy.batch(() => {
        cy.elements().filter(item => !ids.has(item.id())).remove();
        const occupied = cy.nodes().map(node => node.position());
        for (const node of nodes) {
          const existing = cy.getElementById(node.data.id);
          if (existing.length) {
            existing.data(node.data);
          } else {
            if (initialized) node.position = freePosition(node.position, occupied);
            cy.add(node);
            occupied.push(node.position);
          }
        }
        for (const edge of edges) {
          const existing = cy.getElementById(edge.data.id);
          if (!existing.length) cy.add(edge);
          else existing.data(edge.data);
        }
      });
      // Keep existing model positions. Fit the viewport only when new nodes
      // would otherwise appear beyond the visible canvas.
      const visible = cy.extent();
      const bounds = cy.elements().boundingBox();
      if (!initialized || (addedNodes && (bounds.x1 < visible.x1 || bounds.y1 < visible.y1 ||
          bounds.x2 > visible.x2 || bounds.y2 > visible.y2))) {
        cy.fit(undefined, 35);
      }
      initialized = true;
    },
    focus(id) {
      cy.elements().removeClass('focused');
      if (id) cy.getElementById(id).addClass('focused');
    },
    zoom(multiplier) {
      cy.zoom({ level: cy.zoom() * multiplier, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
    },
    fit() { cy.fit(undefined, 35); },
    destroy() { observer.disconnect(); cy.destroy(); },
  };
}
