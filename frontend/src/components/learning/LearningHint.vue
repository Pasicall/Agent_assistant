<script setup lang="ts">
import type { Source } from '../../api/learning'
defineProps<{ hint: { level: number; text: string; sources: Source[] } }>()
defineEmits<{ download: [source: Source] }>()
</script>

<template>
  <div class="source-card" role="status">
    <strong>第 {{ hint.level }} 级帮助</strong>
    <p class="learning-text">{{ hint.text }}</p>
    <div v-for="source in hint.sources" :key="source.id">
      <strong>{{ source.document_name }}{{ source.page ? ` · 第 ${source.page} 页` : '' }}</strong>
      <blockquote>{{ source.excerpt }}</blockquote>
      <button v-if="source.document_id" class="text-button" @click="$emit('download', source)">下载任务采用的原文版本</button>
    </div>
  </div>
</template>
