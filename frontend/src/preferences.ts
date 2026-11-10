import { computed, onUnmounted, reactive, ref, watch } from 'vue'
import { writeLocal } from './storage'

type Preferences = {
  theme: 'light' | 'dark' | 'system'
  fontSize: 'standard' | 'large'
  startPage: 'library' | 'chat' | 'learning'
  enterSends: boolean
}
const defaults: Preferences = { theme: 'light', fontSize: 'standard', startPage: 'library', enterSends: true }

export function usePreferences(userId: string) {
  const key = `zhiheng-preferences:${userId}`
  const storageUnavailable = ref(false)
  let saved: Partial<Preferences> = {}
  let raw: string | null = null
  try { raw = localStorage.getItem(key) }
  catch { storageUnavailable.value = true }
  try {
    const value = JSON.parse(raw || '{}')
    if (value && typeof value === 'object' && !Array.isArray(value)) saved = value
  } catch { /* Invalid saved values use the defaults without disabling storage. */ }
  const preferences = reactive<Preferences>({
    theme: ['light', 'dark', 'system'].includes(saved.theme ?? '') ? saved.theme! : defaults.theme,
    fontSize: saved.fontSize === 'large' ? 'large' : 'standard',
    startPage: ['library', 'chat', 'learning'].includes(saved.startPage ?? '') ? saved.startPage! : defaults.startPage,
    enterSends: typeof saved.enterSends === 'boolean' ? saved.enterSends : defaults.enterSends,
  })
  const media = window.matchMedia('(prefers-color-scheme: dark)')
  const systemDark = ref(media.matches)
  const onTheme = (event: MediaQueryListEvent) => { systemDark.value = event.matches }
  media.addEventListener('change', onTheme)
  onUnmounted(() => media.removeEventListener('change', onTheme))
  const theme = computed(() => preferences.theme === 'system' ? (systemDark.value ? 'dark' : 'light') : preferences.theme)
  watch(preferences, value => {
    storageUnavailable.value = !writeLocal(key, JSON.stringify(value))
  }, { flush: 'sync' })
  const reset = () => { Object.assign(preferences, defaults) }
  return { preferences, theme, storageUnavailable, reset }
}
