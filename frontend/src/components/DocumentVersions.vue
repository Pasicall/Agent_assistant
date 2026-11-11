<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api, downloadDocument, type Chunk, type Document, type Job } from '../api'
import { available, dateText, documentStatus, errorText, expired, fileProblem, parseTime, processing, uploadExtensions } from '../workspace'
import WorkspaceDialog from './WorkspaceDialog.vue'

const props = defineProps<{
  document: Document
  maxMb?: number
  restoring: boolean
  onChanged: (doc: Document, jobId?: string, restore?: boolean) => Promise<boolean>
}>()
const emit = defineEmits<{ close: [] }>()
type Version = { id: string; name: string; status: string; active: boolean; created_at: string }
const versions = ref<Version[]>([])
const chunks = ref<Chunk[]>([])
const job = ref<Job>()
const error = ref('')
const notice = ref('')
const loading = ref(false)
const busy = ref(false)
const readError = ref('')
const previewVersion = ref<Version>()
const previewReadAt = ref('')
const currentVersion = computed(() => versions.value.find(version => version.id === props.document.active_revision_id))
const updateInput = ref<HTMLInputElement>()
const expires = ref(localInput(props.document.expires_at))
const labels: Record<string, string> = { ready: '已发布', queued: '等待处理', processing: '处理中', failed: '处理失败' }

function localInput(value?: string) {
  if (!value) return ''
  const date = parseTime(value)
  return Number.isFinite(date.getTime()) ? new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16) : ''
}

let readSequence = 0
async function refresh() {
  const id = ++readSequence
  loading.value = true
  readError.value = ''
  const doc = props.document
  try {
    const nextVersions = await api<Version[]>(`/documents/${doc.id}/versions`)
    const active = nextVersions.find(version => version.active)
    const [nextChunks, nextJob] = await Promise.all([
      active ? api<Chunk[]>(`/documents/${doc.id}/chunks`) : [],
      doc.job_id && processing(doc) ? api<Job>(`/jobs/${doc.job_id}`) : undefined,
    ])
    const confirmed = await api<Version[]>(`/documents/${doc.id}/versions`)
    if (active?.id !== confirmed.find(version => version.active)?.id) throw new Error('活动版本正在切换，请重新读取详情。')
    if (id !== readSequence) return
    versions.value = confirmed
    chunks.value = nextChunks
    previewVersion.value = active
    previewReadAt.value = new Date().toISOString()
    job.value = nextJob
  } catch (reason) { if (id === readSequence) readError.value = errorText(reason) }
  finally { if (id === readSequence) loading.value = false }
}

watch(() => [props.document.active_revision_id, props.document.job_id, props.document.updated_at, props.document.status], refresh, { immediate: true })

async function mutate(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try { await action() } catch (reason) { error.value = errorText(reason) }
  finally { busy.value = false }
}

async function upload(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || !props.maxMb) return
  input.value = ''
  await mutate(async () => {
    const problem = fileProblem(file, props.maxMb!)
    if (problem) throw new Error(problem)
    const doc = props.document
    const form = new FormData()
    form.set('file', file)
    form.set('knowledge_base_id', doc.knowledge_base_id)
    form.set('document_id', doc.id)
    const result = await api<{ document: Document }>('/documents/upload', { method: 'POST', body: form })
    notice.value = '更新文件已接收。'
    if (!await props.onChanged(result.document)) notice.value += '资料列表暂未刷新。'
  })
}

function reprocess(versionId?: string) {
  return mutate(async () => {
    const doc = props.document
    const path = versionId ? `versions/${versionId}/restore` : 'retry'
    const { job_id } = await api<{ job_id: string }>(`/documents/${doc.id}/${path}`, { method: 'POST' })
    notice.value = versionId ? '已提交恢复，后台发布后才会切换版本。' : '已提交重新处理。'
    if (!await props.onChanged({ ...doc, job_id, status: 'queued', error: undefined }, job_id, !!versionId)) notice.value += '资料列表暂未刷新。'
  })
}

function saveExpiry() {
  return mutate(async () => {
    const doc = props.document
    const { expires_at } = await api<{ expires_at?: string }>(`/documents/${doc.id}/metadata`, {
      method: 'PATCH', body: JSON.stringify({ expires_at: expires.value ? new Date(expires.value).toISOString() : null }),
    })
    notice.value = '有效期已保存。'
    if (!await props.onChanged({ ...doc, expires_at })) notice.value += '资料列表暂未刷新。'
  })
}
</script>

<template>
  <WorkspaceDialog :title="document.name" drawer dockable :busy="busy" @close="emit('close')">
    <p>{{ documentStatus(document, restoring) }} · {{ document.source?.startsWith('folder:') ? '目录同步' : '上传' }}</p>
    <p v-if="document.active_revision_id"><strong>当前使用版本</strong> <time v-if="currentVersion">· {{ dateText(currentVersion.created_at) }}</time></p>
    <p v-else class="muted">尚未发布可用版本</p>
    <p v-if="expired(document)">资料已过期，调整有效期后可用于新的问答。</p>
    <p v-else-if="available(document) && (processing(document) || document.error)">已发布的版本仍可用于新的问答。</p>
    <p v-if="job && processing(document)" role="status">{{ job.stage || '等待处理' }} · {{ Math.round(job.progress) }}%
      <span v-if="job.total_pages"> · {{ job.processed_pages || 0 }}/{{ job.total_pages }} 页</span>
    </p>
    <div class="inline-actions">
      <button class="btn secondary" :disabled="busy" @click="mutate(() => downloadDocument(document.id, document.name, document.active_revision_id))">下载原文</button>
    </div>
    <div v-if="document.error" class="alert error-alert">
      <p>{{ document.error }}</p>
      <button v-if="!document.active_revision_id" class="text-button" :disabled="busy || processing(document)" @click="reprocess()">重新处理</button>
      <button class="text-button" :disabled="busy || processing(document) || !maxMb" @click="updateInput?.click()">上传更新文件</button>
    </div>
    <input ref="updateInput" type="file" hidden :accept="uploadExtensions" @change="upload" />
    <p v-if="error" class="alert error-alert" role="alert">{{ error }}</p>
    <p v-if="notice" class="workspace-notice" role="status">{{ notice }}</p>
    <p v-if="readError" class="alert error-alert" role="alert">{{ readError }} <button class="text-button" @click="refresh">重新读取详情</button></p>

    <section>
      <h3>解析文本{{ previewVersion ? ' · ' + previewVersion.name : '' }}</h3>
      <p v-if="previewReadAt" class="muted">最近成功读取于 {{ dateText(previewReadAt) }}{{ previewVersion ? ' · 版本时间 ' + dateText(previewVersion.created_at) : '' }}</p>
      <p v-if="previewReadAt && (loading || readError || previewVersion?.id !== document.active_revision_id)" role="status">
        {{ loading ? '正在读取最新详情。' : '最新详情暂未取得。' }}下方保留最近成功读取的文本。
      </p>
      <p v-if="loading && !chunks.length">正在读取详情…</p>
      <p v-else-if="!chunks.length && !readError">暂无已解析文本。</p>
      <div class="document-excerpts">
        <article v-for="chunk in chunks" :key="chunk.id">
          <small>{{ chunk.page ? `第 ${chunk.page} 页` : `片段 ${chunk.ordinal + 1}` }}</small>
          <p>{{ chunk.text }}</p>
        </article>
      </div>
    </section>

    <details>
      <summary>版本与有效期</summary>
      <div class="action-stack">
        <label v-if="maxMb">上传更新文件<input type="file" :disabled="busy || processing(document)" :accept="uploadExtensions" @change="upload" /></label>
        <button class="btn secondary" :disabled="busy || processing(document)" @click="reprocess()">重新处理</button>
        <form class="action-stack" @submit.prevent="saveExpiry">
          <label>有效期至（本地时间，留空为长期有效）<input v-model="expires" type="datetime-local" /></label>
          <button class="btn secondary" :disabled="busy">保存有效期</button>
        </form>
        <ul class="version-list">
          <li v-for="version in versions" :key="version.id">
            <strong>{{ version.name }}</strong>
            <small>{{ dateText(version.created_at) }} · {{ labels[version.status] || version.status }} {{ version.active ? '· 当前使用' : '' }}</small>
            <code>{{ version.id }}</code>
            <button v-if="!version.active && version.status === 'ready'" class="text-button" :disabled="busy || processing(document)" @click="reprocess(version.id)">恢复此版本</button>
          </li>
        </ul>
      </div>
    </details>
  </WorkspaceDialog>
</template>
