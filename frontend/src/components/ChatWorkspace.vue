<script setup lang="ts">
import { readLocal, writeLocal, removeLocal } from '../storage'
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, toRef, watch } from 'vue'
import { ArrowUp, MessageSquare, Plus } from 'lucide-vue-next'
import { api, ApiError, downloadDocument, streamChat, type AnswerMetadata, type Citation, type Conversation, type Message, type User } from '../api'
import type { Corpus } from '../api/corpus'
import { available, dateText, errorText, processing, record, useVisiblePolling } from '../workspace'
import ConfirmAction from './ConfirmAction.vue'
import WorkspaceDialog from './WorkspaceDialog.vue'

const props = defineProps<{ user: User; corpus: Corpus; active: boolean; enterSends: boolean; entry?: { scope: string } }>()
const emit = defineEmits<{ upload: [id: string] }>()
type Thread = {
  localId: string
  id: string
  scope: string | null
  question: string
  messages: Message[]
  pending: string
  partial: string
  loading: boolean
  loaded: boolean
  error: string
  notice: string
  attempt?: { key: string; body: string }
}
const scopeKey = (id: string) => `chat-scope:${props.user.id}:${id}`
const history = ref<Conversation[]>([])
const historyLoaded = ref(false)
const historyLoading = ref(false)
const historyError = ref('')
let historySequence = 0
const threads = ref<Thread[]>([])
const controllers = new Map<string, AbortController>()
const scroller = ref<HTMLElement>()
const followLatest = ref(true)
const historyOpen = ref(false)
const pendingDelete = ref<Conversation>()
const sources = ref<{ thread: string; message: string; selected: string; citations: Citation[]; loading: boolean; error: string; retryable: boolean }>()
let disposed = false

function createThread(scope: string | null, id = '') {
  const item = reactive<Thread>({
    localId: crypto.randomUUID(), id, scope, question: '', messages: [], pending: '', partial: '',
    loading: false, loaded: !id, error: '', notice: '',
  })
  threads.value.push(item)
  return item
}

const current = ref(createThread(props.entry?.scope ?? ''))
const conversationTitle = computed(() => current.value.id
  ? history.value.find(item => item.id === current.value.id)?.title || current.value.messages.find(item => item.role === 'user')?.content.slice(0, 100) || '问答'
  : '问答')
const sourceOpener = ref<HTMLElement>()
const selectedSource = computed(() => sources.value?.citations.find(item => item.chunk_id === sources.value?.selected))

// Keep the reading position when the docked panel changes the width of the conversation.
watch(() => !!sources.value, async () => {
  const element = scroller.value
  if (!element) return
  const top = element.scrollTop
  const atEnd = element.scrollHeight - top - element.clientHeight < 8
  const viewport = element.getBoundingClientRect()
  const message = Array.from(element.querySelectorAll('.message-content')).find(item => {
    const bounds = item.getBoundingClientRect()
    return bounds.bottom > viewport.top && bounds.top < viewport.bottom
  })
  const bounds = message?.getBoundingClientRect()
  const line = bounds && document.caretRangeFromPoint?.(bounds.left + 2, Math.max(viewport.top, bounds.top) + 2)
  const anchor = line && message?.contains(line.startContainer) ? line : message
  const anchorTop = anchor?.getBoundingClientRect().top
  await nextTick()
  if (atEnd) element.scrollTop = element.scrollHeight
  else if (anchor && anchorTop !== undefined) element.scrollTop += anchor.getBoundingClientRect().top - anchorTop
  else element.scrollTop = top
})
const state = props.corpus.state
const scopeMissing = computed(() => !!current.value.scope && state.loaded && !state.bases.some(kb => kb.id === current.value.scope))
const hasSources = computed(() => current.value.scope !== null && state.loaded && !scopeMissing.value &&
  state.documents.some(doc => (!current.value.scope || doc.knowledge_base_id === current.value.scope) && available(doc)))
const activeRequest = computed(() => !!current.value.pending)
const unsaved = computed(() => threads.value.filter(item => !item.id && item !== current.value && (item.question || item.pending)))
const canSend = computed(() => hasSources.value && current.value.loaded && !current.value.loading && !activeRequest.value && !!current.value.question.trim())

const scopedDocuments = computed(() => state.documents.filter(doc => !current.value.scope || doc.knowledge_base_id === current.value.scope))
const waitingDocuments = computed(() => !hasSources.value && scopedDocuments.value.some(processing))
const scopeBlocker = computed(() => {
  if (!state.loaded) return state.error ? '资料范围读取失败' : '正在读取资料范围'
  if (current.value.scope === null) return '请先选择本次提问范围'
  if (scopeMissing.value) return '原知识库已不可用'
  if (!hasSources.value) return '此范围暂无可用资料'
  return ''
})
const retryAvailable = computed(() => !!current.value.attempt && !activeRequest.value)
watch(() => current.value.question, () => {
  const item = current.value
  if (item.attempt && !item.pending && JSON.parse(item.attempt.body).question !== item.question.trim()) {
    item.attempt = undefined
    item.error = ''
    item.notice = ''
  }
}, { flush: 'sync' })

function openThread(item: Thread) {
  followLatest.value = true
  current.value = item
  historyOpen.value = false
  sources.value = undefined
  void scroll()
}

function start(scope = '') {
  const item = current.value
  if (!item.id && !item.question && !item.pending) {
    item.scope = scope
    item.error = ''
    item.notice = ''
    item.attempt = undefined
    return
  }
  openThread(createThread(scope))
}

function changeScope(event: Event) {
  const scope = (event.target as HTMLSelectElement).value
  const item = current.value
  if (scope === item.scope) return
  if (item.id) {
    const next = createThread(scope)
    next.question = item.question
    next.notice = '已用新范围开启对话，可在历史中查看原会话。'
    openThread(next)
  } else {
    item.scope = scope
    item.notice = ''
    item.error = ''
    item.attempt = undefined
  }
}

watch(() => props.entry, entry => { if (entry) start(entry.scope) })
watch(() => props.active, active => { if (!active) sources.value = undefined })

async function loadHistory(saved = false) {
  if (historyLoading.value && !saved) return
  const ticket = ++historySequence
  historyLoading.value = true
  try {
    const result = await api<Conversation[]>('/conversations')
    if (ticket !== historySequence) return
    history.value = result
    historyLoaded.value = true
    historyError.value = ''
  } catch (error) {
    if (ticket === historySequence) historyError.value = saved ? '回答已保存，会话列表暂未刷新。' : errorText(error)
  } finally {
    if (ticket === historySequence) historyLoading.value = false
  }
}

async function readThread(item: Thread) {
  if (!item.id || item.loading || item.pending) return
  item.loading = true
  item.error = ''
  try {
    const result = await api<{ messages: Message[] }>(`/conversations/${item.id}`)
    item.messages = result.messages
    item.loaded = true
    if (item === current.value) void scroll()
  } catch (error) {
    item.loaded = false
    item.error = errorText(error)
  } finally { item.loading = false }
}

function openConversation(id: string) {
  const item = threads.value.find(thread => thread.id === id) ?? createThread(readLocal(scopeKey(id)), id)
  openThread(item)
  void readThread(item)
}

async function removeConversation() {
  const item = pendingDelete.value
  if (!item) return
  await api(`/conversations/${item.id}`, { method: 'DELETE' })
  historySequence += 1
  historyLoading.value = false
  history.value = history.value.filter(entry => entry.id !== item.id)
  removeLocal(scopeKey(item.id))
  threads.value = threads.value.filter(thread => thread.id !== item.id)
  if (current.value.id === item.id) openThread(createThread(''))
  void loadHistory()
}

function stop() { controllers.get(current.value.localId)?.abort() }

async function ask() {
  if (!canSend.value) return
  const item = current.value
  const question = item.question.trim()
  if (question.length > 8000) { item.error = '问题最多 8000 字符。'; return }
  if (!state.documents.some(doc => (!item.scope || doc.knowledge_base_id === item.scope) && available(doc))) {
    item.error = '此范围当前没有可用资料，请上传资料或切换范围。'
    return
  }
  const body = {
    question, knowledge_base_ids: item.scope ? [item.scope] : [], top_k: 5, mode: 'agent',
    ...(item.id ? { conversation_id: item.id } : {}),
  }
  const serialized = JSON.stringify(body)
  if (item.attempt?.body !== serialized) item.attempt = { body: serialized, key: crypto.randomUUID() }
  const requestId = item.attempt.key
  const started = performance.now()
  const controller = new AbortController()
  controllers.set(item.localId, controller)
  item.error = ''
  item.notice = ''
  item.partial = ''
  item.pending = question
  followLatest.value = true
  void scroll()
  let outcome = '失败'
  try {
    const result = await streamChat({ ...body, request_id: requestId }, controller.signal, token => {
      item.partial += token
      if (item === current.value) void scroll()
    })
    if (disposed) return
    item.id = result.conversation_id
    if (!history.value.some(entry => entry.id === item.id)) {
      history.value.unshift({ id: item.id, title: question.slice(0, 100), created_at: new Date().toISOString() })
    }
    item.messages.push(
      { id: requestId, role: 'user', content: question },
      { id: result.message_id, role: 'assistant', content: result.answer, citations: result.citations, run_metadata: result },
    )
    writeLocal(scopeKey(item.id), item.scope ?? '')
    item.question = ''
    item.attempt = undefined
    outcome = '完成'
  } catch (error) {
    if (controller.signal.aborted) {
      outcome = '停止'
      item.notice = '已停止接收，问题已保留'
    } else item.error = error instanceof ApiError && [0, 502].includes(error.status)
      ? '回答连接中断，可重试原问题'
      : errorText(error)
  } finally {
    item.pending = ''
    item.partial = ''
    controllers.delete(item.localId)
    record('chat_finished', { outcome, duration_ms: Math.round(performance.now() - started) })
    if (item === current.value) void scroll()
  }
  if (outcome === '完成') void loadHistory(true)
}

function submitOnEnter(event: KeyboardEvent) {
  if (!props.enterSends || event.shiftKey || event.altKey || event.ctrlKey || event.metaKey || event.isComposing || event.keyCode === 229) return
  event.preventDefault()
  void ask()
}

function answerLabel(meta?: AnswerMetadata) {
  if (meta?.abstained || meta?.verification?.mode === 'model_abstention') return '证据不足'
  if (meta?.verification?.mode === 'extractive' || meta?.provider?.startsWith('extractive')) return '原文摘录'
  return ''
}

function sourceAccessible(message: Message) {
  return !message.restricted && (!state.loaded || !message.citations?.some(citation => !state.documents.some(doc => doc.id === citation.document_id)))
}

async function showSource(message: Message, citation: Citation, event: MouseEvent) {
  historyOpen.value = false
  sourceOpener.value = event.currentTarget as HTMLElement
  sources.value = {
    thread: current.value.localId, message: message.id, selected: citation.chunk_id,
    citations: [], loading: false, error: '', retryable: false,
  }
  await readSources()
}

async function readSources() {
  const drawer = sources.value
  const thread = threads.value.find(item => item.localId === drawer?.thread)
  if (!drawer || !thread || drawer.loading) return
  drawer.loading = true
  drawer.error = ''
  drawer.retryable = false
  try {
    const result = await api<{ messages: Message[] }>(`/conversations/${thread.id}`)
    const saved = result.messages.find(row => row.id === drawer.message)
    if (!saved?.citations?.some(item => item.chunk_id === drawer.selected) || !sourceAccessible(saved)) {
      if (saved?.restricted) thread.messages = thread.messages.map(row => row.id === saved.id ? saved : row)
      throw new ApiError('来源已删除或无权访问。', 404)
    }
    drawer.citations = saved.citations
  } catch (error) {
    drawer.error = errorText(error)
    drawer.retryable = !(error instanceof ApiError && [401, 403, 404, 410].includes(error.status))
  } finally { drawer.loading = false }
}

async function selectSource(citation: Citation) {
  const panel = sources.value
  if (!panel || panel.selected === citation.chunk_id) return
  panel.selected = citation.chunk_id
  panel.error = ''
  await nextTick()
  const heading = document.getElementById('source-' + citation.chunk_id)?.querySelector('h3')
  heading?.closest('dialog')?.scrollTo({ top: 0 })
  heading?.focus({ preventScroll: true })
}

async function download(citation: Citation) {
  const drawer = sources.value
  if (!drawer) return
  if (!citation.revision_id) {
    drawer.error = '此引用未记录原文版本，无法下载对应版本。'
    drawer.retryable = false
    return
  }
  drawer.error = ''
  try { await downloadDocument(citation.document_id, citation.document_name, citation.revision_id) }
  catch (error) {
    if (error instanceof ApiError && [403, 404, 410].includes(error.status)) {
      drawer.citations = drawer.citations.filter(item => item.chunk_id !== citation.chunk_id)
      drawer.error = '此版本的原文已不可用。'
      drawer.retryable = false
    } else drawer.error = errorText(error)
  }
}

async function scroll() {
  await nextTick()
  if (props.active && followLatest.value && scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight
}

function trackScroll() {
  const element = scroller.value
  if (element) followLatest.value = element.scrollHeight - element.scrollTop - element.clientHeight < 64
}
function jumpToLatest() { followLatest.value = true; void scroll() }

async function recheck() {
  void loadHistory()
  if (!state.loading) await props.corpus.refresh()
  if (current.value.id && current.value.loaded && !current.value.attempt) await readThread(current.value)
}
useVisiblePolling(toRef(props, 'active'), () => false, recheck)
onMounted(() => { void loadHistory() })
onUnmounted(() => { disposed = true; for (const controller of controllers.values()) controller.abort() })
</script>

<template>
  <main class="chat-page">
    <div class="chat-workspace">
      <section class="chat-main">
        <header class="chat-top page-heading">
          <h1>{{ conversationTitle }}</h1>
          <div class="inline-actions chat-actions">
            <details class="chat-history menu-popover" :open="historyOpen" @toggle="historyOpen = ($event.target as HTMLDetailsElement).open">
              <summary class="btn secondary">会话历史{{ historyError ? '（暂未刷新）' : '' }}</summary>
              <div class="chat-history-list">
                <p v-if="historyLoading && !historyLoaded" role="status">正在读取会话…</p>
                <p v-if="historyError" role="alert">{{ historyError }} <button class="text-button" @click="loadHistory()">重试读取历史</button></p>
                <p v-if="historyLoaded && !historyError && !history.length && !unsaved.length" class="muted">暂无历史会话。</p>
                <button v-for="item in unsaved" :key="item.localId" class="text-button" @click="openThread(item)">未发送的草稿</button>
                <div v-for="item in history" :key="item.id" class="history-row">
                  <button class="text-button" :aria-current="current.id === item.id ? 'true' : undefined" @click="openConversation(item.id)">{{ item.title }}</button>
                  <details>
                    <summary :aria-label="'更多操作 · ' + item.title">更多</summary>
                    <button class="text-button danger-button" :disabled="threads.some(thread => thread.id === item.id && thread.pending)" @click="pendingDelete = item">删除会话</button>
                  </details>
                </div>
              </div>
            </details>
            <button class="btn secondary" @click="start()"><Plus :size="17" />新建对话</button>
          </div>
        </header>
        <div ref="scroller" class="messages" @scroll="trackScroll">
          <p v-if="current.loading && !current.loaded" role="status">正在读取会话…</p>
          <article v-for="message in current.loaded ? current.messages : []" :key="message.id" class="message" :class="message.role">
            <div class="message-body">
              <div class="message-author">{{ message.role === 'assistant' ? '知衡助手' : '我' }}<small v-if="answerLabel(message.run_metadata)">{{ answerLabel(message.run_metadata) }}</small></div>
              <div class="message-content">{{ sourceAccessible(message) ? message.content : '此回答的来源已不可用，内容已隐藏。' }}</div>
              <div v-if="sourceAccessible(message) && message.citations?.length" class="message-citations">
                <button v-for="(citation, index) in message.citations" :key="citation.chunk_id" :aria-expanded="sources?.message === message.id && sources.selected === citation.chunk_id"
                  @click="showSource(message, citation, $event)">
                  <span>{{ index + 1 }}</span>{{ citation.document_name }}<small v-if="citation.page">第 {{ citation.page }} 页</small>
                </button>
              </div>
            </div>
          </article>
          <template v-if="activeRequest">
            <article class="message user"><div class="message-body"><div class="message-author">我</div><div class="message-content">{{ current.pending }}</div></div></article>
            <article class="message assistant" aria-live="polite"><div class="message-body"><div class="message-author">生成中，尚未核对</div><div class="message-content">{{ current.partial || '正在查找相关资料…' }}</div></div></article>
          </template>
        </div>

        <div class="composer-wrap">
          <div v-if="current.loaded && !current.messages.length && !activeRequest" class="chat-welcome">
            <MessageSquare :size="24" aria-hidden="true" />
            <div><h2>向你的资料提问</h2><p>回答附有引用，可核对原文。</p></div>
          </div>
          <button v-if="!followLatest" class="btn secondary return-latest" @click="jumpToLatest">回到最新回答</button>
          <p v-if="current.notice" class="workspace-notice" role="status">{{ current.notice }}</p>
          <p v-if="current.error" class="alert error-alert" role="alert">{{ current.error }} <button v-if="!current.loaded" class="text-button" @click="readThread(current)">重试读取</button></p>
          <p v-if="state.error" class="alert error-alert" role="alert">资料范围读取失败。{{ state.readAt ? '保留上次读取结果（' + dateText(state.readAt) + '）。' : '' }} <button class="text-button" @click="corpus.refresh()">重试读取</button></p>
          <form class="composer" @submit.prevent="ask">
            <textarea v-model="current.question" aria-label="输入问题" placeholder="输入你的问题" rows="2" maxlength="8000"
              :disabled="activeRequest || !current.loaded" @keydown.enter="submitOnEnter" />
            <div class="composer-bottom">
              <label class="chat-scope">本次范围
                <select :value="current.scope ?? '__unset__'" :disabled="activeRequest || !state.loaded" @change="changeScope">
                  <option v-if="current.scope === null" disabled value="__unset__">选择范围</option>
                  <option value="">全部资料</option>
                  <option v-if="scopeMissing" :value="current.scope">原知识库已不可用</option>
                  <option v-for="kb in state.bases" :key="kb.id" :value="kb.id">{{ kb.name }}</option>
                </select>
              </label>
              <button v-if="activeRequest" class="btn secondary" type="button" @click="stop">停止生成</button>
              <button v-else-if="retryAvailable" class="btn primary" type="submit" :disabled="!canSend">重试原问题</button>
              <button v-else class="btn primary send-button" type="submit" aria-label="发送问题" :disabled="!canSend">发送<ArrowUp :size="16" /></button>
            </div>
          </form>
          <p v-if="current.id" class="composer-note">选择其他范围会开启新对话</p>
          <p v-if="scopeBlocker" class="composer-note" role="status">{{ scopeBlocker }}
            <button v-if="waitingDocuments && current.scope !== null && !scopeMissing" class="text-button"
              @click="emit('upload', current.scope || scopedDocuments.find(processing)?.knowledge_base_id || '')">查看资料处理状态</button>
            <button v-else-if="state.loaded && current.scope !== null && !scopeMissing && !hasSources" class="text-button"
              @click="emit('upload', current.scope || '')">上传资料</button>
          </p>
          <p v-else-if="canSend" class="composer-note">{{ enterSends ? 'Enter 发送 · Shift + Enter 换行' : 'Enter 换行 · 点击发送按钮提交' }}</p>
        </div>
      </section>
    </div>

    <WorkspaceDialog v-if="sources" title="回答来源" drawer dockable :return-focus="sourceOpener" @close="sources = undefined">
      <p v-if="sources.loading" role="status">正在核对来源…</p>
      <p v-if="sources.error" class="alert error-alert" role="alert">{{ sources.error }}
        <button v-if="sources.retryable" class="text-button" :disabled="sources.loading" @click="readSources">重试读取来源</button>
      </p>
      <article v-if="selectedSource" :id="'source-' + selectedSource.chunk_id" :key="selectedSource.chunk_id" class="source-card focused citation-preview">
        <h3 tabindex="-1">{{ selectedSource.document_name }}</h3>
        <p class="source-meta muted">{{ selectedSource.page ? `第 ${selectedSource.page} 页` : '来源片段' }}
          <span v-if="selectedSource.revision_id"> · 引用版本 {{ selectedSource.revision_id }}</span>
        </p>
        <button class="btn secondary" @click="download(selectedSource)">下载原文</button>
        <blockquote>{{ selectedSource.text }}</blockquote>
      </article>
      <nav v-if="sources.citations.length > 1 || (!selectedSource && sources.citations.length)" class="citation-list" aria-label="此回答的引用">
        <h3>此回答的引用</h3>
        <button v-for="(citation, index) in sources.citations" :key="citation.chunk_id" class="citation-option"
          :aria-current="sources.selected === citation.chunk_id ? 'true' : undefined" @click="selectSource(citation)">
          <span class="citation-index">{{ index + 1 }}</span>
          <span>{{ citation.document_name }}<small v-if="citation.page"> · 第 {{ citation.page }} 页</small></span>
        </button>
      </nav>
    </WorkspaceDialog>
    <ConfirmAction v-if="pendingDelete" :title="'删除「' + pendingDelete.title + '」'"
      description="会移除此会话的所有问题和回答。删除无法撤销。" :action="removeConversation" @close="pendingDelete = undefined" />
  </main>
</template>
