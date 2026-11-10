<script setup lang="ts">
import { ref } from 'vue'
import { errorText } from '../workspace'
import WorkspaceDialog from './WorkspaceDialog.vue'

const props = defineProps<{ title: string; description: string; action: () => Promise<void>; label?: string }>()
const emit = defineEmits<{ close: [] }>()
const busy = ref(false)
const error = ref('')
async function confirm() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    await props.action()
    emit('close')
  } catch (reason) { error.value = errorText(reason) }
  finally { busy.value = false }
}
</script>

<template>
  <WorkspaceDialog :title="title" :busy="busy" @close="emit('close')">
    <p>{{ description }}</p>
    <p v-if="error" class="alert error-alert" role="alert">{{ error }}</p>
    <div class="modal-actions">
      <button autofocus class="btn secondary" :disabled="busy" @click="emit('close')">取消</button>
      <button class="btn primary" :disabled="busy" @click="confirm">{{ busy ? '正在处理…' : label || '确认删除' }}</button>
    </div>
  </WorkspaceDialog>
</template>
