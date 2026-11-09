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
