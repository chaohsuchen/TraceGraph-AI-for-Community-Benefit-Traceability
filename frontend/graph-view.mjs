export function graphRoot(graph) {
  if (graph.question) {
    return { id: 'question:' + graph.jobId, kind: 'question', label: 'Research question', title: graph.question };
  }
  return { id: 'commitment:' + graph.commitment.id, kind: 'commitment', label: 'Current commitment',
    title: graph.commitment.title ?? graph.commitment.description };
}

export function partialGraph(jobId, question, result) {
  if (result.outcome !== 'PARTIAL_RESEARCH') return null;
  const findings = [];
  for (const fact of result.facts ?? []) {
    if (!fact.excerpt?.trim() || !/^https?:\/\//i.test(fact.source_url ?? '')) continue;
    findings.push({ id: 'excerpt-' + (findings.length + 1), claim: fact.claim || fact.excerpt, supports: [{
      excerpt: fact.excerpt, source: { title: fact.source_title || fact.source_url, url: fact.source_url },
    }] });
  }
  return findings.length ? { jobId, question, findings, gaps: result.gaps ?? [], relations: [] } : null;
}

export function evidenceBranch(graph, root) {
  if (root?.kind !== 'finding') return graph;

  const children = new Map(graph.findings.map(finding => [finding.id, []]));
  for (const edge of graph.relations) {
    if (edge.kind === 'PROGRESS' && children.has(edge.to_id)) {
      children.get(edge.from_id)?.push(edge.to_id);
    }
  }

  const visible = new Set(children.has(root.key) ? [root.key] : []);
  for (const id of visible) {
    for (const child of children.get(id)) visible.add(child);
  }
  return {
    ...graph,
    findings: graph.findings.filter(finding => visible.has(finding.id)),
    relations: graph.relations.filter(edge => visible.has(edge.from_id) && visible.has(edge.to_id)),
  };
}
