import { onMounted, onUnmounted, watch, type Ref } from 'vue'
import type { Document } from './api'

export type View = 'library' | 'chat' | 'learning' | 'evaluations'
export type Upload = { file: File; state: 'pending' | 'sending' | 'accepted' | 'failed'; detail?: string; unknown?: boolean }
export type UploadSession = { owner: string; target: string; files: Upload[] }

export function parseTime(value: string) {
  return new Date(/Z$|[+-]\d\d:\d\d$/i.test(value) ? value : value + 'Z')
}

export function dateText(value?: string) {
  return value ? parseTime(value).toLocaleString('zh-CN') : '—'
}

export function expired(doc: Document) {
  return !!doc.expires_at && parseTime(doc.expires_at).getTime() <= Date.now()
}

export function available(doc: Document) {
  return !!doc.active_revision_id && !expired(doc)
}

export const uploadExtensions = '.txt,.md,.csv,.json,.html,.htm,.pdf,.docx,.png,.jpg,.jpeg,.webp'
export function fileProblem(file: File, maxMb: number) {
  const extension = file.name.slice(file.name.lastIndexOf('.')).toLowerCase()
  if (!uploadExtensions.split(',').includes(extension)) return '文件格式不受支持'
  if (!file.size) return '文件为空'
  if (file.size > maxMb * 1024 ** 2) return `文件超过 ${maxMb} MB 上限`
  return ''
}

export const errorText = (error: unknown) => error instanceof Error ? error.message : '操作失败，请重试。'

export function record(name: string, fields: Record<string, string | number>) {
  if (import.meta.env.DEV) console.info(name, fields)
}

export function useVisiblePolling(active: Ref<boolean>, pending: () => boolean, refresh: () => Promise<void>) {
  let running = false
  let timer: ReturnType<typeof setInterval>
  async function tick(force = false) {
    if (running || !active.value || document.hidden || (!force && !pending())) return
    running = true
    try { await refresh() } finally { running = false }
  }
  const onVisible = () => { if (!document.hidden) void tick(true) }
  watch(active, onVisible)
  onMounted(() => {
    timer = setInterval(() => { void tick() }, 2500)
    document.addEventListener('visibilitychange', onVisible)
  })
  onUnmounted(() => {
    clearInterval(timer)
    document.removeEventListener('visibilitychange', onVisible)
  })
}

export function processing(doc: Document) {
  return doc.status === 'queued' || doc.status === 'processing'
}

export function documentStatus(doc: Document, restoring = false) {
  if (expired(doc)) return '已过期'
  if (restoring && processing(doc)) return available(doc) ? '正在恢复，旧版仍可用' : '正在恢复'
  if (available(doc) && processing(doc)) return '正在更新，旧版仍可用'
  if (available(doc) && (doc.status === 'failed' || doc.error)) return '更新失败，仍在使用旧版'
  return ({ queued: '已接收，等待处理', processing: '处理中', failed: '处理失败', ready: '可用', cancelled: '已取消' } as Record<string, string>)[doc.status] || doc.status
}
