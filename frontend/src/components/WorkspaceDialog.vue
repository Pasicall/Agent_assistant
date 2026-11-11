<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, onUnmounted, ref } from 'vue'
const props = defineProps<{ title: string; busy?: boolean; drawer?: boolean; dockable?: boolean; returnFocus?: HTMLElement }>()
const emit = defineEmits<{ close: [] }>()
const dialog = ref<HTMLDialogElement>()
const desktop = window.matchMedia('(min-width: 1200px)')
const docked = ref(!!props.dockable && desktop.matches)
let opener: HTMLElement | null
let returnArea: Element | null

function openPanel() {
  const element = dialog.value
  if (!element) return
  const focus = document.activeElement
  const wasOpen = element.open
  element.close()
  docked.value = !!props.dockable && desktop.matches
  if (docked.value) element.show()
  else element.showModal()
  const target = wasOpen && focus instanceof HTMLElement && element.contains(focus)
    ? focus : element.querySelector<HTMLElement>('[autofocus]')
  target?.focus({ preventScroll: true })
}

function closeOnEscape(event: KeyboardEvent) {
  if (event.key !== 'Escape' || event.defaultPrevented || event.isComposing || !docked.value || props.busy || document.querySelector(':modal')) return
  event.preventDefault()
  emit('close')
}

onMounted(() => {
  opener = props.returnFocus ?? (document.activeElement instanceof HTMLElement ? document.activeElement : null)
  returnArea = opener?.closest('main, aside, dialog') || null
  openPanel()
  if (props.dockable) {
    desktop.addEventListener('change', openPanel)
    document.addEventListener('keydown', closeOnEscape)
  }
})
onBeforeUnmount(() => {
  desktop.removeEventListener('change', openPanel)
  document.removeEventListener('keydown', closeOnEscape)
  dialog.value?.close()
})
onUnmounted(async () => {
  await nextTick()
  const trigger = props.returnFocus ?? opener
  if (trigger?.isConnected && trigger.getClientRects().length && !trigger.matches(':disabled')) {
    trigger.focus({ preventScroll: true })
    return
  }
  const area = document.querySelector('dialog[open]') ||
    (returnArea?.getClientRects().length ? returnArea : document.querySelector('main:not([style*="display: none"])'))
  const fallback = area?.querySelector<HTMLElement>('h1, h2, button:not(:disabled), input:not(:disabled)')
  if (fallback) {
    if (fallback.matches('h1, h2')) fallback.tabIndex = -1
    fallback.focus({ preventScroll: true })
  }
})
</script>

<template>
  <dialog ref="dialog" class="workspace-dialog" :class="{ 'workspace-drawer': drawer, 'workspace-docked': docked }" :aria-label="title"
    @cancel.prevent="!busy && emit('close')" @click.self="!busy && !docked && emit('close')">
    <header class="dialog-heading">
      <h2>{{ title }}</h2>
      <button v-if="drawer" autofocus class="btn secondary" :disabled="busy" @click="emit('close')">关闭</button>
    </header>
    <section class="dialog-content"><slot /></section>
  </dialog>
</template>
