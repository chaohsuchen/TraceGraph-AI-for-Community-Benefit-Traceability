# TraceGraph MVP 接口契约

应用库从空目录开始。Golden Workbook 只由评估器读取，参考证据、来源和人工结论留在评分端。旧版工作簿导入数据与新版应用 schema 隔离。OpenAPI 描述当前接口，examples.json 保留给旧版回归测试。

## Worktree 分工

| Worktree | 负责内容 |
|---|---|
| feat/data-api | PostgreSQL 目录、研究任务、证据去重持久化、图谱投影 |
| feat/retrieval | 混合检索、承诺身份判定、LLM 证据归并和综合评估 |
| feat/research-agent | 首次研究、刷新编排、冲突补查、独立 Golden 评估 |
| feat/frontend | 球形证据图、证据和关系详情、缩放和键盘选择 |

## 浏览器接口

- POST /api/v1/lookup 接收自然语言查询。MATCH 返回承诺 ID，AMBIGUOUS 返回澄清问题，未命中则返回 202 和独立研究任务 ID。
- GET /api/v1/commitments/{commitment_id} 返回 commitment、organization、assessment、findings 和 relations。
- GET /api/v1/organizations/{organization_id}/commitments 按组织 ID 返回该组织的承诺。
- POST /api/v1/commitments/{commitment_id}/research-runs 保留已有路径。它启动一次刷新任务，成功后更新同一条承诺的累积证据。
- GET /api/v1/research-jobs/{job_id} 查询任务。RESOLVED 包含 commitment_id，run_id 是兼容字段，当前返回 null。

AMBIGUOUS 只表示用户的选择会改变组织、项目或义务等研究对象，由 LLM 判定并返回一个区分对象的问题。明确对象的证据缺口、原文截断或无关搜索命中不构成对象歧义。

DISCOVER 在原始条款仍不完整或来源复核中断时返回 PARTIAL_RESEARCH，不保存承诺、不生成 commitment_id 或履约结论。它包含 query、message、facts（与目标有关的来源原文）和 gaps（尚需核实的内容）。可选 unmatched_records 保存关联尚未经确认的原文，单独呈现，不能作为目标事实。facts 与 unmatched_records 的每项都包含 source_title、source_url 和 excerpt。成功搜索但未能验证任何摘录时，facts 为空；仍可返回明确标注未验证摘录的来源链接。后续搜索是可选操作，用户可原样继续，不必补充一个已经明确的对象。旧版 NO_PUBLIC_PROMISE 结果仍可读取。

建图前的独立条款复核会再次确认对象是否需要用户选择。最多六条额外候选事实各自只用本条引用进行独立语义复核。未通过或超出预算的候选保留原文，不进入图谱；成功的 RESOLVED 任务也可带 unmatched_records。它们存于任务结果，不构成图谱事实或履约依据。

AMBIGUOUS、待研究的查询和 PARTIAL_RESEARCH 可以带上 suggestions。SAVED_COMMITMENT 指向已有承诺；WEB_LEAD 的 source_title 是来源元数据，title 为兼容字段，不能用生成摘要充当来源标题。description 与非空 excerpt 保留来源原文。只有链接而没有已验证引文时，excerpt 为空字符串，description 明示未验证。浏览器读取旧 WEB_LEAD 而缺少 source_title 时用 URL 作标题，不显示旧的生成式标题。

同一 URL 的不同摘录按 URL 与摘录共同去重，可作为不同条款供选择。用户选择已有承诺时直接加载图谱；选择网页线索时，新查询包含当前目标、来源 URL 和选中的完整摘录；补充信息时保留已有对话上下文。用户可以随时退出。

两个 POST 都使用 Idempotency-Key。网络重试复用同一键，独立刷新生成新键。相同键搭配不同请求会返回 409。失败或尚未完成的任务保留当前图谱。

## 图谱数据和更新

- finding 有稳定 ID、归纳后的具体事实和一个或多个 supports。每个 support 保存来源、定位、摘录或摘要、状态及抓取时间。语义重复的事实可以合并出处，重复引用复用已有记录。原始引用保留。
- PROGRESS 的 from_id 指较早事实，to_id 指进展。图谱用有方向的箭头表示。
- CONFLICT 连接相斥事实。status 为 UNRESOLVED 或 RESOLVED。只有具备引用的补查才能明确 preferred_id 并给出理由。两边仍可查看。每次任务最多一轮定向补查。
- assessment 是当前的综合结论。label、verdict、reasoning 和 verification_date 依据累计去重证据。并发刷新用修订校验，避免覆盖先完成的证据更新。
- 公司、承诺和事实节点可以分别点选。冲突边显示双方说法和出处。新增证据时保留现有节点坐标。

## 评估

一次只选少量有明细证据的承诺。每个测试问题在空目录发起一次独立研究。评分端对返回的证据与 Golden 中的原始行做一对一匹配，给出已确认召回率。相似证据留待复核，未匹配条目可能是新增证据。实际用户库与测试目录独立。
