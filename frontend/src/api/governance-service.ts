import { request } from './client'

async function asJson<T>(response: Response): Promise<T> {
  return (await response.json()) as T
}

export type OrphanReceipt = {
  id: number
  回执编号: string
  执行轮次id: number | null
  计划id: number | null
  任务id: number | null
  任务名称: string
  原执行站点: string
  原执行站点名称: string
  抓取量: number
  上报时间: string
  最近重投时间: string
  重投次数: number
  归属状态: string
  无法确定原因: string
  解析站点: string
  补记状态: string
  补记人: string
  补记依据: string
  补记时间: string
  入账标识: string
  status: string
}

export type ExecutionRound = {
  id: number
  计划id: number
  轮次编号: string
  任务id: number
  任务名称: string
  来源站点: string
  来源站点名称: string
  抓取量: number
  执行时间: string
}

export type SiteBackfill = {
  id: number
  补记单号: string
  回执编号: string
  执行轮次id: number
  轮次编号: string
  任务id: number
  任务名称: string
  站点名称: string
  站点显示名: string
  抓取量: number
  补记人: string
  补记依据: string
  补记时间: string
  入账状态: string
  入账标识: string
}

export type MetricsLedger = {
  id: number
  入账时间: string
  站点名称: string
  站点显示名: string
  抓取增量: number
  来源类型: string
  来源id: number
  来源标识: string
  回执编号: string
  执行轮次id: number | null
  任务id: number | null
  操作人: string
}

export type MetricSite = {
  metric_id: number
  site_domain: string
  site_name: string
  region: string
  status: string
  crawled: string
}

type ListEnvelope<T> = { ok: boolean; message: string; items: T[]; total: number }
type ResultEnvelope = { ok: boolean; message: string; [key: string]: unknown }

export type BackfillBasisResult = {
  ok: boolean
  message: string
  receipt: OrphanReceipt
  backfills: SiteBackfill[]
  booked: boolean
  owner: string
}

export type TrendResult = {
  ok: boolean
  message: string
  site: string
  site_name: string
  crawled: string
  parsed: string
  output: string
  status: string
  traceable_sources: number
}

export type TraceResult = {
  ok: boolean
  message: string
  ledger: MetricsLedger
  receipt: OrphanReceipt | null
  round: ExecutionRound | null
  task: {
    id: number
    任务名称: string
    目标站点: string
    status: string
  } | null
}

// ---- 执行回执 ----

export async function listReceipts(status?: string): Promise<ListEnvelope<OrphanReceipt>> {
  const query = status ? `?status=${encodeURIComponent(status)}` : ''
  return asJson(await request(`/api/execution/receipts${query}`))
}

export async function listUnresolvedReceipts(): Promise<ListEnvelope<OrphanReceipt>> {
  return asJson(await request('/api/execution/receipts/unresolved'))
}

export async function correctReceipt(
  receiptId: number,
  payload: { round_id: number; operator: string; evidence: string },
): Promise<ResultEnvelope> {
  return asJson(
    await request(`/api/execution/receipts/${receiptId}/correct`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  )
}

export async function continueReceipt(
  receiptId: number,
  operator: string,
): Promise<ResultEnvelope> {
  return asJson(
    await request(`/api/execution/receipts/${receiptId}/continue`, {
      method: 'POST',
      body: JSON.stringify({ operator }),
    }),
  )
}

export async function getBackfillBasis(receiptCode: string): Promise<BackfillBasisResult> {
  return asJson(await request(`/api/execution/receipts/${receiptCode}/backfill-basis`))
}

export async function listBackfills(receiptCode?: string): Promise<ListEnvelope<SiteBackfill>> {
  const query = receiptCode ? `?receipt_code=${encodeURIComponent(receiptCode)}` : ''
  return asJson(await request(`/api/execution/backfills${query}`))
}

// ---- 执行轮次 ----

export async function listRounds(planId?: number): Promise<ListEnvelope<ExecutionRound>> {
  const query = planId ? `?plan_id=${planId}` : ''
  return asJson(await request(`/api/execution/rounds${query}`))
}

// ---- 指标治理 ----

export async function listLedger(site?: string): Promise<ListEnvelope<MetricsLedger>> {
  const query = site ? `?site=${encodeURIComponent(site)}` : ''
  return asJson(await request(`/api/metrics-governance/ledger${query}`))
}

export async function traceLedger(ledgerId: number): Promise<TraceResult> {
  return asJson(await request(`/api/metrics-governance/ledger/${ledgerId}/trace`))
}

export async function getTrend(site: string): Promise<TrendResult> {
  return asJson(
    await request(`/api/metrics-governance/trend?site=${encodeURIComponent(site)}`),
  )
}

export async function listMetricSites(): Promise<ListEnvelope<MetricSite>> {
  return asJson(await request('/api/metrics-governance/sites'))
}

// ---- 场景复位 ----

export async function resetScenario(): Promise<ResultEnvelope> {
  return asJson(
    await request('/api/execution/scenario/reset', { method: 'POST', body: '{}' }),
  )
}
