import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'
import ReceiptGovernanceView from '@/views/ReceiptGovernanceView.vue'
import MetricsGovernanceView from '@/views/MetricsGovernanceView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    { path: '/receipt_governance', name: 'receipt-governance', component: ReceiptGovernanceView },
    { path: '/metrics_governance', name: 'metrics-governance', component: MetricsGovernanceView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
