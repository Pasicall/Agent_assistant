<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, onUnmounted, ref } from 'vue'
defineProps<{ title: string; busy?: boolean; drawer?: boolean }>()
const emit = defineEmits<{ close: [] }>()
const dialog = ref<HTMLDialogElement>()
let opener: HTMLElement | null
let returnArea: Element | null
onMounted(async () => {
  opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
  returnArea = opener?.closest('main, aside, dialog') || null
  dialog.value?.showModal()
  await nextTick()
  dialog.value?.querySelector<HTMLElement>('[autofocus]')?.focus()
})
onBeforeUnmount(() => dialog.value?.close())
onUnmounted(async () => {
  await nextTick()
  if (opener?.isConnected && opener.getClientRects().length && !opener.matches(':disabled')) {
    opener.focus()
    return
  }
  const area = document.querySelector('dialog[open]') || (returnArea?.isConnected ? returnArea : document.querySelector('main:not([style*="display: none"])'))
  const fallback = area?.querySelector<HTMLElement>('h1, h2, button:not(:disabled), input:not(:disabled)')
  if (fallback) {
    if (fallback.matches('h1, h2')) fallback.tabIndex = -1
    fallback.focus()
  }
})
</script>

<template>
  <dialog ref="dialog" class="modal workspace-dialog" :class="{ 'workspace-drawer': drawer }" :aria-label="title"
    @cancel.prevent="!busy && emit('close')" @click.self="!busy && emit('close')">
    <header class="dialog-heading">
      <h2>{{ title }}</h2>
      <button v-if="drawer" autofocus class="btn secondary" :disabled="busy" @click="emit('close')">关闭</button>
    </header>
    <section class="dialog-content"><slot /></section>
  </dialog>
</template>
