<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { ArrowRight, BookOpen, Check, ChevronLeft, Clock3, Lightbulb, LoaderCircle, Plus, RefreshCw, Target, Trash2 } from 'lucide-vue-next'
import { downloadDocument } from '../../api'
import type { Document, KnowledgeBase } from '../../api'
import { learning } from '../../api/learning'
import type { Attempt, LearningHistory, LearningTask, Source, Workspace, WorkspaceSummary } from '../../api/learning'
const props = defineProps<{ knowledgeBases: KnowledgeBase[]; documents: Document[]; userId: string; modelReady: boolean }>()
const summaries = ref<WorkspaceSummary[]>([]), workspace = ref<Workspace | null>(null)
const task = ref<LearningTask | null>(null), history = ref<LearningHistory | null>(null)
const creating = ref(false), busy = ref(false), error = ref(''), message = ref(''), tab = ref('task')
const kbId = ref(''), documentIds = ref<string[]>([]), goal = ref('我想理解数据库事务，并能分析并发场景下的数据异常。')
const answer = ref(''), selectedTaskId = ref(''), hint = ref<{ level: number; text: string; sources: Source[] } | null>(null)
const correction = ref<{ attempt: Attempt; reason: string; key: string } | null>(null)
let timer: ReturnType<typeof setInterval> | undefined, polling = false, disposed = false
let creationKey = crypto.randomUUID(), lastSubmission: { text: string; task: string; key: string } | null = null
const options = computed(() => props.documents.filter(d => d.knowledge_base_id === kbId.value))
const running = computed(() => workspace.value?.jobs.some(j => ['queued', 'running'].includes(j.status)) || false)
const visibleTask = computed(() => !!workspace.value && ['active', 'completed', 'needs_practice'].includes(workspace.value.status))
const labels: Record<string, string> = { generating: '生成草稿中', draft: '待确认目标', active: '学习中', completed: '验证完成', needs_practice: '需要更多练习', failed: '待重试', queued: '排队中', running: '评价中', unverified: '尚未验证', needs_evidence: '需要补充证据', assisted: '帮助下完成', independent: '独立完成', transfer_verified: '变式验证通过', suspected: '疑似障碍', confirmed: '有重复证据', disputed: '判断有争议', resolved: '已有解除依据', met: '满足标准', partial: '部分满足', not_met: '尚未满足', insufficient_evidence: '证据不足', pending: '待完成', skipped: '已跳过', current: '当前版本', historical: '历史版本', expired: '已过期', unavailable: '来源不可用', stock_value_vs_order_count: '库存值与订单数混淆', interleaving: '交错执行理解', prevention: '防护方法不足', expression: '表达需要补充' }
const name = (v: string) => labels[v] || v
const draftKey = (id: string) => `learning-draft:${props.userId}:${id}`
const currentCap = computed(() => workspace.value?.capabilities.find(c => c.id === task.value?.capability_id))
const sourceName = (id: string) => { const s = workspace.value?.sources.find(s => s.id === id); return s ? `${s.document_name}${s.page ? ` · 第${s.page}页` : ''}` : '来源已不可用' }
const requestKey = () => crypto.randomUUID()
const time = (v: string) => new Date(v.endsWith('Z') ? v : v + 'Z').toLocaleString('zh-CN')
function available(d: Document) { return !!d.active_revision_id && (!d.expires_at || new Date(/Z$|[+-]\d\d:\d\d$/.test(d.expires_at) ? d.expires_at : d.expires_at + 'Z').getTime() > Date.now()) }
async function action(fn: () => Promise<void>) { if (busy.value) return; busy.value = true; error.value = ''; try { await fn() } catch (e) { error.value = e instanceof Error ? e.message : '操作失败'; } finally { busy.value = false } }
async function list() { summaries.value = await learning.list() }
async function loadTask(id: string) {
  if (!id) { task.value = null; return }
  const changed = task.value?.id !== id
  const result = await learning.task(id)
  if (disposed) return
  task.value = result
  if (changed) { answer.value = localStorage.getItem(draftKey(id)) || ''; hint.value = null }
}
async function refresh() {
  const wid = workspace.value?.id
  if (!wid) { await list(); return }
  const [w, h] = await Promise.all([learning.get(wid), learning.history(wid)])
  if (disposed || workspace.value?.id !== wid) return
  workspace.value = w; history.value = h
  await loadTask(selectedTaskId.value || w.current_task_id || '')
}
async function open(id: string) { await action(async () => { selectedTaskId.value = ''; task.value = null; hint.value = null; workspace.value = await learning.get(id); tab.value = 'task'; creating.value = false; await refresh() }) }
async function create() { await action(async () => { const result = await learning.create({ knowledge_base_id: kbId.value, document_ids: documentIds.value, goal_text: goal.value, request_key: creationKey }); creationKey = crypto.randomUUID(); workspace.value = await learning.get(result.workspace_id); creating.value = false; selectedTaskId.value = ''; await refresh(); await list() }) }
async function save(confirm = false) { await action(async () => { if (!workspace.value) return; workspace.value = await learning.save(workspace.value); if (confirm) workspace.value = await learning.confirm(workspace.value); message.value = confirm ? '学习目标已确认，可以开始初始任务。' : '草稿已保存。'; await refresh(); await list() }) }
async function submit() { await action(async () => {
  if (!task.value || !answer.value.trim()) return
  const text = answer.value.trim(), id = task.value.id
  if (!lastSubmission || lastSubmission.text !== text || lastSubmission.task !== id) lastSubmission = { text, task: id, key: crypto.randomUUID() }
  await learning.submit(id, text, lastSubmission.key)
  localStorage.removeItem(draftKey(id)); message.value = '作答已保存，正在按标准评价。'; await refresh()
}) }
async function help(level: number) { await action(async () => { if (!task.value) return; hint.value = await learning.hint(task.value.id, level, crypto.randomUUID()); task.value.hint_level = Math.max(task.value.hint_level, level) }) }
async function review(id: string) { await action(async () => { selectedTaskId.value = id; await loadTask(id); tab.value = 'task' }) }
async function showEvidence(taskId: string) { await action(async () => { selectedTaskId.value = taskId; await loadTask(taskId); hint.value = await learning.hint(taskId, 2, crypto.randomUUID()); if (task.value) task.value.hint_level = Math.max(task.value.hint_level, 2); tab.value = 'task' }) }
async function next(skip: boolean) { await action(async () => { if (!workspace.value) return; workspace.value = await learning.next(workspace.value, skip); selectedTaskId.value = ''; await refresh() }) }
async function retry(id: string) { await action(async () => { await learning.retry(id); await refresh() }) }
async function correct() { await action(async () => { if (!workspace.value || !correction.value) return; workspace.value = await learning.correct(workspace.value, correction.value.attempt.id, correction.value.reason, correction.value.key); correction.value = null; selectedTaskId.value = ''; message.value = '争议已记录，原判定已暂停使用。请重新解释或作答以补充证据。'; await refresh() }) }
async function remove() { await action(async () => { if (!workspace.value || !window.confirm('删除这个专题的学习记录？原始资料会保留。')) return; await learning.remove(workspace.value.id); workspace.value = null; task.value = null; await list() }) }
async function copyPlan() { const w = workspace.value; if (!w) return; kbId.value = w.knowledge_base_id || ''; await nextTick(); documentIds.value = [...w.selected_document_ids]; goal.value = w.goal_text; creating.value = true }
watch(workspace, w => { const key = `learning-current:${props.userId}`; if (w) localStorage.setItem(key, w.id); else localStorage.removeItem(key) })
watch(kbId, () => { documentIds.value = [] })
watch(answer, value => { if (task.value) localStorage.setItem(draftKey(task.value.id), value) })
onMounted(() => { void action(async () => { await list(); const saved = localStorage.getItem(`learning-current:${props.userId}`); if (saved && summaries.value.some(w => w.id === saved)) { workspace.value = await learning.get(saved); await refresh() } }); timer = setInterval(async () => { if (polling || busy.value || !running.value || workspace.value?.status === 'draft') return; polling = true; try { await refresh(); await list() } catch (e) { error.value = e instanceof Error ? e.message : '状态更新失败' } finally { polling = false } }, 2000) })
onUnmounted(() => { disposed = true; clearInterval(timer) })
</script>

<template>
  <main class="page learning-page">
    <div class="page-heading"><div><span class="eyebrow">LEARN WITH A PURPOSE</span><h1>专题学习<span class="heading-dot">.</span></h1><p>围绕一个目标，用真实作答决定下一步。</p></div><div class="learning-actions"><button class="btn secondary" :disabled="busy" @click="action(refresh)"><RefreshCw :size="16" />刷新</button><button class="btn primary" @click="creating = true; message = ''"><Plus :size="16" />新建专题</button></div></div>
    <div v-if="error" class="alert error-alert" role="alert">{{ error }}</div>
    <p v-if="message" class="learning-notice" role="status">{{ message }}</p>
    <p v-if="!modelReady" class="learning-notice">尚未配置评价模型。可以核对资料、确认模板目标并保存作答，自动评价需配置模型后重试。</p>

    <section v-if="creating" class="panel learning-create">
      <div class="section-heading"><h2>建立学习目标</h2><button class="text-button" @click="creating = false">取消</button></div>
      <p class="muted">这一版先支持数据库事务专题。选择真实资料后，系统生成三个能力草稿；正式练习采用固定模板。</p>
      <form @submit.prevent="create"><label>知识库<select v-model="kbId" required><option value="" disabled>选择个人知识库</option><option v-for="kb in knowledgeBases" :key="kb.id" :value="kb.id">{{ kb.name }}</option></select></label>
        <label>学习目标<textarea v-model="goal" rows="3" minlength="10" maxlength="1000" required /></label>
        <fieldset><legend>选择资料（最多 10 份）</legend><p v-if="!options.length" class="muted">请先在知识空间上传资料并等待解析完成。</p><label v-for="doc in options" :key="doc.id" class="learning-check"><input v-model="documentIds" type="checkbox" :value="doc.id" :disabled="!available(doc) || (!documentIds.includes(doc.id) && documentIds.length >= 10)" /><span>{{ doc.name }} <small v-if="!available(doc)">尚不可用或已过期</small></span></label></fieldset>
        <button class="btn primary" :disabled="busy || !documentIds.length || goal.trim().length < 10"><LoaderCircle v-if="busy" class="spin" :size="16" />生成能力草稿<ArrowRight :size="16" /></button>
      </form>
    </section>

    <template v-else-if="workspace">
      <div class="learning-toolbar"><button class="text-button" @click="workspace = null; task = null; action(list)"><ChevronLeft :size="16" />全部专题</button><span class="outline-chip">{{ name(workspace.status) }}</span><button class="text-button" @click="copyPlan">按此目标新建草稿</button><button class="icon-button" aria-label="删除专题" @click="remove"><Trash2 :size="16" /></button></div>
      <h2 class="learning-goal">{{ workspace.goal_text }}</h2>
      <p v-if="workspace.source_warning" class="alert error-alert">{{ workspace.source_warning }}</p>
      <div v-for="job in workspace.jobs.filter(j => ['queued', 'running', 'failed'].includes(j.status))" :key="job.id" class="learning-job panel"><LoaderCircle v-if="job.status !== 'failed'" class="spin" :size="16" /><Clock3 v-else :size="16" /><div><strong>{{ job.stage }}</strong><p v-if="job.error">{{ job.error }}</p></div><button v-if="job.status === 'failed'" class="btn secondary" :disabled="busy || running" @click="retry(job.id)">重试</button></div>

      <section v-if="workspace.status === 'draft'" class="learning-plan">
        <p class="learning-notice">请核对能力与原文。标记“资料已覆盖”表示你确认这些片段能支持该能力及练习；缺口部分不会生成计分任务。</p>
        <article v-for="cap in workspace.capabilities" :key="cap.id" class="panel learning-cap-edit"><span class="eyebrow">能力 {{ cap.ordinal + 1 }}</span><label>能力名称<input v-model="cap.title" maxlength="200" /></label><label>可验证的产出<textarea v-model="cap.observable_outcome" rows="2" maxlength="2000" /></label><label>资料覆盖<select v-model="cap.coverage_status"><option value="covered">资料已覆盖（已核对）</option><option value="partial">部分覆盖 / 待核对</option><option value="missing">缺少依据</option></select></label>
          <details><summary>先修关系与原文依据</summary><fieldset><legend>先修能力</legend><label v-for="prior in workspace.capabilities.filter(c => c.id !== cap.id)" :key="prior.id" class="learning-check"><input v-model="cap.prerequisite_ids" type="checkbox" :value="prior.id" />{{ prior.title }}</label></fieldset><label v-for="source in workspace.sources" :key="source.id" class="learning-check learning-source"><input v-model="cap.source_ids" type="checkbox" :value="source.id" /><span><strong>{{ sourceName(source.id) }}</strong><small>{{ name(source.state || '') }}</small><blockquote>{{ source.excerpt }}</blockquote></span></label><p v-if="!workspace.sources.length">没有找到匹配片段，请补充资料后重新创建草稿。</p></details>
        </article>
        <div class="learning-actions"><button class="btn secondary" :disabled="busy" @click="save(false)">保存草稿</button><button class="btn primary" :disabled="busy" @click="save(true)"><Check :size="16" />确认目标并开始</button></div>
      </section>

      <template v-if="visibleTask">
        <div class="learning-tabs"><button :class="{ active: tab === 'task' }" @click="tab = 'task'">当前学习</button><button :class="{ active: tab === 'history' }" @click="tab = 'history'">作答与调整记录</button></div>
        <div v-if="tab === 'task'" class="learning-layout">
          <aside class="panel learning-capabilities"><h3><Target :size="18" />能力目标</h3><article v-for="cap in workspace.capabilities" :key="cap.id" :class="{ active: cap.id === task?.capability_id }"><strong>{{ cap.ordinal + 1 }}. {{ cap.title }}</strong><p>{{ cap.observable_outcome }}</p><span class="outline-chip">{{ name(cap.state) }}</span><small v-if="cap.coverage_status !== 'covered'">资料存在缺口</small></article><div v-for="o in workspace.obstacles" :key="o.id" class="learning-obstacle"><strong>{{ name(o.status) }}</strong><p>{{ name(o.obstacle_code) }}</p><small>{{ o.evidence_refs.length }} 条作答依据</small></div></aside>
          <section class="panel learning-task">
            <template v-if="task"><div class="section-heading"><span class="eyebrow">{{ task.template_id }} · {{ currentCap?.title }}</span><button v-if="selectedTaskId" class="text-button" @click="selectedTaskId = ''; action(refresh)">返回当前任务</button></div><h3>本次需要完成的产出</h3><p class="learning-prompt">{{ task.prompt }}</p><details><summary>评价标准</summary><ol><li v-for="criterion in task.rubric" :key="criterion.id">{{ criterion.description }}</li></ol></details><p class="muted">本题已获得的最高帮助：{{ task.hint_level }} 级。未使用系统帮助不代表未查阅外部资料。</p><label>你的分析<textarea v-model="answer" rows="9" maxlength="10000" placeholder="写下推理过程。草稿会保留在当前浏览器。" :disabled="task.restricted" /></label><div class="learning-actions"><button class="btn primary" :disabled="busy || running || task.restricted || !answer.trim()" @click="submit"><LoaderCircle v-if="running" class="spin" :size="16" />{{ running ? '评价处理中' : '提交作答' }}</button><button v-if="!selectedTaskId" class="btn secondary" :disabled="busy || running" @click="next(true)">跳过（不记为通过）</button></div></template>
            <template v-else><BookOpen :size="30" /><h3>{{ workspace.status === 'completed' ? '本专题的变式验证已完成' : '暂时没有可用的下一题' }}</h3><p>可以回看记录。若资料或题目不足，请补充资料并新建草稿；跳过不代表已经掌握。</p><button v-if="workspace.status !== 'completed'" class="btn secondary" :disabled="busy" @click="next(false)">检查下一步</button></template>
          </section>
          <aside v-if="task" class="panel learning-help"><h3><Lightbulb :size="18" />按需获得帮助</h3><p class="muted">每次请求都会记录，完整解析后的完成将与独立作答区分。</p><button v-for="(label, index) in ['提醒条件', '查看相关原文', '局部示例', '完整解析']" :key="label" class="btn secondary" :disabled="busy || task.restricted" @click="help(index + 1)">{{ index + 1 }} · {{ label }}</button><div v-if="hint" class="learning-hint"><strong>第 {{ hint.level }} 级帮助</strong><p>{{ hint.text }}</p><div v-for="source in hint.sources" :key="source.id"><strong>{{ sourceName(source.id) }}</strong><blockquote>{{ source.excerpt }}</blockquote><button v-if="source.document_id" class="text-button" @click="action(async () => { if (source.document_id) await downloadDocument(source.document_id, source.document_name, source.revision_id) })">下载任务采用的原文版本</button></div></div></aside>
        </div>
        <section v-if="history" class="learning-feedback">
          <h3>{{ tab === 'task' ? '最近一次提交与下一步依据' : '学习记录' }}</h3>
          <article v-for="a in (tab === 'task' ? history.attempts.slice(0, 1) : history.attempts)" :key="a.id" class="panel learning-attempt"><div class="section-heading"><strong>{{ time(a.submitted_at) }} · {{ name(a.assessment_status) }}</strong><span class="outline-chip">{{ a.hint_level_at_submit ? `${a.hint_level_at_submit} 级帮助下作答` : '未使用系统帮助' }}</span></div><p v-if="a.invalidated" class="learning-notice">此评价已被提出争议，不再用于能力和任务调整。</p><details><summary>查看提交原文</summary><pre>{{ a.answer_text }}</pre></details><p>{{ a.assessment.feedback || (a.assessment_status === 'completed' ? '来源不可用，评价内容已隐藏。' : '作答已保存，等待有效评价。') }}</p><div v-for="c in a.assessment.criteria || []" :key="c.criterion_id" class="learning-criterion"><strong>{{ c.criterion_id }} · {{ name(c.verdict) }}</strong><blockquote v-for="(q, i) in c.student_evidence" :key="i">{{ q.quote }}</blockquote><p>{{ c.reason }}</p><button class="text-button learning-evidence-link" @click="showEvidence(a.task_id)">原文依据：{{ c.source_ids.map(sourceName).join('；') }}（查看会记录第 2 级帮助）</button></div><p v-for="o in a.assessment.obstacle_candidates || []" :key="o.code" class="muted">疑似：{{ name(o.code) }}。其他解释：{{ o.alternative_explanation }}</p><div class="learning-actions"><button class="text-button" :disabled="busy" @click="review(a.task_id)">查看任务 / 再次作答</button><button v-if="a.assessment_status === 'completed' && !a.invalidated && a.assessment.criteria?.length" class="text-button" :disabled="running" @click="correction = { attempt: a, reason: '', key: requestKey() }">我不同意这个判断</button></div></article>
          <article v-for="d in (tab === 'task' ? history.decisions.slice(0, 1) : history.decisions)" :key="d.id" class="panel learning-decision"><span class="eyebrow">{{ d.kind === 'correction' ? '用户纠正' : '下一步调整' }} · {{ d.rule_version }}</span><p>{{ d.reason }}</p><small>{{ d.status === 'superseded' ? '已被纠正后的判断替代' : `${d.evidence_refs.length} 条提交依据` }}</small></article>
          <details v-if="tab === 'history'"><summary>提示使用记录（最近 50 条）</summary><p v-for="h in history.hints" :key="h.id">{{ time(h.revealed_at) }} · 第 {{ h.level }} 级帮助</p></details>
        </section>
      </template>
    </template>

    <section v-else class="learning-list"><div v-if="!summaries.length" class="panel learning-empty"><Target :size="38" /><h2>从一个具体目标开始</h2><p>选择事务教材与课程笔记，通过解释、场景分析和实践方案，逐步验证你能做什么。</p><button class="btn primary" @click="creating = true">创建第一个专题<ArrowRight :size="16" /></button></div><button v-for="w in summaries" :key="w.id" class="panel learning-summary" @click="open(w.id)"><span class="stat-icon teal"><Target :size="22" /></span><span><strong>{{ w.goal_text }}</strong><small>{{ name(w.status) }} · {{ time(w.updated_at) }}</small></span><ArrowRight :size="18" /></button></section>

    <div v-if="correction" class="modal-backdrop" @click.self="correction = null"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="correction-title"><h2 id="correction-title">纠正这次评价</h2><p>记录争议后，此次评价将停止影响能力和推荐。你可以重新作答，补充澄清证据。</p><label>哪里需要纠正<textarea v-model="correction.reason" rows="4" maxlength="1000" placeholder="例如：我理解订单数，但在回答里省略了说明。" /></label><div class="modal-actions"><button class="btn secondary" @click="correction = null">取消</button><button class="btn primary" :disabled="busy || !correction.reason.trim()" @click="correct">记录争议并重新验证</button></div></section></div>
  </main>
</template>

<style scoped>
.learning-page{max-width:1600px}.learning-actions,.learning-toolbar,.learning-job{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.learning-toolbar{margin:18px 0}.learning-toolbar .outline-chip{margin-right:auto}.learning-goal{font-size:21px;line-height:1.65;margin:20px 0}.learning-create{padding:28px;max-width:900px}.learning-create form,.learning-cap-edit{display:grid;gap:18px}.learning-create form{margin-top:24px}.learning-check{display:flex;align-items:flex-start;gap:10px;margin:12px 0;font-weight:400}.learning-check input{width:16px;flex-shrink:0;margin-top:4px}.learning-check span{min-width:0}.learning-check small,.learning-summary small{display:block;color:#73827e;margin-top:5px}.learning-source{padding-top:12px;border-top:1px solid #e5ece9}.learning-source blockquote{max-height:200px;overflow:auto}.learning-cap-edit{padding:24px;margin-bottom:16px}.learning-cap-edit details{min-width:0}.learning-plan{max-width:1000px}.learning-notice{padding:14px 18px;background:#eef6f2;border:1px solid #dbe9e1;border-radius:10px;color:#3b6252;line-height:1.7;margin:16px 0}.learning-job{padding:16px;margin:12px 0}.learning-job>div{flex:1}.learning-job p{margin:6px 0 0;color:#8d5544}.learning-tabs{display:flex;border-bottom:1px solid #dae4df;margin:24px 0}.learning-tabs button{padding:14px 20px;border-bottom:2px solid transparent;color:#6c7f76}.learning-tabs .active{color:#277555;border-bottom-color:#277555}.learning-layout{display:grid;grid-template-columns:240px minmax(300px,1fr) 270px;gap:18px;align-items:start}.learning-layout>.panel{padding:22px;min-width:0}.learning-layout h3{display:flex;gap:8px;align-items:center;margin:0 0 18px}.learning-capabilities article{padding:18px 0;border-bottom:1px solid #e4eae7}.learning-capabilities article.active{border-left:3px solid #3f8b67;padding-left:12px}.learning-capabilities p,.learning-help p{font-size:13px;line-height:1.8;color:#748078}.learning-capabilities small{display:block;margin-top:8px;color:#976c4a}.learning-obstacle{margin-top:20px;background:#faf6ed;padding:12px;border-radius:8px}.learning-prompt{white-space:pre-wrap;line-height:1.9;margin:16px 0}.learning-task details{margin:18px 0}.learning-task li{margin:10px 0;line-height:1.7}.learning-task textarea{margin:10px 0 18px;line-height:1.85}.learning-help>.btn{width:100%;justify-content:flex-start;margin:7px 0}.learning-hint{margin-top:22px;line-height:1.85;white-space:pre-wrap;font-size:13px}.learning-feedback{margin-top:28px}.learning-attempt,.learning-decision{padding:24px;margin:16px 0;line-height:1.8;overflow-wrap:anywhere}.learning-attempt pre{white-space:pre-wrap;font:inherit;background:#f7f9f7;padding:14px;border-radius:8px}.learning-criterion{border-top:1px solid #e5eae7;margin-top:16px;padding-top:16px}.learning-criterion small{color:#748078}blockquote{border-left:3px solid #b7cdc0;margin:12px 0;padding:8px 14px;white-space:pre-wrap;line-height:1.8;color:#65776c;font-size:13px}.learning-list{display:grid;gap:16px}.learning-summary{display:flex;text-align:left;align-items:center;gap:20px;padding:24px;width:100%}.learning-summary>span:nth-child(2){flex:1}.learning-summary strong{line-height:1.7}.learning-empty{text-align:center;padding:64px 30px}.learning-empty>svg{color:#448267;margin-bottom:20px}.learning-empty p{max-width:500px;margin:18px auto 25px;line-height:1.9;color:#748078}fieldset{border:1px solid #e1e8e3;border-radius:10px;padding:15px}legend{padding:0 8px;font-size:13px;color:#66786e}label{display:flex;flex-direction:column;gap:8px}label.learning-check{flex-direction:row}select{width:100%;padding:12px;border:1px solid #dce4df;border-radius:8px;background:white;color:#334d3e}details summary{cursor:pointer;line-height:1.8}input,textarea{width:100%}@media(max-width:1250px){.learning-layout{grid-template-columns:200px minmax(280px,1fr)}.learning-help{grid-column:2}}@media(max-width:800px){.learning-layout{grid-template-columns:1fr}.learning-help{grid-column:auto}.learning-capabilities{order:3}.learning-actions .btn{flex:1}.learning-create{padding:20px}.learning-toolbar{gap:10px}.learning-goal{font-size:18px}}
</style>
