<script setup lang="ts">
import { readLocal, writeLocal, removeLocal } from '../../storage'
import { computed, onMounted, ref, toRef } from 'vue'
import { ApiError, downloadDocument, type Document, type KnowledgeBase } from '../../api'
import { learning, type Attempt, type Capability, type LearningHistory, type LearningTask, type Source, type Workspace, type WorkspaceSummary } from '../../api/learning'
import { available, dateText, errorText, record, useVisiblePolling } from '../../workspace'
import ConfirmAction from '../ConfirmAction.vue'
import WorkspaceDialog from '../WorkspaceDialog.vue'
import LearningFeedback from './LearningFeedback.vue'
import LearningHint from './LearningHint.vue'

const props = defineProps<{ knowledgeBases: KnowledgeBase[]; documents: Document[]; userId: string; modelReady: boolean; active: boolean }>()
const emit = defineEmits<{ upload: [id: string] }>()
const summaries = ref<WorkspaceSummary[]>([])
const workspace = ref<Workspace>()
const task = ref<LearningTask>()
const history = ref<LearningHistory>()
const feedback = ref<Attempt>()
const feedbackTask = ref<LearningTask>()
const recommendedTask = ref<LearningTask>()
const historical = ref<{ attempt: Attempt; task: LearningTask }>()
const creating = ref(false)
const listLoaded = ref(false)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const tab = ref<'task' | 'history'>('task')
const kbId = ref('')
const documentIds = ref<string[]>([])
const goal = ref('我想理解数据库事务，并能分析并发场景下的数据异常。')
const reviewed = ref(new Set<string>())
const answer = ref('')
const hint = ref<{ level: number; text: string; sources: Source[] }>()
const skipConfirm = ref(false)
const removeConfirm = ref(false)
const correction = ref<{ attempt: Attempt; reason: string; key: string }>()
const redoing = ref(false)
let sequence = 0

const currentKey = `learning-current:${props.userId}`
const draftKey = (id: string) => `learning-draft:${props.userId}:${id}`
const seenKey = (id: string) => `learning-feedback:${props.userId}:${id}`
type RequestKey = { signature: string; key: string }
let creation: RequestKey | undefined
let submission: RequestKey | undefined
let advancement: RequestKey | undefined
let helpRequest: RequestKey | undefined
function requestFor(signature: string, previous?: RequestKey) {
  return previous?.signature === signature ? previous : { signature, key: crypto.randomUUID() }
}

const options = computed(() => props.documents.filter(doc => doc.knowledge_base_id === kbId.value))
const chosen = computed(() => options.value.filter(doc => documentIds.value.includes(doc.id) && available(doc)).map(doc => doc.id))
const ongoing = computed(() => summaries.value.filter(item => !['completed', 'failed'].includes(item.status)))
const continueWith = computed(() => ongoing.value.find(item => item.id === readLocal(currentKey)) || ongoing.value[0])
const running = computed(() => ['queued', 'running'].includes(feedback.value?.assessment_status || '') ||
  (workspace.value?.jobs.some(job => ['queued', 'running'].includes(job.status)) ?? false))
const planJob = computed(() => workspace.value?.jobs.find(job => job.kind !== 'assessment'))
const assessmentJob = computed(() => workspace.value?.jobs.find(job => job.kind === 'assessment' && job.target_id === feedback.value?.id))
const decision = computed(() => history.value?.decisions.find(item => item.trigger_attempt_id === feedback.value?.id && item.status !== 'superseded'))
const currentCap = computed(() => workspace.value?.capabilities.find(cap => cap.id === task.value?.capability_id))
const covered = computed(() => workspace.value?.capabilities.filter(cap => cap.coverage_status === 'covered') || [])
function hasUsableSources(cap: Capability) {
  return cap.source_ids.length > 0 && cap.source_ids.every(id => {
    const source = workspace.value?.sources.find(item => item.id === id)
    return source && !['unavailable', 'expired'].includes(source.state || '')
  })
}
const unchecked = computed(() => covered.value.filter(cap => !reviewed.value.has(cap.id) || !hasUsableSources(cap)))
const reviewedCount = computed(() => covered.value.length - unchecked.value.length)
const canConfirm = computed(() => covered.value.length > 0 && !unchecked.value.length)
const planBlocker = computed(() => {
  if (workspace.value?.source_warning) return '来源已失效，请重新选择资料。'
  if (!covered.value.length) return '尚无资料已覆盖的能力，请查看原文并编辑覆盖状态，或补充资料。'
  if (covered.value.some(cap => !hasUsableSources(cap))) return '已覆盖的能力中有原文依据缺失或失效，请补充绑定或重新选择资料。'
  return unchecked.value.length ? '请逐项查看原文依据并核对。' : ''
})
function focusUnchecked() {
  const cap = unchecked.value[0] || workspace.value?.capabilities[0]
  if (!cap) return
  const element = document.getElementById('cap-' + cap.id)
  element?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  element?.focus({ preventScroll: true })
}
const hintNames = ['提醒条件', '查看相关原文', '局部示例', '完整解析']
const labels: Record<string, string> = { generating: '生成草稿中', draft: '待确认目标', active: '学习中', completed: '验证完成', needs_practice: '需要更多练习', failed: '待重试', queued: '排队中', running: '评价中', unverified: '尚未验证', needs_evidence: '需要补充证据', assisted: '帮助下完成', independent: '独立完成', transfer_verified: '变式验证通过', suspected: '疑似障碍', confirmed: '有重复证据', disputed: '判断有争议', resolved: '已有解除依据', met: '满足标准', partial: '部分满足', not_met: '尚未满足', insufficient_evidence: '证据不足', pending: '待完成', skipped: '已跳过', current: '当前版本', historical: '历史版本', expired: '已过期', unavailable: '来源不可用', stock_value_vs_order_count: '库存值与订单数混淆', interleaving: '交错执行理解', prevention: '防护方法不足', expression: '表达需要补充' }
const name = (v: string) => labels[v] || v
function sourceName(id: string) {
  const source = workspace.value?.sources.find(item => item.id === id)
  return source ? `${source.document_name}${source.page ? ` · 第 ${source.page} 页` : ''}` : '来源不可用'
}

async function list() {
  summaries.value = await learning.list()
  listLoaded.value = true
}

function setTask(next?: LearningTask) {
  if (task.value?.id !== next?.id) {
    answer.value = next ? readLocal(draftKey(next.id)) || '' : ''
    hint.value = undefined
  }
  task.value = next
}

function editAnswer(event: Event) {
  answer.value = (event.target as HTMLTextAreaElement).value
  if (task.value) writeLocal(draftKey(task.value.id), answer.value)
}

function openFeedback(attempt: Attempt, shownTask?: LearningTask) {
  recommendedTask.value = undefined
  feedback.value = attempt
  feedbackTask.value = shownTask
  historical.value = undefined
  hint.value = undefined
  redoing.value = false
  tab.value = 'task'
  record('learning_feedback_opened', { status: attempt.assessment_status, hint_level: attempt.hint_level_at_submit })
}

async function readWorkspace(id: string, enter = false, ticket = sequence) {
  const [next, records] = await Promise.all([learning.get(id), learning.history(id)])
  const attempt = feedback.value && workspace.value?.id === id
    ? records.attempts.find(item => item.id === feedback.value?.id)
    : enter ? records.attempts.find(item => !item.invalidated && item.id !== readLocal(seenKey(id))) : undefined
  const latest = records.attempts[0]
  const show = enter ? (attempt?.id === latest?.id ? attempt : undefined) : attempt
  const taskId = show?.task_id || (!enter && redoing.value ? task.value?.id : next.current_task_id)
  const nextTask = taskId ? await learning.task(taskId) : undefined
  if (ticket !== sequence) return
  if (enter) {
    feedback.value = undefined
    feedbackTask.value = undefined
    historical.value = undefined
    hint.value = undefined
    redoing.value = false
  }
  workspace.value = next
  history.value = records
  if (show) {
    if (enter) openFeedback(show, nextTask)
    else { feedback.value = show; feedbackTask.value = nextTask }
  } else if (!feedback.value) setTask(nextTask)
  recommendedTask.value = undefined
  if (show?.assessment_status === 'completed' && next.current_task_id && next.current_task_id !== show.task_id && !next.source_warning) {
    try {
      const recommendation = await learning.task(next.current_task_id)
      if (ticket === sequence && !recommendation.restricted) recommendedTask.value = recommendation
    } catch { /* The saved feedback remains usable while the recommendation can be retried. */ }
  }
  if (next.source_warning) hint.value = undefined
}

async function update() {
  const id = workspace.value?.id
  if (id) await readWorkspace(id)
  else await list()
}

async function action(fn: () => Promise<void>) {
  if (busy.value) return
  sequence += 1
  busy.value = true
  error.value = ''
  notice.value = ''
  try { await fn() }
  catch (reason) {
    error.value = errorText(reason)
    if (reason instanceof ApiError && [403, 409, 410].includes(reason.status) && workspace.value) {
      try { await readWorkspace(workspace.value.id); reviewed.value.clear(); error.value += ' 已读取最新状态，请核对后继续。' }
      catch { error.value += ' 可重试读取最新状态。' }
    }
  } finally { busy.value = false }
}

async function savedRefresh(id: string) {
  try { await readWorkspace(id) }
  catch { notice.value += ' 状态暂未刷新，可重试读取。' }
}

function open(id: string) {
  return action(async () => {
    await readWorkspace(id, true)
    if (!workspace.value) return
    writeLocal(currentKey, id)
    reviewed.value.clear()
    creating.value = false
    tab.value = 'task'
  })
}

function back() {
  return action(async () => {
    workspace.value = undefined
    setTask()
    feedback.value = undefined
    feedbackTask.value = undefined
    historical.value = undefined
    redoing.value = false
    await list()
  })
}

function beginCreate(template?: Workspace, reselect = false) {
  error.value = ''
  notice.value = ''
  if (template) {
    kbId.value = props.knowledgeBases.some(base => base.id === template.knowledge_base_id) ? template.knowledge_base_id! : ''
    goal.value = template.goal_text
    documentIds.value = reselect ? [] : [...template.selected_document_ids]
  } else if (!kbId.value && props.knowledgeBases.length === 1) kbId.value = props.knowledgeBases[0]!.id
  creating.value = true
}

function create() {
  return action(async () => {
    if (!chosen.value.length || chosen.value.length > 10 || goal.value.trim().length < 10) return
    const body = { knowledge_base_id: kbId.value, document_ids: chosen.value, goal_text: goal.value.trim() }
    creation = requestFor(JSON.stringify(body), creation)
    const result = await learning.create({ ...body, request_key: creation.key })
    notice.value = '专题已创建。'
    try { await readWorkspace(result.workspace_id, true) }
    catch { notice.value += ' 状态暂未刷新，再次提交会打开同一个专题。'; return }
    creation = undefined
    writeLocal(currentKey, result.workspace_id)
    creating.value = false
    tab.value = 'task'
    reviewed.value.clear()
    notice.value = '专题已创建，正在准备能力草稿。'
  })
}

function save(confirm = false) {
  return action(async () => {
    if (!workspace.value || (confirm && !canConfirm.value)) return
    workspace.value = await learning.save(workspace.value)
    notice.value = '草稿已保存。'
    if (confirm) {
      workspace.value = await learning.confirm(workspace.value)
      notice.value = '目标已确认。'
      await savedRefresh(workspace.value.id)
    }
  })
}

function submit() {
  return action(async () => {
    const current = task.value
    const w = workspace.value
    const text = answer.value.trim()
    if (!current || !w || !text || text.length > 10000 || current.restricted || w.source_warning) return
    submission = requestFor(JSON.stringify([current.id, text]), submission)
    const result = await learning.submit(current.id, text, submission.key)
    openFeedback(result, current)
    removeLocal(draftKey(current.id))
    answer.value = ''
    submission = undefined
    notice.value = '作答已保存。'
    await savedRefresh(w.id)
  })
}

function continueToTask() {
  return action(async () => {
    const w = workspace.value
    const a = feedback.value
    if (!w || !a) return
    const latest = await learning.get(w.id)
    const nextTask = latest.current_task_id ? await learning.task(latest.current_task_id) : undefined
    workspace.value = latest
    writeLocal(seenKey(w.id), a.id)
    feedback.value = undefined
    feedbackTask.value = undefined
    setTask(nextTask)
  })
}

function review(attempt: Attempt) {
  return action(async () => {
    const [latest, oldTask] = await Promise.all([learning.attempt(attempt.id), learning.task(attempt.task_id)])
    historical.value = { attempt: latest, task: oldTask }
  })
}

function redo(oldTask: LearningTask) {
  if (oldTask.restricted || !workspace.value || workspace.value.source_warning) return
  if (feedback.value) writeLocal(seenKey(workspace.value.id), feedback.value.id)
  feedback.value = undefined
  feedbackTask.value = undefined
  historical.value = undefined
  redoing.value = true
  tab.value = 'task'
  setTask(oldTask)
}

function help(level: number, id = task.value?.id) {
  return action(async () => {
    if (!id) return
    helpRequest = requestFor(JSON.stringify([id, level]), helpRequest)
    hint.value = await learning.hint(id, level, helpRequest.key)
    helpRequest = undefined
    if (task.value?.id === id) task.value.hint_level = Math.max(task.value.hint_level, level)
  })
}

async function advance(skip: boolean) {
  const w = workspace.value
  if (!w) return
  sequence += 1
  advancement = requestFor(JSON.stringify([w.id, w.state_version, skip]), advancement)
  try { workspace.value = await learning.next(w, skip, advancement.key) }
  catch (reason) {
    if (reason instanceof ApiError && reason.status === 409) {
      await readWorkspace(w.id)
      throw new Error('学习状态已变化，请关闭此窗口并核对当前任务。')
    }
    throw reason
  }
  redoing.value = false
  advancement = undefined
  notice.value = skip ? '已跳过本题。' : '已检查下一步。'
  await savedRefresh(w.id)
}

function retry(id: string) {
  return action(async () => {
    if (!workspace.value) return
    const updated = await learning.retry(id)
    workspace.value.jobs = workspace.value.jobs.map(job => job.id === id ? updated : job)
    if (feedback.value && updated.target_id === feedback.value.id) feedback.value.assessment_status = 'queued'
    notice.value = '已重新提交原任务。'
    await savedRefresh(workspace.value.id)
  })
}

function correct() {
  return action(async () => {
    if (!workspace.value || !correction.value) return
    const item = correction.value
    workspace.value = await learning.correct(workspace.value, item.attempt.id, item.reason, item.key)
    correction.value = undefined
    feedback.value = undefined
    historical.value = undefined
    redoing.value = false
    tab.value = 'task'
    notice.value = '争议已记录，该次评价已停止影响后续推荐。'
    await savedRefresh(workspace.value.id)
  })
}

async function remove() {
  const id = workspace.value?.id
  if (!id) return
  await learning.remove(id)
  summaries.value = summaries.value.filter(item => item.id !== id)
  if (readLocal(currentKey) === id) removeLocal(currentKey)
  await back()
  notice.value = error.value ? '专题已删除，列表暂未刷新。' : '专题已删除。'
}

function feedbackTitle(a: Attempt) {
  if (a.invalidated) return '此评价已记录争议'
  if (a.assessment_status === 'failed') return '评价失败'
  if (a.assessment_status === 'running' || (feedback.value?.id === a.id && assessmentJob.value?.status === 'running')) return '正在评价'
  if (a.assessment_status !== 'completed') return '等待评价'
  return a.assessment.criteria?.length ? '本次评价' : '来源不可用，评价已隐藏'
}

function download(source: Source) {
  return action(async () => {
    if (source.document_id) await downloadDocument(source.document_id, source.document_name, source.revision_id)
  })
}

function dispute(attempt: Attempt) {
  historical.value = undefined
  correction.value = { attempt, reason: '', key: crypto.randomUUID() }
}

useVisiblePolling(toRef(props, 'active'), () => running.value, async () => {
  if (busy.value || creating.value || skipConfirm.value || removeConfirm.value || correction.value || workspace.value?.status === 'draft') return
  const ticket = sequence
  try { await update() }
  catch (reason) { if (ticket === sequence) error.value = errorText(reason) }
})
onMounted(() => { void action(list) })
</script>

<template>
  <main class="page learning-page">
    <div class="page-heading">
      <h1>专题学习</h1>
      <button v-if="!creating && !workspace && summaries.length" class="btn secondary" :disabled="busy" @click="beginCreate()">新建专题</button>
    </div>
    <p v-if="error" class="alert error-alert" role="alert">{{ error }} <button class="text-button" :disabled="busy" @click="action(update)">重试读取</button></p>
    <p v-if="notice" class="workspace-notice" role="status">{{ notice }}</p>

    <section v-if="creating" class="panel learning-panel">
      <div class="section-heading"><h2>建立学习目标</h2><button class="text-button" :disabled="busy" @click="creating = false">取消</button></div>
      <p>当前支持数据库事务专题。从一个知识库选择 1–10 份可用资料。</p>
      <form class="action-stack" @submit.prevent="create">
        <label>知识库
          <select v-model="kbId" required :disabled="busy" @change="documentIds = []">
            <option value="" disabled>选择个人知识库</option>
            <option v-for="kb in knowledgeBases" :key="kb.id" :value="kb.id">{{ kb.name }}</option>
          </select>
        </label>
        <label>学习目标<textarea v-model="goal" rows="3" minlength="10" maxlength="1000" :disabled="busy" required /></label>
        <fieldset>
          <legend>选择资料</legend>
          <p v-if="!options.some(available)">当前库没有可用资料。 <button class="text-button" type="button" @click="emit('upload', kbId)">上传资料</button></p>
          <label v-for="doc in options" :key="doc.id" class="check-label">
            <input v-model="documentIds" type="checkbox" :value="doc.id"
              :disabled="busy || !available(doc) || (!documentIds.includes(doc.id) && chosen.length >= 10)" />
            <span>{{ doc.name }} <small v-if="!available(doc)">· 尚不可用或已过期</small></span>
          </label>
        </fieldset>
        <button class="btn primary" :disabled="busy || !chosen.length || goal.trim().length < 10">{{ busy ? '正在提交…' : '生成能力草稿' }}</button>
      </form>
    </section>

    <template v-else-if="workspace">
      <div class="inline-actions">
        <button class="text-button" :disabled="busy" @click="back">返回全部专题</button>
        <span class="outline-chip">{{ name(workspace.status) }}</span>
        <details>
          <summary>专题更多操作</summary>
          <div class="action-stack">
            <button class="text-button" :disabled="busy" @click="beginCreate(workspace)">以此为模板</button>
            <button class="text-button danger-button" :disabled="busy" @click="removeConfirm = true">删除专题</button>
          </div>
        </details>
      </div>
      <h2 class="learning-goal">{{ workspace.goal_text }}</h2>
      <div v-if="workspace.source_warning" class="alert error-alert" role="alert">
        {{ workspace.source_warning }} <button class="text-button" :disabled="busy" @click="beginCreate(workspace, true)">重新选择资料</button>
      </div>

      <section v-if="workspace.status === 'generating' || workspace.status === 'failed'" class="panel learning-panel">
        <h3>{{ name(workspace.status) }}</h3>
        <p v-if="planJob?.error">{{ planJob.error }}</p>
        <button v-if="planJob?.status === 'failed'" class="btn primary" :disabled="busy" @click="retry(planJob.id)">重试生成草稿</button>
      </section>

      <section v-else-if="workspace.status === 'draft'" class="learning-plan">
        <p>核对每项正式练习所需的资料。部分覆盖或缺少依据的能力会保留原覆盖状态。</p>
        <article v-for="cap in workspace.capabilities" :id="'cap-' + cap.id" :key="cap.id" class="panel learning-panel" tabindex="-1">
          <h3>{{ cap.title }}</h3>
          <p><strong>你将能做什么</strong> {{ cap.observable_outcome }}</p>
          <p class="muted">{{ { covered: '资料已覆盖', partial: '部分覆盖', missing: '缺少依据' }[cap.coverage_status] || cap.coverage_status }} · {{ cap.source_ids.length }} 段依据</p>
          <details class="bound-sources">
            <summary>查看原文依据</summary>
            <p v-if="!cap.source_ids.length">尚未绑定依据，请从编辑入口选择原文。</p>
            <article v-for="id in cap.source_ids" :key="id" class="source-card">
              <strong>{{ sourceName(id) }}</strong>
              <p>{{ name(workspace.sources.find(source => source.id === id)?.state || 'unavailable') }}</p>
              <blockquote>{{ workspace.sources.find(source => source.id === id)?.excerpt }}</blockquote>
            </article>
          </details>
          <details @change="reviewed.delete(cap.id)">
            <summary>编辑目标与依据</summary>
            <div class="action-stack">
              <label>能力名称<input v-model="cap.title" maxlength="200" :disabled="busy" @input="reviewed.delete(cap.id)" /></label>
              <label>可验证的产出<textarea v-model="cap.observable_outcome" rows="2" maxlength="2000" :disabled="busy" @input="reviewed.delete(cap.id)" /></label>
              <label>资料覆盖
                <select v-model="cap.coverage_status" :disabled="busy">
                  <option value="covered">资料已覆盖</option><option value="partial">部分覆盖</option><option value="missing">缺少依据</option>
                </select>
              </label>
              <fieldset>
                <legend>先修能力</legend>
                <label v-for="prior in workspace.capabilities.filter(item => item.id !== cap.id)" :key="prior.id" class="check-label">
                  <input v-model="cap.prerequisite_ids" type="checkbox" :value="prior.id" :disabled="busy" />{{ prior.title }}
                </label>
              </fieldset>
              <fieldset>
                <legend>原文依据</legend>
                <label v-for="source in workspace.sources" :key="source.id" class="check-label">
                  <input v-model="cap.source_ids" type="checkbox" :value="source.id" :disabled="busy" />
                  <span><strong>{{ sourceName(source.id) }}</strong> · {{ name(source.state || '') }}<blockquote>{{ source.excerpt }}</blockquote></span>
                </label>
              </fieldset>
            </div>
          </details>
          <label v-if="cap.coverage_status === 'covered'" class="check-label">
            <input v-model="reviewed" type="checkbox" :value="cap.id" :disabled="busy" />已核对此项能力及其原文依据
          </label>
        </article>
        <p role="status">已核对 {{ reviewedCount }} / {{ covered.length }} 项
          <button v-if="!canConfirm" class="text-button" @click="focusUnchecked">查看待核对项</button>
        </p>
        <p v-if="planBlocker" class="workspace-notice">{{ planBlocker }}
          <button v-if="workspace.source_warning || !covered.length || covered.some(cap => !hasUsableSources(cap))" class="text-button" :disabled="busy" @click="beginCreate(workspace, true)">重新选择资料</button>
        </p>
        <div class="inline-actions">
          <button class="btn primary" :disabled="busy || !canConfirm || !!workspace.source_warning" @click="save(true)">确认目标并开始</button>
          <button class="btn secondary" :disabled="busy || !!workspace.source_warning" @click="save()">保存草稿</button>
        </div>
      </section>

      <template v-else>
        <div class="learning-tabs" aria-label="专题内容">
          <button :class="{ active: tab === 'task' }" @click="tab = 'task'">{{ feedback ? '本次反馈' : '当前任务' }}</button>
          <button :class="{ active: tab === 'history' }" @click="tab = 'history'; hint = undefined">学习记录</button>
        </div>

        <section v-if="tab === 'history'" class="panel learning-panel">
          <h3>学习记录</h3>
          <p v-if="!history?.attempts.length">还没有作答记录。</p>
          <div v-for="item in history?.attempts" :key="item.id" class="history-row">
            <span>{{ dateText(item.submitted_at) }} · {{ name(item.assessment_status) }}</span>
            <button class="text-button" :disabled="busy" @click="review(item)">回看作答</button>
          </div>
          <details v-if="history?.decisions.length || history?.hints.length">
            <summary>调整依据与帮助记录</summary>
            <p v-for="item in history?.decisions" :key="item.id">{{ item.reason }}</p>
            <p v-for="item in history?.hints" :key="item.id">{{ dateText(item.revealed_at) }} · 第 {{ item.level }} 级帮助</p>
          </details>
          <button class="text-button" @click="tab = 'task'">返回当前任务</button>
        </section>

        <section v-else-if="feedback" class="panel learning-panel">
          <LearningFeedback :attempt="feedback" :task="feedbackTask" :title="feedbackTitle(feedback)" :reason="decision?.reason"
            :source-name="sourceName" :show-actions="!busy && !running && !workspace.source_warning"
            @evidence="help(2, feedback.task_id)" @dispute="dispute(feedback)" />
          <LearningHint v-if="hint" :hint="hint" @download="download" />
          <p v-if="assessmentJob?.error" class="alert error-alert">{{ assessmentJob.error }}</p>
          <button v-if="assessmentJob?.status === 'failed'" class="btn primary" :disabled="busy || running || !!workspace.source_warning"
            @click="retry(assessmentJob.id)">重试原评价</button>
          <button v-else-if="feedback.assessment_status === 'completed' || feedback.invalidated" class="btn primary"
            :disabled="busy || running" @click="continueToTask">{{ recommendedTask ? '继续下一题' : '返回当前进度' }}</button>
          <p v-if="!modelReady">评价模型未配置，请由部署者配置后重试原评价。本次作答已保留。</p>
        </section>

        <section v-else-if="task" class="panel learning-panel">
          <div class="section-heading">
            <h3>{{ currentCap?.title || '当前任务' }}</h3>
            <small>{{ workspace.tasks.filter(item => item.status === 'completed').length }} / {{ workspace.tasks.length }} 题已完成</small>
          </div>
          <p class="learning-text">{{ task.prompt }}</p>
          <details v-if="task.rubric.length">
            <summary>评价要求</summary>
            <ul><li v-for="item in task.rubric" :key="item.id">{{ item.description }}</li></ul>
          </details>
          <p v-if="!modelReady" class="muted">评价模型未配置，仍可保存作答。</p>
          <form class="action-stack" @submit.prevent="submit">
            <label>你的作答<textarea :value="answer" rows="8" maxlength="10000" :disabled="busy || task.restricted || !!workspace.source_warning"
              placeholder="草稿会保存在当前浏览器。" @input="editAnswer" /></label>
            <div class="inline-actions">
              <button class="btn primary" :disabled="busy || running || task.restricted || !!workspace.source_warning || !answer.trim()">提交作答</button>
              <details v-if="!redoing"><summary>更多</summary><button class="text-button" type="button" :disabled="busy || running || !!workspace.source_warning" @click="skipConfirm = true">跳过</button></details>
              <button v-else class="text-button" type="button" :disabled="busy" @click="redoing = false; action(update)">返回当前任务</button>
            </div>
          </form>
          <details class="task-help">
            <summary>需要提示{{ task.hint_level ? `（当前第 ${task.hint_level} 级）` : '' }}</summary>
            <p>查看原文会记录为第 2 级帮助。阅读完整解析后的作答会按有帮助的口径评价。</p>
            <div class="inline-actions">
              <button v-for="(label, index) in hintNames" :key="label" class="btn secondary"
                :disabled="busy || task.restricted || !!workspace.source_warning" @click="help(index + 1)">{{ index + 1 }} · {{ label }}</button>
            </div>
            <LearningHint v-if="hint" :hint="hint" @download="download" />
          </details>
        </section>

        <section v-else class="panel learning-panel">
          <h3>{{ workspace.status === 'completed' ? '本专题已完成' : '暂时没有可用题目' }}</h3>
          <p>可在学习记录中回看作答和任务调整。</p>
          <button v-if="workspace.status !== 'completed' && !workspace.source_warning" class="btn secondary" :disabled="busy || running"
            @click="action(() => advance(false))">检查下一步</button>
        </section>
      </template>
    </template>

    <section v-else class="panel learning-panel">
      <p v-if="!listLoaded && !error" role="status">正在读取专题…</p>
      <template v-if="listLoaded">
        <div v-if="continueWith" class="section-heading">
          <h2>{{ continueWith.goal_text }}</h2>
          <button class="btn primary" :disabled="busy" @click="open(continueWith.id)">继续学习</button>
        </div>
        <div v-if="!summaries.length">
          <h2>从一个具体目标开始</h2><p>选择资料，练习解释和分析数据库事务场景。</p>
          <button class="btn primary" :disabled="busy" @click="beginCreate()">新建专题</button>
        </div>
        <ul v-else class="workspace-list">
          <li v-for="item in summaries" :key="item.id" class="history-row">
            <button class="text-button" :disabled="busy" @click="open(item.id)">{{ item.goal_text }}</button><small>{{ name(item.status) }}</small>
          </li>
        </ul>
      </template>
    </section>

    <ConfirmAction v-if="skipConfirm" title="跳过本题" description="这题会被记为跳过，不计为通过。本地草稿会保留。"
      label="确认跳过" :action="() => advance(true)" @close="skipConfirm = false" />
    <ConfirmAction v-if="removeConfirm" title="删除专题" description="会删除专题、作答和评价记录，资料库中的原始资料会保留。删除无法撤销。"
      :action="remove" @close="removeConfirm = false" />
    <WorkspaceDialog v-if="historical" title="历史作答" drawer @close="historical = undefined">
      <button autofocus class="text-button" @click="historical = undefined; tab = 'task'">返回当前任务</button>
      <LearningFeedback :attempt="historical.attempt" :task="historical.task" :title="feedbackTitle(historical.attempt)" :source-name="sourceName" />
      <div class="inline-actions">
        <button v-if="historical.task.status === 'completed' && !historical.task.restricted && !workspace?.source_warning" class="btn secondary"
          :disabled="busy || running" @click="redo(historical.task)">再次作答</button>
        <button v-if="historical.attempt.assessment.criteria?.length && !historical.attempt.invalidated" class="text-button"
          :disabled="busy || running" @click="dispute(historical.attempt)">我不同意这个判断</button>
      </div>
    </WorkspaceDialog>
    <WorkspaceDialog v-if="correction" title="记录对评价的争议" :busy="busy" @close="correction = undefined">
      <form class="action-stack" @submit.prevent="correct">
        <p>争议记录成功后，这次评价将停止影响能力状态和任务推荐。</p>
        <label>哪里需要纠正<textarea v-model="correction.reason" rows="4" maxlength="1000" /></label>
        <p v-if="error" class="alert error-alert" role="alert">{{ error }}</p>
        <div class="modal-actions">
          <button autofocus class="btn secondary" type="button" :disabled="busy" @click="correction = undefined">取消</button>
          <button class="btn primary" :disabled="busy || !correction.reason.trim()">记录争议</button>
        </div>
      </form>
    </WorkspaceDialog>
  </main>
</template>
