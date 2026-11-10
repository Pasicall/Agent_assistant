<script setup lang="ts">
import { readLocal, writeLocal } from '../storage'
import { computed, ref, toRef, watch } from 'vue'
import { FileText, UploadCloud } from 'lucide-vue-next'
import { api, ApiError, type Capabilities, type Document, type KnowledgeBase } from '../api'
import type { Corpus } from '../api/corpus'
import { available, dateText, documentStatus, errorText, fileProblem, processing, record, uploadExtensions, useVisiblePolling, type Upload, type UploadSession } from '../workspace'
import ConfirmAction from './ConfirmAction.vue'
import DocumentVersions from './DocumentVersions.vue'
import WorkspaceDialog from './WorkspaceDialog.vue'

const props = defineProps<{ corpus: Corpus; capabilities?: Capabilities; active: boolean; uploadSession: UploadSession }>()
const emit = defineEmits<{ ask: [id: string] }>()
const selected = defineModel<string>('selected', { required: true })
const state = props.corpus.state
const selectedKb = computed(() => state.bases.find(kb => kb.id === selected.value))
const docs = computed(() => state.documents.filter(doc => doc.knowledge_base_id === selected.value))
const filtered = computed(() => docs.value.filter(doc => doc.name.toLowerCase().includes(query.value.trim().toLowerCase())))
const query = ref('')
const input = ref<HTMLInputElement>()
const libraryDialog = ref<'upload' | 'create' | 'sync' | ''>('')
const name = ref('')
const path = ref('.')
const localError = ref('')
const notice = ref('')
const dialogError = ref('')
const busy = ref(false)
const uploading = ref(false)
const target = toRef(props.uploadSession, 'target')
const detailId = ref('')
const detail = computed(() => state.documents.find(doc => doc.id === detailId.value))
const deleteTarget = ref<{ kind: 'library' | 'document'; id: string; title: string; description: string }>()
const menu = ref('')
const rowBusy = ref('')
const batch = toRef(props.uploadSession, 'files')
const maxMb = computed(() => props.capabilities?.max_upload_mb)
const replaceQueue = ref(false)
const proposedFiles = ref<File[]>([])
const replacementApproved = ref(false)
const unfinished = computed(() => batch.value.some(item => item.state === 'pending' || item.unknown ||
  (item.state === 'failed' && maxMb.value && !fileProblem(item.file, maxMb.value))))
const batchCounts = computed(() => ({
  pending: batch.value.filter(item => item.state === 'pending').length,
  accepted: batch.value.filter(item => item.state === 'accepted').length,
  unknown: batch.value.filter(item => item.unknown).length,
  failed: batch.value.filter(item => item.state === 'failed' && !item.unknown).length,
}))
const waitingForFiles = computed(() => !uploading.value && !busy.value && !!maxMb.value && state.loaded)

watch(() => state.bases, bases => {
  if (!bases.some(kb => kb.id === selected.value)) selected.value = bases.find(kb => kb.id === target.value)?.id || bases[0]?.id || ''
})
watch(selected, () => { query.value = ''; menu.value = ''; localError.value = '' })
useVisiblePolling(toRef(props, 'active'), () => docs.value.some(processing),
  async () => {
    if (!busy.value && !uploading.value && !rowBusy.value && !deleteTarget.value && !state.loading) await props.corpus.refresh()
  })

function requestUpload() {
  if (unfinished.value) { proposedFiles.value = []; replaceQueue.value = true }
  else input.value?.click()
}

function approveReplacement() {
  replaceQueue.value = false
  replacementApproved.value = true
  if (proposedFiles.value.length) beginBatch(proposedFiles.value)
  else input.value?.click()
}

function keepQueue() {
  replaceQueue.value = false
  proposedFiles.value = []
  replacementApproved.value = false
}

function chooseFiles(event: Event) {
  const field = event.target as HTMLInputElement
  beginBatch(Array.from(field.files || []))
  field.value = ''
  replacementApproved.value = false
}

function beginBatch(files: File[]) {
  if (!files.length || !waitingForFiles.value || !maxMb.value) return
  const items: Upload[] = files.map(file => {
    const detail = fileProblem(file, maxMb.value!)
    return { file, state: detail ? 'failed' : 'pending', detail }
  })
  if (!items.some(item => item.state === 'pending')) {
    localError.value = '所选文件均无法上传，请更换文件。' + items.map(item => `${item.file.name} · ${item.detail}`).join('；')
    return
  }
  if (unfinished.value && !replacementApproved.value) {
    proposedFiles.value = files
    replaceQueue.value = true
    return
  }
  replacementApproved.value = false
  proposedFiles.value = []
  target.value = selected.value
  localError.value = ''
  notice.value = ''
  batch.value = items
  if (target.value) void sendBatch()
  else showDialog('upload')
}

function showDialog(type: typeof libraryDialog.value) {
  dialogError.value = ''
  name.value = ''
  path.value = '.'
  libraryDialog.value = type
  menu.value = ''
}

async function createLibrary() {
  if (!name.value.trim() || busy.value) return
  const uploadingAfter = libraryDialog.value === 'upload'
  busy.value = true
  dialogError.value = ''
  try {
    const base = await api<KnowledgeBase>('/knowledge-bases', { method: 'POST', body: JSON.stringify({ name: name.value.trim() }) })
    props.corpus.invalidate()
    state.bases = [...state.bases, { ...base, document_count: base.document_count ?? 0 }]
    selected.value = base.id
    libraryDialog.value = ''
    if (uploadingAfter) {
      target.value = base.id
      await sendBatch()
    } else notice.value = '知识库已创建。'
  } catch (error) { dialogError.value = errorText(error) }
  finally { busy.value = false }
}

async function reconcile(item: Upload, targetId: string) {
  if (!await props.corpus.refresh()) throw new ApiError('无法核对上传结果，请恢复连接后重试。', 0)
  const hash = await crypto.subtle.digest('SHA-256', await item.file.arrayBuffer())
  const digest = Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, '0')).join('')
  return state.documents.some(doc => doc.knowledge_base_id === targetId && doc.sha256 === digest)
}

async function sendBatch(only?: Upload) {
  if (uploading.value || !target.value || !maxMb.value) return
  const targetId = target.value
  if (!state.bases.some(base => base.id === targetId)) { localError.value = '目标知识库已不可用，请重新选择文件和目标库。'; return }
  uploading.value = true
  localError.value = ''
  try {
    for (const item of only ? [only] : batch.value.filter(row => row.state === 'pending')) {
      if (fileProblem(item.file, maxMb.value)) continue
      item.state = 'sending'
      try {
        if (item.unknown && await reconcile(item, targetId)) {
          item.state = 'accepted'
          item.detail = '已在目标库找到相同内容'
          item.unknown = false
          continue
        }
        const form = new FormData()
        form.set('file', item.file)
        form.set('knowledge_base_id', targetId)
        const result = await api<{ document: Document; duplicate: boolean }>('/documents/upload', { method: 'POST', body: form })
        props.corpus.putDocument(result.document)
        item.state = 'accepted'
        item.detail = result.duplicate ? '目标库已有相同内容' : '后台将处理文件'
        item.unknown = false
      } catch (error) {
        item.state = 'failed'
        item.detail = errorText(error)
        const status = error instanceof ApiError ? error.status : undefined
        item.unknown = status === 0 || status === 408 || (status !== undefined && status >= 500) || status === undefined
        if (item.unknown || status === 401) {
          if (item.unknown) item.detail = '提交结果待核对，重试时会先检查目标库。'
          localError.value = '批次已暂停，剩余文件仍在队列中。'
          if (status === 401 || (error instanceof DOMException && error.name === 'AbortError')) return
          break
        }
      }
    }
    if (batch.value.some(item => item.state === 'accepted') && !await props.corpus.refresh()) notice.value = '文件已接收，资料列表暂未刷新。'
    record('upload_batch_finished', {
      accepted: batch.value.filter(item => item.state === 'accepted').length,
      failed: batch.value.filter(item => item.state === 'failed').length,
    })
  } finally { uploading.value = false }
}

async function sync() {
  const id = selected.value
  if (!id || !path.value.trim() || busy.value) return
  busy.value = true
  dialogError.value = ''
  try {
    const result = await api<{ files: { unchanged: boolean }[] }>('/sources/sync', {
      method: 'POST', body: JSON.stringify({ knowledge_base_id: id, relative_path: path.value.trim() }),
    })
    props.corpus.invalidate()
    libraryDialog.value = ''
    notice.value = `已检查 ${result.files.length} 份资料，${result.files.filter(file => !file.unchanged).length} 份已提交处理。`
    if (!await props.corpus.refresh()) notice.value += '资料列表暂未刷新。'
  } catch (error) { dialogError.value = errorText(error) }
  finally { busy.value = false }
}

function requestDelete(kind: 'library' | 'document', item: KnowledgeBase | Document) {
  deleteTarget.value = {
    kind, id: item.id, title: `删除「${item.name}」`,
    description: kind === 'library'
      ? `将删除此知识库及其中的 ${(item as KnowledgeBase).document_count} 份资料。关联资料将无法检索或用于学习。删除无法撤销。`
      : '将停止该资料的处理任务并移除所有版本，关联引用和学习任务可能不可用。删除无法撤销。',
  }
}

async function remove() {
  const item = deleteTarget.value
  if (!item) return
  const library = item.kind === 'library'
  await api(`/${library ? 'knowledge-bases' : 'documents'}/${item.id}`, { method: 'DELETE' })
  props.corpus.invalidate()
  if (library) state.bases = state.bases.filter(kb => kb.id !== item.id)
  else if (selectedKb.value) selectedKb.value.document_count = Math.max(0, selectedKb.value.document_count - 1)
  state.documents = state.documents.filter(doc => library ? doc.knowledge_base_id !== item.id : doc.id !== item.id)
  menu.value = ''
  notice.value = await props.corpus.refresh() ? '已删除。' : '已删除，列表暂未刷新。'
}

async function retry(doc: Document) {
  if (rowBusy.value) return
  rowBusy.value = doc.id
  localError.value = ''
  try {
    const { job_id } = await api<{ job_id: string }>(`/documents/${doc.id}/retry`, { method: 'POST' })
    props.corpus.putDocument({ ...doc, job_id, status: 'queued', error: undefined })
    notice.value = await props.corpus.refresh() ? '已提交处理。' : '已提交处理，列表暂未刷新。'
  } catch (error) { localError.value = errorText(error) }
  finally { rowBusy.value = '' }
}

async function changed(doc: Document, jobId?: string, restore = false) {
  props.corpus.putDocument(doc)
  if (restore && jobId) writeLocal(restoreKey(doc.id), jobId)
  return props.corpus.refresh()
}

const restoreKey = (id: string) => `document-restore:${props.uploadSession.owner}:${id}`
function isRestoring(doc: Document) {
  return processing(doc) && doc.job_id === readLocal(restoreKey(doc.id))
}
</script>

<template>
  <main class="page library-page">
    <div class="page-heading">
      <h1>资料库</h1>
      <button v-if="docs.length" class="btn primary" :disabled="!waitingForFiles" @click="requestUpload"><UploadCloud :size="16" />上传资料</button>
    </div>
    <input ref="input" type="file" hidden multiple :accept="uploadExtensions" @change="chooseFiles" @cancel="replacementApproved = false" />
    <div v-if="state.error" class="alert error-alert" role="alert"><span>资料读取失败。{{ state.error }}</span> <button class="text-button" @click="corpus.refresh()">重试</button></div>
    <p v-if="!state.loaded && !state.error" role="status">正在读取资料库…</p>
    <template v-if="state.loaded">
      <div class="library-toolbar">
        <label>当前知识库
          <select v-model="selected" :disabled="uploading || busy || !state.bases.length">
            <option v-if="!state.bases.length" value="">尚无知识库</option>
            <option v-for="kb in state.bases" :key="kb.id" :value="kb.id">{{ kb.name }}</option>
          </select>
        </label>
        <details :open="menu === 'library'" @toggle="($event.target as HTMLDetailsElement).open ? menu = 'library' : menu === 'library' && (menu = '')">
          <summary>知识库更多操作</summary>
          <div class="action-stack">
            <button class="text-button" :disabled="uploading || busy" @click="showDialog('create')">新建知识库</button>
            <button v-if="selectedKb && capabilities?.folder_sync_available" class="text-button" :disabled="uploading || busy" @click="showDialog('sync')">从目录同步</button>
            <button v-if="selectedKb" class="text-button danger-button" :disabled="uploading || busy" @click="requestDelete('library', selectedKb)">删除此知识库</button>
          </div>
        </details>
        <button v-if="docs.some(available)" class="btn secondary" @click="emit('ask', selected)">基于此库提问</button>
        <p v-else-if="docs.some(processing)" role="status">资料正在处理，显示可用后即可基于此库提问。</p>
      </div>

      <p v-if="localError" class="alert error-alert" role="alert">{{ localError }}</p>
      <p v-if="notice" class="workspace-notice" role="status">{{ notice }}</p>

      <section v-if="!docs.length" class="panel library-empty" @dragover.prevent @drop.prevent="beginBatch(Array.from($event.dataTransfer?.files || []))">
        <h2>{{ selectedKb ? '这个知识库还没有资料' : '添加第一份资料' }}</h2>
        <p>{{ selectedKb ? '可将文件拖到这里，或从设备选择。' : '选择文件后，可以在同一步创建目标知识库。' }}</p>
        <button class="btn primary" :disabled="!waitingForFiles" @click="requestUpload"><UploadCloud :size="16" />上传资料</button>
        <p>上传后会自动处理，显示可用后即可提问。</p>
        <small v-if="maxMb">单文件最多 {{ maxMb }} MB，支持 PDF、Word、文本和图片等格式。</small>
      </section>

      <section v-if="batch.length" class="panel upload-queue" aria-label="批量上传" aria-live="polite">
        <div class="section-heading">
          <h2>本次上传 <small v-if="target">· {{ state.bases.find(kb => kb.id === target)?.name || '目标库已不可用' }}</small></h2>
          <button v-if="!uploading && batch.some(item => item.state === 'pending')" class="text-button" @click="target ? sendBatch() : showDialog('upload')">继续待提交文件</button>
        </div>
        <p>待提交 {{ batchCounts.pending }} · 已接收 {{ batchCounts.accepted }} · 待核对 {{ batchCounts.unknown }} · 失败 {{ batchCounts.failed }}</p>
        <p class="muted">已接收的文件会继续处理，可用状态请查看资料列表。</p>
        <ul>
          <li v-for="(item, index) in batch" :key="index">
            <strong>{{ item.file.name }}</strong>
            <span>{{ { pending: '待提交', sending: '提交中', accepted: '已接收', failed: '失败' }[item.state] }}{{ item.detail ? ' · ' + item.detail : '' }}</span>
            <button v-if="item.state === 'failed' && maxMb && !fileProblem(item.file, maxMb)" class="text-button" :disabled="uploading" @click="sendBatch(item)">{{ item.unknown ? '核对并重试' : '重试此文件' }}</button>
          </li>
        </ul>
      </section>

      <section v-if="docs.length" class="panel library-list" @dragover.prevent @drop.prevent="beginBatch(Array.from($event.dataTransfer?.files || []))">
        <div class="section-heading">
          <h2>当前资料</h2>
          <input v-model="query" type="search" aria-label="搜索资料名称" placeholder="搜索资料名称" />
        </div>
        <p v-if="!filtered.length" class="muted">没有匹配的资料。 <button class="text-button" @click="query = ''">清除搜索</button></p>
        <ul>
          <li v-for="doc in filtered" :key="doc.id" class="library-row">
            <button class="text-button file-name" @click="detailId = doc.id"><FileText :size="16" />{{ doc.name }}</button>
            <span>{{ documentStatus(doc, isRestoring(doc)) }}
              <button v-if="doc.status === 'failed'" class="text-button" :disabled="!!rowBusy" @click="retry(doc)">重试</button>
              <button v-else-if="doc.status === 'ready' && doc.error" class="text-button" @click="detailId = doc.id">查看原因</button>
            </span>
            <time :datetime="doc.updated_at || doc.created_at">{{ dateText(doc.updated_at || doc.created_at) }}</time>
            <details :open="menu === doc.id" @toggle="($event.target as HTMLDetailsElement).open ? menu = doc.id : menu === doc.id && (menu = '')">
              <summary :aria-label="'更多操作 · ' + doc.name">更多</summary>
              <button class="text-button danger-button" @click="requestDelete('document', doc)">删除资料</button>
            </details>
          </li>
        </ul>
      </section>
    </template>

    <WorkspaceDialog v-if="replaceQueue" title="当前批次还有待办文件" @close="keepQueue">
      <p>当前目标库为 {{ state.bases.find(kb => kb.id === target)?.name || '尚未选择' }}。可返回队列继续处理，或选择新文件替换待办项。</p>
      <p>取消选择或新文件均无效时，原队列会保留。已接收资料可在列表中查看。</p>
      <div class="modal-actions">
        <button autofocus class="btn secondary" @click="keepQueue">返回当前批次</button>
        <button class="btn primary" @click="approveReplacement">选择文件并替换待办项</button>
      </div>
    </WorkspaceDialog>
    <WorkspaceDialog v-if="libraryDialog" :title="libraryDialog === 'upload' ? '选择上传目标' : libraryDialog === 'sync' ? '从目录同步' : '新建知识库'"
      :busy="busy" @close="libraryDialog = ''">
      <form class="action-stack" @submit.prevent="libraryDialog === 'sync' ? sync() : createLibrary()">
        <p v-if="libraryDialog === 'upload'">已选 {{ batch.length }} 个文件。创建知识库后，文件会提交到此库。</p>
        <label v-if="libraryDialog === 'sync'">相对路径（目标库为 {{ selectedKb?.name }}）<input v-model="path" required maxlength="500" autofocus /></label>
        <label v-else>知识库名称<input v-model="name" required maxlength="120" autofocus /></label>
        <p v-if="dialogError" class="alert error-alert" role="alert">{{ dialogError }}</p>
        <div class="modal-actions">
          <button class="btn secondary" type="button" :disabled="busy" @click="libraryDialog = ''">取消</button>
          <button class="btn primary" :disabled="busy">{{ busy ? '正在提交…' : libraryDialog === 'upload' ? '创建并上传' : libraryDialog === 'sync' ? '开始同步' : '创建知识库' }}</button>
        </div>
      </form>
    </WorkspaceDialog>
    <DocumentVersions v-if="detail && active" :key="detail.id" :document="detail" :max-mb="maxMb"
      :restoring="isRestoring(detail)" :on-changed="changed" @close="detailId = ''" />
    <ConfirmAction v-if="deleteTarget" :title="deleteTarget.title" :description="deleteTarget.description" :action="remove" @close="deleteTarget = undefined" />
  </main>
</template>
