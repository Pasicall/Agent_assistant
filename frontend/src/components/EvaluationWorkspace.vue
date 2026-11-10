<script setup lang="ts">
import { computed, onMounted, ref, toRef } from 'vue'
import { BookOpen, Check, ChevronDown, ChevronRight, Clock3, FileCheck2, FlaskConical, Layers3, LoaderCircle, Plus, Trash2 } from 'lucide-vue-next'
import { api, type Comparison, type Conversation, type Evaluation, type EvaluationCase, type Message } from '../api'
import type { Corpus } from '../api/corpus'
import { available, dateText as date, errorText, useVisiblePolling } from '../workspace'

const props = defineProps<{ corpus: Corpus; active: boolean }>()
const knowledgeBases = computed(() => props.corpus.state.bases)
const documents = computed(() => props.corpus.state.documents)
const readyDocuments = computed(() => documents.value.some(available))
const error = ref('')
const notice = ref('')
const fail = (reason: unknown) => { error.value = errorText(reason) }
const notify = (message: string) => { notice.value = message }
const conversations = ref<Conversation[]>([])
const messages = ref<Message[]>([])
let readSequence = 0
async function readConversations() {
  try { conversations.value = await api('/conversations') } catch (reason) { fail(reason) }
}
async function readMessages(id: string) {
  const ticket = ++readSequence
  messages.value = []
  if (!id) return
  try {
    const result = await api<{ messages: Message[] }>(`/conversations/${id}`)
    if (ticket === readSequence) messages.value = result.messages
  } catch (reason) { if (ticket === readSequence) fail(reason) }
}
function addHistoryCase(message: Message) {
  const index = messages.value.findIndex(item => item.id === message.id)
  const question = messages.value.slice(0, index).reverse().find(item => item.role === 'user')?.content
  if (!question || evaluationCases.value.some(item => item.message_id === message.id)) return
  const testCase = { question, reference: '', expected_document_ids: [], message_id: message.id }
  const initial = evaluationCases.value[0]
  if (evaluationCases.value.length === 1 && initial && !initial.question && !initial.reference) evaluationCases.value = [testCase]
  else evaluationCases.value.push(testCase)
  notify('已加入历史答案，填写参考答案后即可复评。')
}
const evaluations = ref<Evaluation[]>([])
const evaluation = ref<Evaluation | null>(null)
const evaluationName = ref('')
const evaluationCases = ref<EvaluationCase[]>([{ question: '', reference: '', expected_document_ids: [] }])
const evaluationKbIds = ref<string[]>([])
const useRagas = ref(false)
const evaluating = ref(false)
const comparing = ref(false)
const comparison = ref<Comparison | null>(null)
const evaluationLoading = ref(false)
const metricNames: Record<string, string> = { hit_rate: '命中率', recall: '召回率', precision: '精确率', mrr: 'MRR', mean_reciprocal_rank: 'MRR', context_precision: '上下文精确率', context_recall: '上下文召回率', answer_relevancy: '答案相关性', faithfulness: '忠实度', answer_similarity: '答案相似度', citation_coverage: '引用覆盖率', avg_latency_ms: '平均延迟 (ms)', latency_ms: '延迟 (ms)', lexical_overlap: '词项重合度', reference_overlap: '参考答案重合度', case_count: '测试用例', count: '数量', retrieval_hit_rate: '检索命中率', hit_at_5: 'Hit@5 命中率', recall_at_5: 'Recall@5 召回率', mrr_at_5: 'MRR@5', labeled_cases: '已标注用例', total_cases: '总用例', reference_token_recall: '参考词项召回率', ragas_enabled: 'Ragas 评测', reciprocal_rank: '倒数排名', question: '问题', reference: '参考答案', answer: '生成答案', contexts: '检索上下文', citations: '来源引用', expected_document_ids: '期望来源 ID', retrieved_document_ids: '检索来源 ID', provider: '回答模型', cached: '缓存命中' }
function display(value: unknown): string {
  if (value === undefined || value === null) return '—'
  if (typeof value === 'object') return JSON.stringify(value, null, 2)
  return String(value)
}
function metric(value: unknown) {
  if (typeof value === 'boolean') return value ? '已启用' : '未启用'
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(3)
  return display(value)
}
function comparisonMetrics(result: Record<string, unknown>): Record<string, unknown> {
  return result.metrics && typeof result.metrics === 'object' && !Array.isArray(result.metrics) ? result.metrics as Record<string, unknown> : result
}
function statusText(status: string) {
  return ({ ready: '已就绪', completed: '已完成', queued: '排队中', pending: '排队中', processing: '处理中', cancelled: '已取消', running: '运行中', parsing: '解析中', indexing: '索引中', failed: '处理失败' } as Record<string, string>)[status] || status
}
function toggleId(ids: string[], id: string) { const index = ids.indexOf(id); index >= 0 ? ids.splice(index, 1) : ids.push(id) }
function addCase() { evaluationCases.value.push({ question: '', reference: '', expected_document_ids: [] }) }
function validCases(): EvaluationCase[] | null {
  if (!evaluationCases.value.length || evaluationCases.value.some(item => !item.question.trim() || !item.reference.trim())) {
    error.value = '请为每个测试用例填写问题和参考答案。'; return null
  }
  return evaluationCases.value.map(item => ({ question: item.question.trim(), reference: item.reference.trim(), expected_document_ids: item.expected_document_ids || [], ...(item.message_id ? { message_id: item.message_id } : {}) }))
}
async function runEvaluation() {
  const cases = validCases(); if (!cases) return
  const previousIds = new Set(evaluations.value.map(item => item.id))
  evaluating.value = true; error.value = ''
  try {
    const result = await api<Evaluation>('/evaluations', { method: 'POST', body: JSON.stringify({ name: evaluationName.value.trim() || `评测 ${new Date().toLocaleString('zh-CN')}`, cases, knowledge_base_ids: evaluationKbIds.value, use_ragas: useRagas.value }) })
    evaluation.value = result
    evaluations.value = [result, ...evaluations.value.filter(item => item.id !== result.id)]
    notify('评测已提交。')
    await loadRuns(true)
  } catch (reason) {
    fail(reason)
    try {
      evaluations.value = await api<Evaluation[]>('/evaluations')
      const savedFailure = evaluations.value.find(item => !previousIds.has(item.id))
      if (savedFailure) evaluation.value = savedFailure
    } catch { /* Preserve the actionable error from the original evaluation request. */ }
  } finally { evaluating.value = false }
}
async function compareBaseline() {
  const cases = validCases(); if (!cases) return
  if (cases.some(item => !item.expected_document_ids?.length)) { error.value = '比较检索命中率前，请为每个用例选择至少一篇期望命中的文档。'; return }
  comparing.value = true; error.value = ''
  try { comparison.value = await api<Comparison>('/evaluations/compare', { method: 'POST', body: JSON.stringify({ cases: cases.map(({ message_id: _messageId, ...item }) => item), knowledge_base_ids: evaluationKbIds.value }) }) }
  catch (reason) { fail(reason) } finally { comparing.value = false }
}
async function openEvaluation(id: string) {
  if (evaluationLoading.value) return
  evaluationLoading.value = true
  try { evaluation.value = await api<Evaluation>(`/evaluations/${id}`) } catch (reason) { fail(reason) } finally { evaluationLoading.value = false }
}

let loadingRuns = false
async function loadRuns(saved = false) {
  if (loadingRuns) return
  loadingRuns = true
  try {
    evaluations.value = await api('/evaluations')
    const current = evaluation.value
    if (current && ['queued', 'running'].includes(current.status) && !evaluationLoading.value) {
      const result = await api<Evaluation>(`/evaluations/${current.id}`)
      if (evaluation.value?.id === current.id) evaluation.value = result
    }
  } catch (reason) {
    if (saved) notify('评测已提交，列表暂未刷新。')
    else fail(reason)
  } finally { loadingRuns = false }
}
useVisiblePolling(toRef(props, 'active'), () => evaluations.value.some(item => ['queued', 'running'].includes(item.status)), loadRuns)
onMounted(() => { void loadRuns() })
</script>

<template>
      <main class="page evaluation-page"><div class="page-heading"><div><span class="eyebrow">MEASURE WHAT MATTERS</span><h1>开发工具</h1><p>用真实测试用例，衡量检索与回答的每一次进步。</p></div><span class="outline-chip"><FlaskConical :size="15" />Evidence over assumptions</span></div>
        <p v-if="error" class="alert error-alert" role="alert">{{ error }} <button class="text-button" @click="loadRuns()">重试读取</button></p>
        <p v-if="notice" class="workspace-notice" role="status">{{ notice }}</p>
        <details class="panel development-history">
          <summary>从历史对话选择答案或查看检索轨迹</summary>
          <div class="action-stack">
            <button v-if="!conversations.length" class="btn secondary" @click="readConversations">读取历史会话</button>
            <label v-if="conversations.length">会话<select @change="readMessages(($event.target as HTMLSelectElement).value)">
              <option value="">选择会话</option><option v-for="item in conversations" :key="item.id" :value="item.id">{{ item.title }}</option>
            </select></label>
            <article v-for="item in messages.filter(row => row.role === 'assistant' && !row.restricted)" :key="item.id" class="source-card">
              <p>{{ item.content }}</p>
              <button class="text-button" @click="addHistoryCase(item)">加入评测</button>
              <details v-if="item.trace?.length"><summary>检索轨迹</summary>
                <pre v-for="(trace, i) in item.trace" :key="i">{{ trace.step }} {{ display(trace.detail) }}</pre>
              </details>
            </article>
          </div>
        </details>
        <section class="panel evaluation-builder"><div class="section-heading"><h2>创建评测 <span>TEST SUITE</span></h2><button class="text-button" :disabled="evaluating || comparing" @click="addCase"><Plus :size="15" />添加用例</button></div><div class="evaluation-config"><label>评测名称<input v-model="evaluationName" placeholder="例如：产品知识库回归评测" maxlength="120" /></label><div><span class="field-label">知识库范围 <small>不选时使用全部</small></span><div class="kb-chips"><button v-for="kb in knowledgeBases" :key="kb.id" :class="{ selected: evaluationKbIds.includes(kb.id) }" @click="toggleId(evaluationKbIds, kb.id)"><Check v-if="evaluationKbIds.includes(kb.id)" :size="12" /><BookOpen v-else :size="12" />{{ kb.name }}</button><span v-if="!knowledgeBases.length" class="muted">尚无知识库</span></div></div></div>
          <div class="case-list"><div v-for="(testCase, index) in evaluationCases" :key="index" class="evaluation-case"><div class="case-heading"><span>CASE <strong>{{ String(index + 1).padStart(2, '0') }}</strong><em v-if="testCase.message_id" class="history-case-badge"><Clock3 :size="11" />历史答案 · 不重新生成</em></span><button class="icon-button danger-button" aria-label="删除用例" :disabled="evaluationCases.length === 1 || evaluating || comparing" @click="evaluationCases.splice(index, 1)"><Trash2 :size="15" /></button></div><div class="case-fields"><label>测试问题<textarea v-model="testCase.question" :readonly="!!testCase.message_id" :title="testCase.message_id ? '历史复评使用存档问题' : ''" rows="2" placeholder="希望从知识库中回答的问题" maxlength="8000" /></label><label>参考答案<textarea v-model="testCase.reference" rows="2" placeholder="用于比较的正确答案或关键要点" maxlength="20000" /></label></div><details class="expected-documents"><summary><FileCheck2 :size="14" />期望命中的文档 <span>{{ testCase.expected_document_ids?.length || 0 }} 份</span><ChevronDown :size="13" /></summary><p>用于真实命中率与 Dense / Hybrid 比较；请选中问题应引用的来源。</p><div class="expected-list"><label v-for="doc in documents.filter(d => !evaluationKbIds.length || evaluationKbIds.includes(d.knowledge_base_id))" :key="doc.id"><input type="checkbox" :checked="testCase.expected_document_ids?.includes(doc.id)" @change="toggleId(testCase.expected_document_ids!, doc.id)" /><span>{{ doc.name }}</span></label><span v-if="!documents.length" class="muted">上传文档后即可设置检索目标。</span></div></details></div></div>
          <div class="evaluation-actions"><label class="checkbox-label"><input v-model="useRagas" type="checkbox" />启用 Ragas <small>需要配置真实模型</small></label><div class="button-row"><button class="btn secondary" :disabled="evaluating || comparing || !readyDocuments" @click="compareBaseline"><LoaderCircle v-if="comparing" :size="15" class="spin" /><Layers3 v-else :size="15" />比较检索基线</button><button class="btn primary" :disabled="evaluating || comparing || !readyDocuments" @click="runEvaluation"><LoaderCircle v-if="evaluating" :size="16" class="spin" /><FlaskConical v-else :size="16" />{{ evaluating ? '评测运行中…' : '运行评测' }}</button></div></div>
        </section>
        <section v-if="comparison" class="panel comparison-panel"><div class="section-heading"><h2>检索基线比较</h2><span class="outline-chip">同一测试集 · 同一知识范围</span></div><div class="comparison-grid"><div><span class="eyebrow">DENSE</span><h3>向量检索</h3><dl><template v-for="(value, key) in comparisonMetrics(comparison.dense)" :key="key"><dt>{{ metricNames[String(key)] || key }}</dt><dd>{{ metric(value) }}</dd></template></dl></div><div><span class="eyebrow">HYBRID</span><h3>混合检索</h3><dl><template v-for="(value, key) in comparisonMetrics(comparison.hybrid)" :key="key"><dt>{{ metricNames[String(key)] || key }}</dt><dd>{{ metric(value) }}</dd></template></dl></div><div class="comparison-delta"><span>命中率相对变化</span><strong>{{ typeof comparison.relative_hit_rate_change === 'number' ? `${comparison.relative_hit_rate_change > 0 ? '+' : ''}${(comparison.relative_hit_rate_change * 100).toFixed(1)}%` : '无法计算' }}</strong><p>仅反映当前测试集的实际结果。<br>基线为零时，相对变化可能无法计算。</p></div></div></section>
        <div class="evaluation-results-layout"><section class="panel evaluation-results"><div class="section-heading"><h2>评测结果</h2><span v-if="evaluation" class="status-badge" :class="evaluation.status"><span class="status-dot"></span>{{ statusText(evaluation.status) }}</span></div><div v-if="evaluationLoading" class="empty-card"><LoaderCircle :size="22" class="spin" /><p>正在读取评测结果…</p></div><div v-else-if="!evaluation" class="empty-card"><span class="empty-icon"><FlaskConical :size="28" /></span><h3>让数据，给出判断</h3><p>运行评测或选择历史记录，查看真实指标与逐条结果。</p></div><template v-else><div class="result-title"><h3>{{ evaluation.name }}</h3><span>{{ date(evaluation.created_at) }}</span></div><div v-if="evaluation.error" class="alert error-alert">{{ evaluation.error }}</div><div v-if="Object.keys(evaluation.metrics || {}).length" class="result-metrics"><div v-for="(value, key) in evaluation.metrics" :key="key"><span>{{ metricNames[String(key)] || key }}</span><strong>{{ metric(value) }}</strong></div></div><p v-else class="muted result-note">当前运行尚未返回指标。</p><details v-for="(result, index) in evaluation.results || []" :key="index" class="case-result"><summary><span class="case-number">{{ index + 1 }}</span><strong>{{ result.question || `用例 ${index + 1}` }}</strong><ChevronDown :size="15" /></summary><dl><template v-for="(value, key) in result" :key="key"><dt>{{ metricNames[String(key)] || key }}</dt><dd><pre>{{ display(value) }}</pre></dd></template></dl></details></template></section><aside class="panel evaluation-history"><div class="section-heading"><h2>历史运行</h2><span>{{ evaluations.length }}</span></div><div v-if="!evaluations.length" class="history-empty"><Clock3 :size="25" /><p>评测运行记录<br>会自动保存在这里。</p></div><button v-for="run in evaluations" :key="run.id" class="evaluation-history-item" :class="{ active: evaluation?.id === run.id }" @click="openEvaluation(run.id)"><span class="history-run-icon"><FlaskConical :size="16" /></span><span><strong>{{ run.name }}</strong><small>{{ date(run.created_at) }} · {{ statusText(run.status) }}</small></span><ChevronRight :size="14" /></button></aside></div>
      </main>

</template>
