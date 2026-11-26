<template>
  <section class="page" data-module="receipt_governance">
    <header class="page-head">
      <div>
        <h2>执行回执归属治理</h2>
        <p class="page-desc">
          调度执行回执失去关联时挂起并保留原因，凭原执行轮次补核归属后再继续入账；
          同名任务不作为归属依据，同一回执重复提交只增加一次抓取量。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn ghost" data-testid="reset-scenario" type="button" @click="resetAll">
          复位复现场景（计划39 / 北站5 / 同名南站）
        </button>
        <RouterLink class="btn" to="/metrics_governance">打开采集量指标治理</RouterLink>
      </div>
    </header>

    <p v-if="banner" class="result-banner" :class="bannerOk ? 'ok' : 'err'" data-testid="banner">
      {{ banner }}
    </p>

    <h3 class="section-title">待归属回执（无法确定站点，保留原因）</h3>
    <table class="data-table" data-testid="unresolved-table">
      <thead>
        <tr>
          <th>回执编号</th><th>任务名称</th><th>原执行站点</th><th>抓取量</th>
          <th>重投次数</th><th>归属状态</th><th>无法确定站点的原因</th><th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in unresolved" :key="row.id" :data-testid="`unresolved-row-${row.id}`">
          <td>{{ row.回执编号 }}</td>
          <td>{{ row.任务名称 }}</td>
          <td>{{ row.原执行站点名称 || '—' }}</td>
          <td>{{ row.抓取量 }}</td>
          <td>{{ row.重投次数 }}</td>
          <td><span class="badge warn">{{ row.归属状态 }}</span></td>
          <td class="reason-cell">{{ row.无法确定原因 }}</td>
          <td class="row-actions">
            <button class="link" data-testid="open-correct" type="button" @click="openCorrect(row)">
              补正关联
            </button>
            <button class="link" type="button" @click="onContinue(row)">继续</button>
          </td>
        </tr>
        <tr v-if="!unresolved.length">
          <td colspan="8" class="empty-state">没有无法确定站点的回执</td>
        </tr>
      </tbody>
    </table>

    <h3 class="section-title">全部执行回执</h3>
    <table class="data-table" data-testid="receipt-table">
      <thead>
        <tr>
          <th>回执编号</th><th>执行轮次</th><th>任务名称</th><th>原执行站点</th>
          <th>抓取量</th><th>归属状态</th><th>补记状态</th><th>补记人</th><th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in receipts" :key="row.id" :data-testid="`receipt-row-${row.id}`" :data-status="row.归属状态">
          <td>{{ row.回执编号 }}</td>
          <td>{{ roundCode(row.执行轮次id) }}</td>
          <td>{{ row.任务名称 }}</td>
          <td>{{ row.原执行站点名称 || '—' }}</td>
          <td>{{ row.抓取量 }}</td>
          <td>
            <span class="badge" :class="badgeClass(row.归属状态)">{{ row.归属状态 }}</span>
          </td>
          <td>{{ row.补记状态 }}</td>
          <td>{{ row.补记人 || '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="openCorrect(row)">补正关联</button>
            <button class="link" data-testid="continue-receipt" type="button" @click="onContinue(row)">
              继续
            </button>
            <button class="link" type="button" @click="showBasis(row)">补记依据</button>
          </td>
        </tr>
      </tbody>
    </table>

    <h3 class="section-title">原执行轮次（归属核对的可信来源）</h3>
    <table class="data-table" data-testid="round-table">
      <thead>
        <tr>
          <th>轮次编号</th><th>计划ID</th><th>原任务ID</th><th>任务名称</th>
          <th>原执行站点</th><th>轮次抓取量</th><th>执行时间</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rounds" :key="row.id" :data-testid="`round-row-${row.id}`">
          <td>{{ row.轮次编号 }}</td>
          <td>{{ row.计划id }}</td>
          <td>{{ row.任务id }}</td>
          <td>{{ row.任务名称 }}</td>
          <td>{{ row.来源站点名称 }}（{{ row.来源站点 }}）</td>
          <td>{{ row.抓取量 }}</td>
          <td>{{ formatTime(row.执行时间) }}</td>
        </tr>
      </tbody>
    </table>

    <h3 class="section-title">站点补记单</h3>
    <table class="data-table" data-testid="backfill-table">
      <thead>
        <tr>
          <th>补记单号</th><th>回执编号</th><th>执行轮次</th><th>归属站点</th>
          <th>补记人</th><th>补记依据</th><th>入账状态</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in backfills" :key="row.id" :data-testid="`backfill-row-${row.id}`">
          <td>{{ row.补记单号 }}</td>
          <td>{{ row.回执编号 }}</td>
          <td>{{ row.轮次编号 }}</td>
          <td>{{ row.站点显示名 }}（{{ row.站点名称 }}）</td>
          <td>{{ row.补记人 }}</td>
          <td class="reason-cell">{{ row.补记依据 }}</td>
          <td><span class="badge" :class="badgeClass(row.入账状态)">{{ row.入账状态 }}</span></td>
        </tr>
        <tr v-if="!backfills.length">
          <td colspan="7" class="empty-state">暂无站点补记单</td>
        </tr>
      </tbody>
    </table>

    <div v-if="correctTarget" class="modal-backdrop" data-testid="correct-modal">
      <form class="modal" @submit.prevent="submitCorrect">
        <h3>补正关联：{{ correctTarget.回执编号 }}</h3>
        <p class="modal-hint">
          必须选择该回执的原执行轮次；系统会核对轮次记录的原任务与站点，
          同名任务指向其他站点时会被排除，不会给同名站点加量。
        </p>
        <label class="create-field">
          <span>原执行轮次</span>
          <select v-model="correctForm.round_id" data-testid="correct-round" required>
            <option v-for="row in rounds" :key="row.id" :value="row.id">
              {{ row.轮次编号 }}｜原任务{{ row.任务id }}｜{{ row.来源站点名称 }}
            </option>
          </select>
        </label>
        <label class="create-field">
          <span>补记责任人</span>
          <input v-model="correctForm.operator" data-testid="correct-operator" required />
        </label>
        <label class="create-field">
          <span>补记依据说明</span>
          <input v-model="correctForm.evidence" data-testid="correct-evidence" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="correctTarget = null">取消</button>
          <button class="btn primary" data-testid="submit-correct" type="submit">确认补正（先不入账）</button>
        </div>
      </form>
    </div>

    <div v-if="basisDetail" class="modal-backdrop" data-testid="basis-modal">
      <div class="modal">
        <h3>补记依据：{{ basisDetail.receipt.回执编号 }}</h3>
        <dl class="detail-list">
          <dt>归属状态</dt><dd>{{ basisDetail.receipt.归属状态 }}</dd>
          <dt>原执行站点</dt><dd>{{ basisDetail.receipt.原执行站点名称 || '—' }}</dd>
          <dt>补记责任人</dt><dd>{{ basisDetail.owner || '—' }}</dd>
          <dt>是否已入账</dt><dd>{{ basisDetail.booked ? '已入账（重复提交不再加量）' : '待原回执继续' }}</dd>
          <dt v-for="(item, idx) in basisDetail.backfills" :key="idx">补记单 {{ item.补记单号 }}</dt>
          <dd v-for="(item, idx) in basisDetail.backfills" :key="`d-${idx}`">
            {{ item.补记依据 }}<br />
            入账状态：{{ item.入账状态 }}｜入账标识：{{ item.入账标识 || '—' }}
          </dd>
        </dl>
        <div class="page-actions">
          <button class="btn primary" type="button" @click="basisDetail = null">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  continueReceipt,
  correctReceipt,
  getBackfillBasis,
  listBackfills,
  listReceipts,
  listRounds,
  listUnresolvedReceipts,
  resetScenario,
} from '@/api/governance-service'
import type {
  BackfillBasisResult,
  ExecutionRound,
  OrphanReceipt,
  SiteBackfill,
} from '@/api/governance-service'

const receipts = ref<OrphanReceipt[]>([])
const unresolved = ref<OrphanReceipt[]>([])
const rounds = ref<ExecutionRound[]>([])
const backfills = ref<SiteBackfill[]>([])
const banner = ref('')
const bannerOk = ref(true)
const correctTarget = ref<OrphanReceipt | null>(null)
const correctForm = ref({ round_id: 0, operator: '值班员', evidence: '' })
const basisDetail = ref<BackfillBasisResult | null>(null)

function roundCode(roundId: number | null): string {
  if (roundId === null || roundId === undefined) return '—'
  return rounds.value.find((r) => r.id === roundId)?.轮次编号 ?? `轮次${roundId}`
}

function badgeClass(status: string): string {
  if (status === '已入账' || status === '补记已入账') return 'ok'
  if (status === '已确认归属' || status === '已补记待重投') return 'info'
  return 'warn'
}

function formatTime(value: string): string {
  return value ? value.replace('T', ' ').slice(0, 19) : '—'
}

function showBanner(message: string, ok: boolean) {
  banner.value = message
  bannerOk.value = ok
}

async function reload() {
  const [all, pending, roundList, backfillList] = await Promise.all([
    listReceipts(),
    listUnresolvedReceipts(),
    listRounds(),
    listBackfills(),
  ])
  receipts.value = all.items
  unresolved.value = pending.items
  rounds.value = roundList.items
  backfills.value = backfillList.items
}

function openCorrect(row: OrphanReceipt) {
  correctTarget.value = row
  correctForm.value = {
    round_id: row.执行轮次id ?? rounds.value[0]?.id ?? 0,
    operator: '值班员',
    evidence: '',
  }
}

async function submitCorrect() {
  if (!correctTarget.value) return
  const result = await correctReceipt(correctTarget.value.id, { ...correctForm.value })
  showBanner(result.message, result.ok)
  correctTarget.value = null
  await reload()
}

async function onContinue(row: OrphanReceipt) {
  const result = await continueReceipt(row.id, '值班员')
  showBanner(result.message, result.ok)
  await reload()
}

async function showBasis(row: OrphanReceipt) {
  basisDetail.value = await getBackfillBasis(row.回执编号)
}

async function resetAll() {
  const result = await resetScenario()
  showBanner(result.message, result.ok)
  await reload()
}

onMounted(reload)
</script>

<style scoped>
.section-title { font-size: 15px; margin: 18px 0 8px; }
.reason-cell { max-width: 320px; color: #b42318; font-size: 12px; }
.result-banner { padding: 8px 12px; border-radius: 6px; font-size: 13px; }
.result-banner.ok { background: #ecfdf3; border: 1px solid #22c55e; color: #166534; }
.result-banner.err { background: #fef3f2; border: 1px solid #ef4444; color: #b42318; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; background: #e2e8f0; }
.badge.ok { background: #dcfce7; color: #166534; }
.badge.info { background: #dbeafe; color: #1e40af; }
.badge.warn { background: #fef3c7; color: #92400e; }
.modal-hint { font-size: 12px; color: var(--muted); background: #f8fafc; padding: 8px; border-radius: 6px; }
.detail-list { display: grid; grid-template-columns: 120px 1fr; gap: 6px 12px; font-size: 13px; }
.detail-list dt { color: var(--muted); }
.detail-list dd { margin: 0; }
</style>
