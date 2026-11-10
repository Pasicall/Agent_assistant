<script setup lang="ts">
import type { Attempt, LearningTask } from '../../api/learning'
import { dateText } from '../../workspace'

defineProps<{ attempt: Attempt; task?: LearningTask; title: string; reason?: string; sourceName: (id: string) => string; showActions?: boolean }>()
const emit = defineEmits<{ evidence: []; dispute: [] }>()
const verdicts: Record<string, string> = { met: '满足标准', partial: '部分满足', not_met: '尚未满足', insufficient_evidence: '证据不足' }
</script>

<template>
  <section class="learning-feedback">
    <p class="workspace-notice">作答已保存</p>
    <h3>{{ title }}</h3>
    <p class="muted">{{ dateText(attempt.submitted_at) }} · {{ attempt.hint_level_at_submit ? `第 ${attempt.hint_level_at_submit} 级帮助下作答` : '未使用系统帮助' }}</p>
    <p v-if="attempt.assessment.feedback">{{ attempt.assessment.feedback }}</p>
    <section v-if="attempt.assessment.criteria?.some(item => item.verdict !== 'met')">
      <h4>关键改进点</h4>
      <ul><li v-for="criterion in attempt.assessment.criteria.filter(item => item.verdict !== 'met')" :key="criterion.criterion_id">{{ criterion.reason }}</li></ul>
    </section>
    <p v-if="reason"><strong>下一题理由</strong> {{ reason }}</p>
    <details>
      <summary>本次题目与作答</summary>
      <p v-if="task" class="learning-text">{{ task.prompt }}</p>
      <pre class="learning-text">{{ attempt.answer_text }}</pre>
    </details>
    <details v-if="attempt.assessment.criteria?.length">
      <summary>逐项标准与依据</summary>
      <article v-for="criterion in attempt.assessment.criteria" :key="criterion.criterion_id" class="source-card">
        <h4>{{ task?.rubric.find(item => item.id === criterion.criterion_id)?.description || criterion.criterion_id }}</h4>
        <strong>{{ verdicts[criterion.verdict] || criterion.verdict }}</strong>
        <p>{{ criterion.reason }}</p>
        <blockquote v-for="(quote, index) in criterion.student_evidence" :key="index">{{ quote.quote }}</blockquote>
        <small v-if="criterion.source_ids.length">{{ criterion.source_ids.map(sourceName).join(' · ') }}</small>
      </article>
      <p v-for="item in attempt.assessment.obstacle_candidates" :key="item.code">{{ item.alternative_explanation }}</p>
      <div v-if="showActions" class="inline-actions">
        <button v-if="!task?.restricted" class="text-button" @click="emit('evidence')">查看原文（记录第 2 级帮助）</button>
        <button v-if="!attempt.invalidated" class="text-button" @click="emit('dispute')">我不同意这个判断</button>
      </div>
    </details>
  </section>
</template>
