<template>
  <section class="page" data-module="effluent">
    <header class="page-head">
      <div>
        <h2>出水监测管理</h2>
        <p class="page-desc">维护出水记录，围绕监测编号、采样时间、出水流量、化学需氧量做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记出水记录</button>
        <button class="btn" type="button" @click="exportRows">导出出水监测清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ formatStat(item) }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无出水监测数据，可先登记出水记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条出水监测记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type Stats = { today_flow: number; pass_rate: number; exceeded: number }

const ENDPOINT = '/api/effluent'
const columns = ["监测编号", "采样时间", "出水流量", "化学需氧量", "氨氮浓度", "总磷浓度", "达标判定", "监测状态"]
const actions = ["开始检测", "判定达标", "标记超标"]
const statuses = ["待检测", "检测中", "已达标", "已超标"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const stats = ref<{ label: string; key: keyof Stats; unit: string }[]>([
  { label: "今日出水量", key: "today_flow", unit: "m³" },
  { label: "达标率", key: "pass_rate", unit: "%" },
  { label: "超标次数", key: "exceeded", unit: "次" },
])
const statsData = ref<Stats>({ today_flow: 0, pass_rate: 0, exceeded: 0 })

function formatStat(item: { key: keyof Stats; unit: string }): string {
  const value = statsData.value[item.key]
  return item.unit === '%' ? `${value}%` : `${value}${item.unit}`
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '出水记录登记入口尚未接入审批流'
}

async function loadStats() {
  try {
    const response = await request(`${ENDPOINT}/stats`)
    if (!response.ok) {
      throw new Error('统计数据读取失败')
    }
    statsData.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '出水监测统计读取失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('出水监测动作未生效，请稍后重试')
    }
    // 后端以 HTTP 200 + ok=false 返回业务失败（如 COD 为空/超量程），
    // 必须读出原因并保留页面数据，方便补测后重试；状态未变更，无需刷新列表
    const payload = await response.json()
    if (!payload.ok) {
      errorMessage.value = payload.message || '出水监测动作未生效，请稍后重试'
      return
    }
    await Promise.all([reload(), loadStats()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '出水监测操作失败，请稍后重试'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('出水记录列表读取失败，请稍后重试')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '出水监测列表读取失败，请稍后重试'
  }
}

onMounted(() => {
  void reload()
  void loadStats()
})
</script>
