# TraceGraph MVP 接口契约

四个 worktree 共用本目录。[OpenAPI](./openapi.json) 定义浏览器接口，[样例](./examples.json) 可直接供前端 mock 和后端契约测试使用。内部接口用语言无关的类型签名描述，部署时可在同一个后端进程内调用。

## 接口归属

| Worktree | 提供 | 依赖 |
|---|---|---|
| `feat/frontend` | 搜索、澄清、承诺图、公司展开、研究进度 | HTTP 契约及样例 |
| `feat/data-api` | HTTP 路由、图谱投影、研究任务状态、数据库读写接口 | `Agent.lookup` 和 `Agent.execute` |
| `feat/retrieval` | `Retrieval.resolve`，包含 BM25、pgvector 排名、RRF 和身份判定 | `CatalogSearch` |
| `feat/research-agent` | `Agent.lookup`、`Agent.execute`、网页研究和证据汇总 | `Retrieval.resolve`、`ResearchStore` |

共享契约的改动随样例一起审阅。Data/API 维护 HTTP 绑定和迁移，Retrieval 维护排序和判定，Agent 维护研究业务分支。各线以同一份样例验证自己的接口。

## 交付与集成验收

四条开发线从同一个契约提交分出。各线交付时提供提交 SHA、启动或测试命令、测试结果，以及联调需要的配置和已知阻塞。

| Worktree | 交给集成负责人的产出 |
|---|---|
| `feat/data-api` | 可从空库执行的迁移和样例导入命令。实现五个 HTTP 端点、承诺图投影和研究存储接口。验证 ID 保留及同一个幂等键的请求只创建一个任务。 |
| `feat/retrieval` | 可接入 `CatalogSearch` 的检索模块和可注入的模型接口。提供 H008 改写命中、H001/H009 范围澄清、候选 ID 校验的测试。 |
| `feat/research-agent` | 可被 API 和 worker 调用的 Agent，提供固定网页研究样例和失败样例。验证原始承诺来源门槛、复用已有 ID、追加刷新轮次。 |
| `feat/frontend` | 可配置 API 地址的页面和启动命令。展示搜索、澄清、图谱、公司展开、研究进度和历史轮次，并用共享 JSON 验证界面状态。 |

集成负责人在首批产出可联调时使用短期 `integration/mvp` worktree，维护 `tests/integration/`、联调启动方式和验收记录。先接通 Data/API 与 Retrieval，再接 Agent 和前端。模块缺陷回到对应开发分支修复，集成分支负责装配和跨模块测试。通过后将集成结果合回 `main`。

1. **先验接口**。各模块分别用共享样例验证公开接口和必要的错误分支。集成负责人协调任何契约变更，并更新规范和样例。
2. **再验全链路**。使用真实的 PostgreSQL + pgvector、后端、worker 和前端。模型和网页服务用固定响应复现 H008 命中、H001/H009 澄清、首次研究新增或复用、刷新成功和失败保留历史。验证同一来源在 H008 和 H010 上的证据边不同，以及按组织 ID 展开。
3. **最后验实时研究**。单独跑一次真实网页研究，确认原始来源可定位，研究结果和历史能从数据库重新读取。记录通过项、失败项和对应修复提交。

## 浏览器 HTTP API

统一前缀为 `/api/v1`。ID 是不透明字符串。导入的承诺沿用 Excel `gold_id`，前端以组织 ID 展开公司。

| 接口 | 调用方行为 |
|---|---|
| `POST /lookup` | 提交 `{query}`。`200 MATCH` 返回承诺 ID，随后读取承诺图。`200 AMBIGUOUS` 返回澄清问题，用户可把补充信息连同原查询重新提交。`202` 表示查找未命中并已受理首次研究。 |
| `GET /commitments/{commitment_id}` | 默认读取最新已完成轮次的图。带 `?run_id=` 可看同一承诺的历史轮次。即使暂无已完成轮次，也返回承诺。此时 `latest_completed_run` 和 `viewed_run_id` 为 `null`，`source_edges` 为空。 |
| `GET /organizations/{organization_id}/commitments` | 返回该组织及承诺摘要。 |
| `POST /commitments/{commitment_id}/research-runs` | **Research again**。无请求体。返回 `202`，用原承诺 ID 刷新。 |
| `GET /research-jobs/{job_id}` | 两个 `202` 响应均带 `job_id` 和 `Location`。前端轮询到 `COMPLETED` 或 `FAILED`。`COMPLETED` 下有 `RESOLVED`、`AMBIGUOUS` 和 `NO_PUBLIC_PROMISE` 三种业务结果。 |

`RESOLVED` 后用返回的 `commitment_id` 重新读取图。`run_id` 标识该任务新完成的轮次。首次研究发现已有承诺时复用其 ID，`run_id` 为 `null`。`NO_PUBLIC_PROMISE` 表示研究正常完成，但未找到能定位的原始承诺来源。刷新期间或刷新失败后，图仍展示最近的已完成轮次。

两个 `POST` 都要求 `Idempotency-Key`。一次提交的网络重试复用原键。在同一操作、同一资源范围内，相同键和相同请求返回已记录的响应，已有任务时返回同一个 `job_id`。相同键配不同请求返回 `409`。若任务尚未受理且服务暂时出错，允许用同一键重试。澄清后重新提交、主动再刷新或重试一个已失败的研究任务时，使用新键。`query` 去除首尾空白后不能为空。

| HTTP 状态 | `error.code` | 含义 |
|---|---|---|
| `400` | `INVALID_REQUEST` | 请求体、查询或必填请求头不符合契约。 |
| `404` | `NOT_FOUND` | 资源不存在，或指定的轮次不属于该承诺的已完成历史。 |
| `409` | `IDEMPOTENCY_CONFLICT` | 已用的幂等键对应另一份请求。 |
| `503` | `RESOLUTION_UNAVAILABLE` / `RESEARCH_UNAVAILABLE` | 检索或身份判定出错，或当前无法受理研究任务。非法候选 ID 归入判定错误。 |
| `500` | `INTERNAL_ERROR` | 其他服务错误。 |

已受理任务的运行错误通过任务状态 `FAILED` 和 `error` 返回，轮询本身仍是 `200`。错误对象包含可展示的 `message` 和 `retryable`。`retryable` 描述服务是否适合重试，前端仍按上述幂等规则发起请求。

## 后端接口

### Data/API → Retrieval 的只读检索接口

```text
CatalogSearch.withSnapshot(read: SearchSnapshot -> T) -> T

SearchSnapshot
  catalog_revision: string
  scan(): SearchRecord[]
  vector_top_k(query_embedding, k): RankedId[]

RankedId = { commitment_id, rank }
SearchRecord = {
  commitment_id, title, description, organization_id, organization_name,
  responsible_entity_raw, beneficiary, obligation_type, amount_value,
  term_periodicity, geographic_scope, condition_contingency, milestone
}
```

`scan` 给 BM25 和候选投影使用，`vector_top_k` 在 PostgreSQL/pgvector 上查询。两份排名和候选字段来自同一快照。排名从 1 开始，每份排名中每个承诺 ID 只出现一次。向量模型与入库 embedding 使用同一版本和维度。发布到目录的承诺必须能在同一修订中参与两种检索。组织、受益人和时间字段可缺失，缺失值保留为 `null`。

### Retrieval → Agent 的身份判定接口

```text
Retrieval.embed_commitment(description) -> Embedding
Retrieval.resolve({ query } | { discovered: DiscoveredIdentity }) ->
    MATCH { commitment_id }
  | AMBIGUOUS { question }
  | NO_MATCH { catalog_revision }

DiscoveredIdentity = {
  description, organization_id, organization_name, responsible_entity_raw,
  beneficiary, obligation_type, amount_value, term_periodicity,
  geographic_scope, condition_contingency, milestone, original_promise
}
```

Retrieval 对 BM25 和向量结果按承诺 ID 做 RRF，向 LLM 提供 top-k 的紧凑候选字段。判定时比较组织、具体义务、范围、受益人和相关时间。先校验 `MATCH` 的 ID 是否在本次候选中。无效模型响应、索引故障或模型调用失败均作为判定错误传递。`NO_MATCH` 仅表示一次成功判定的结果。Excel 导入和首次研究入库统一调用 `embed_commitment`，使用与向量检索兼容的模型和维度。

### Agent → Data/API 的应用接口

```text
Agent.lookup(query) -> MATCH { commitment_id }
                    | AMBIGUOUS { question }
                    | START_DISCOVERY { query }

Agent.execute(claimed_job) -> void

ClaimedJob = { job_id, claim_token } & (
    DISCOVER { query }
  | REFRESH { commitment_id }
)
```

路由将 `START_DISCOVERY` 持久化为任务，再返回 `202`。刷新路由确认承诺存在后受理 `REFRESH`。Data/API 持有任务和幂等记录，负责原子受理、认领和终态写入。`claim_token` 由 Data/API 发放，终态写入必须验证当前认领。任务失败或重试时，同一 `job_id` 至多产生一个完成轮次。

### Data/API → Agent 的存储接口

```text
ResearchStore.load_commitment(commitment_id) -> CommitmentForResearch | null
ResearchStore.finish_discovery(claim, expected_catalog_revision, draft: NewCommitmentDraft, result)
    -> COMMITTED { commitment_id, run_id } | RECHECK_REQUIRED
ResearchStore.finish_refresh(claim, result) -> COMMITTED { commitment_id, run_id }
ResearchStore.finish_without_run(claim, outcome: JobResult) -> void
ResearchStore.fail(claim, error) -> void

Claim = { job_id, claim_token }
CommitmentForResearch = { commitment: Commitment, organization: Organization | null }
NewCommitmentDraft = DiscoveredIdentity & { title, original_text, notes, embedding }
OriginalPromiseDraft = { source: SourceDraft, locator, excerpt }
SourceDraft = { source_id?, title, url, citation, publisher, source_type, published_on }
ResearchResult = { verification_date, verdict, reasoning, evidence: EvidenceDraft[] }
EvidenceDraft = { source: SourceDraft, status, explanation, excerpt, summary, locator, retrieved_at }
```

`load_commitment` 提供完整的承诺和原始来源，供现有承诺刷新。`DiscoveredIdentity.original_promise` 使用 `OriginalPromiseDraft`，带可核查的出处和定位。来源草稿可带已确认的来源 ID。`JobResult` 与 OpenAPI 的 `CompletedJob.result` 同形。实时研究的 `verification_date` 和已抓取证据的 `retrieved_at` 必填。可确认的来源发布日期单独记在 `published_on`。

1. 首次研究找到原始承诺后，用发现的结构化身份调用 `Retrieval.resolve` 再查重。命中则以 `RESOLVED` 复用已有 ID，歧义则返回澄清问题，找不到原始出处则完成为 `NO_PUBLIC_PROMISE`。这三种情况走 `finish_without_run`。
2. 查重返回 `NO_MATCH` 后，`finish_discovery` 在同一事务中校验目录修订，写入承诺、来源、研究轮次、证据和任务终态。目录发生变更时返回 `RECHECK_REQUIRED`，Agent 对新快照重新查重。导入和新增承诺等影响身份检索的写入同事务更新目录修订。
3. `finish_refresh` 从任务取目标承诺 ID，在同一事务中追加研究轮次和证据并完成任务。失败的任务保留错误详情。图谱只读已完成结果，按完成时间和稳定 ID 选最新轮次。对历史导入中缺少完成时间的记录，已知完成时间的轮次排在前面。

## 图谱和工作簿样例

图以 `commitment` 为中心。`organization` 是关联的公司，归属不明时为 `null`。每个 `source_edges` 条目是该承诺与一个来源的关系，证据数组归属于 `viewed_run_id`。相同来源可出现在不同承诺的边上，各自保留证据状态和解释。查阅历史时 `latest_completed_run` 仍指最新轮次，边切换为所选历史轮次的证据。

样例使用 `TG-GOLD-H008` 的资本支出上限与年度支持、`TG-GOLD-H001` / `TG-GOLD-H009` 的永久运营与施工范围差异，以及 `TG-GOLD-H010` 的目标与实际用电结果。合成的来源、轮次和任务 ID 仅供测试。没有已知的抓取日期或核验日期时使用 `null`。

导入时保留 `gold commitments` 的原文、归一化描述、原始文档定位和业务字段。`document_id` 与 `sources.source_id` 各有自己的命名空间。只有确认关联时才填 `original_promise.source`。同一来源可复用其内部 `Source.id`。汇总表的 `searched_on` 保留为历史搜索字段。详情页的综合结论、逐文档证据状态及 Golden 评估标签分别保存，评估标签供独立导出。`TG-GOLD-A019` 的冲突标签、`TG-GOLD-A015` 缺少承诺主记录和 `Sheet4` 的两套表头进入导入问题清单。

样例中的 `request.path` 包含 API 前缀，`request.query` 是查询参数，`response.body` 可直接用作模拟响应。`response.headers.Location` 可用于测试轮询跳转。
