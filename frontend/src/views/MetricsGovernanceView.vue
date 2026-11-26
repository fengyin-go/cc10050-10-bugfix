<template>
  <section class="page" data-module="metrics_governance">
    <header class="page-head">
      <div>
        <h2>采集量指标治理</h2>
        <p class="page-desc">
          站点抓取指标只消费可追溯到执行来源（执行轮次/回执/任务）的上报；
          无法确定站点的回执不会显示为空站点或默认站点抓取。点击站点名称可打开实际站点档案。
        </p>
      </div>
      <div class="page-actions">
        <RouterLink class="btn" to="/receipt_governance">打开执行回执归属治理</RouterLink>
      </div>
    </header>

    <p v-if="banner" class="result-banner" :class="bannerOk ? 'ok' : 'err'">{{ banner }}</p>

    <h3 class="section-title">站点指标（点击站点名称打开实际站点）</h3>
    <table class="data-table" data-testid="metrics-table">
      <thead>
        <tr>
          <th>站点</th><th>区域</th><th>抓取量</th><th>解析量</th><th>产出量</th>
          <th>状态</th><th>可追溯来源数</th><th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in sites" :key="row.metric_id" :data-testid="`metric-row-${row.metric_id}`">
          <td>
            <RouterLink
              class="link"
              data-testid="open-site"
              :to="`/shop_site?${encodeURIComponent('站点域名')}=${encodeURIComponent(row.site_domain)}`"
            >
              {{ row.site_name }}（{{ row.site_domain }}）
            </RouterLink>
          </td>
          <td>{{ row.region }}</td>
          <td>{{ row.crawled }}</td>
          <td>{{ metricParsed(row.site_domain) }}</td>
          <td>{{ metricOutput(row.site_domain) }}</td>
          <td><span class="badge" :class="row.status === '正常' ? 'ok' : 'warn'">{{ row.status }}</span></td>
          <td>{{ ledgerCount(row.site_domain) }}</td>
          <td class="row-actions">
            <button class="link" data-testid="view-trend" type="button" @click="onTrend(row.site_domain)">
              查看趋势
            </button>
          </td>
        </tr>
        <tr v-if="!sites.length">
          <td colspan="8" class="empty-state">暂无可追溯的真实站点指标</td>
        </tr>
      </tbody>
    </table>

    <h3 class="section-title">站点抓取指标来源台账（每条抓取量可追溯到执行来源）</h3>
    <table class="data-table" data-testid="ledger-table">
      <thead>
        <tr>
          <th>入账时间</th><th>站点</th><th>抓取增量</th><th>来源类型</th>
          <th>回执编号</th><th>执行轮次</th><th>任务ID</th><th>操作人</th><th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in ledger" :key="row.id" :data-testid="`ledger-row-${row.id}`">
          <td>{{ formatTime(row.入账时间) }}</td>
          <td>{{ row.站点显示名 }}（{{ row.站点名称 }}）</td>
          <td>{{ row.抓取增量 }}</td>
          <td>{{ sourceLabel(row.来源类型) }}</td>
          <td>{{ row.回执编号 || '—' }}</td>
          <td>{{ row.执行轮次id ?? '—' }}</td>
          <td>{{ row.任务id ?? '—' }}</td>
          <td>{{ row.操作人 || '—' }}</td>
          <td class="row-actions">
            <button class="link" data-testid="trace-source" type="button" @click="onTrace(row.id)">
              追溯来源
            </button>
          </td>
        </tr>
        <tr v-if="!ledger.length">
          <td colspan="9" class="empty-state">暂无入账台账（无法确定站点的回执不会进入这里）</td>
        </tr>
      </tbody>
    </table>

    <div v-if="trendDetail" class="modal-backdrop" data-testid="trend-modal">
      <div class="modal">
        <h3>站点趋势：{{ trendDetail.site_name }}</h3>
        <dl class="detail-list">
          <dt>站点域名</dt><dd>{{ trendDetail.site }}</dd>
          <dt>抓取量</dt><dd data-testid="trend-crawled">{{ trendDetail.crawled }}</dd>
          <dt>解析量</dt><dd>{{ trendDetail.parsed }}</dd>
          <dt>产出量</dt><dd>{{ trendDetail.output }}</dd>
          <dt>健康状态</dt><dd>{{ trendDetail.status }}</dd>
          <dt>可追溯来源</dt><dd>{{ trendDetail.traceable_sources }} 条</dd>
        </dl>
        <div class="page-actions">
          <button class="btn primary" type="button" @click="trendDetail = null">关闭</button>
        </div>
      </div>
    </div>

    <div v-if="traceDetail" class="modal-backdrop" data-testid="trace-modal">
      <div class="modal">
        <h3>来源追溯：台账 {{ traceDetail.ledger.id }}</h3>
        <dl class="detail-list">
          <dt>来源标识</dt><dd>{{ traceDetail.ledger.来源标识 }}</dd>
          <dt>站点</dt><dd>{{ traceDetail.ledger.站点显示名 }}（{{ traceDetail.ledger.站点名称 }}）</dd>
          <dt>执行回执</dt>
          <dd>{{ traceDetail.receipt ? `${traceDetail.receipt.回执编号}｜${traceDetail.receipt.归属状态}` : '—' }}</dd>
          <dt>执行轮次</dt>
          <dd>
            <template v-if="traceDetail.round">
              {{ traceDetail.round.轮次编号 }}｜原任务{{ traceDetail.round.任务id }}｜{{ traceDetail.round.来源站点名称 }}
            </template>
            <template v-else>—</template>
          </dd>
          <dt>执行任务</dt>
          <dd>
            <template v-if="traceDetail.task">
              {{ traceDetail.task.任务名称 }}（ID {{ traceDetail.task.id }}）→{{ traceDetail.task.目标站点 }}
            </template>
            <template v-else>—</template>
          </dd>
        </dl>
        <div class="page-actions">
          <button class="btn primary" type="button" @click="traceDetail = null">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  getTrend,
  listLedger,
  listMetricSites,
  traceLedger,
} from '@/api/governance-service'
import type { MetricSite, MetricsLedger, TraceResult, TrendResult } from '@/api/governance-service'
import { listEntries } from '@/api/backend-service'

const sites = ref<MetricSite[]>([])
const ledger = ref<MetricsLedger[]>([])
const metricRows = ref<{ 站点名称: string; 解析量: string; 产出量: string }[]>([])
const banner = ref('')
const bannerOk = ref(true)
const trendDetail = ref<TrendResult | null>(null)
const traceDetail = ref<TraceResult | null>(null)

function sourceLabel(type: string): string {
  const labels: Record<string, string> = {
    execution_receipt: '执行回执补记',
    execution_round: '调度执行轮次',
    crawl_schedule: '调度计划上报',
    price_crawler: '爬取任务上报',
  }
  return labels[type] ?? type
}

function formatTime(value: string): string {
  return value ? value.replace('T', ' ').slice(0, 19) : '—'
}

function ledgerCount(site: string): number {
  return ledger.value.filter((r) => r.站点名称 === site).length
}

function metricParsed(site: string): string {
  return metricRows.value.find((r) => r.站点名称 === site)?.解析量 ?? '—'
}

function metricOutput(site: string): string {
  return metricRows.value.find((r) => r.站点名称 === site)?.产出量 ?? '—'
}

function showBanner(message: string, ok: boolean) {
  banner.value = message
  bannerOk.value = ok
}

async function reload() {
  const [siteList, ledgerList, metricList] = await Promise.all([
    listMetricSites(),
    listLedger(),
    listEntries('collect_metrics'),
  ])
  sites.value = siteList.items
  ledger.value = ledgerList.items
  metricRows.value = metricList.items as never
}

async function onTrend(site: string) {
  const result = await getTrend(site)
  if (!result.ok) {
    showBanner(result.message, false)
    return
  }
  trendDetail.value = result
}

async function onTrace(ledgerId: number) {
  traceDetail.value = await traceLedger(ledgerId)
}

onMounted(reload)
</script>

<style scoped>
.section-title { font-size: 15px; margin: 18px 0 8px; }
.result-banner { padding: 8px 12px; border-radius: 6px; font-size: 13px; }
.result-banner.ok { background: #ecfdf3; border: 1px solid #22c55e; color: #166534; }
.result-banner.err { background: #fef3f2; border: 1px solid #ef4444; color: #b42318; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; background: #e2e8f0; }
.badge.ok { background: #dcfce7; color: #166534; }
.badge.warn { background: #fef3c7; color: #92400e; }
.detail-list { display: grid; grid-template-columns: 120px 1fr; gap: 6px 12px; font-size: 13px; }
.detail-list dt { color: var(--muted); }
.detail-list dd { margin: 0; }
</style>
