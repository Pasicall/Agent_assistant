export type User = { id: string; email: string; name: string }
export type KnowledgeBase = { id: string; name: string; description: string; created_at: string; document_count: number }
export type Document = { id: string; knowledge_base_id: string; name: string; size_bytes: number; sha256?: string; status: string; chunk_count: number; error?: string; created_at: string; updated_at?: string; job_id?: string; active_revision_id?: string; source?: string; expires_at?: string }
export type Job = { id: string; status: string; progress: number; error?: string; document_id: string; stage?: string; processed_pages?: number; total_pages?: number }
export type Chunk = { id: string; text: string; page: number | null; ordinal: number }
export type Citation = { chunk_id: string; document_id: string; document_name: string; knowledge_base_id: string; page: number | null; text: string; score: number; revision_id?: string; source?: string }
export type Trace = { step: string; detail: unknown }
export type Message = { id: string; role: string; content: string; citations?: Citation[]; trace?: Trace[]; created_at?: string; run_metadata?: AnswerMetadata; restricted?: boolean }
export type Conversation = { id: string; title: string; created_at: string }
export type EvaluationCase = { question: string; reference: string; expected_document_ids?: string[]; message_id?: string }
export type Evaluation = { id: string; name: string; status: string; metrics: Record<string, unknown>; results: Record<string, unknown>[]; created_at: string; error?: string }
export type Comparison = { dense: Record<string, unknown>; hybrid: Record<string, unknown>; relative_hit_rate_change: number | null }
export type Capabilities = { max_upload_mb: number; learning_enabled: boolean; learning_general_enabled?: boolean; learning_assessment_configured: boolean; evaluation_enabled: boolean; folder_sync_available: boolean }
export type AnswerMetadata = { provider?: string; verification?: { mode: string; passed: boolean }; abstained?: boolean }
export type ChatResponse = { answer: string; citations: Citation[]; trace: Trace[]; cached: boolean; conversation_id: string; message_id: string; provider: string; usage: Record<string, unknown>; verification?: { mode: string; passed: boolean; semantic_checked?: boolean }; abstained?: boolean }

export const API_BASE = (import.meta.env.VITE_API_BASE || '/api').replace(/\/$/, '')
export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); this.name = 'ApiError' }
}

let session: { controller: AbortController; expired: () => void } | undefined

export function bindApiSession(expired?: () => void) {
  session?.controller.abort()
  session = expired ? { controller: new AbortController(), expired } : undefined
}

function requestContext(signal?: AbortSignal | null) {
  const owner = session
  const authSignal = owner?.controller.signal
  const combined = authSignal && signal ? AbortSignal.any([authSignal, signal]) : authSignal || signal
  return {
    signal: combined,
    check() { combined?.throwIfAborted() },
    fail(error: ApiError): never {
      combined?.throwIfAborted()
      if (error.status === 401 && owner && owner === session) owner.expired()
      throw error
    },
  }
}

async function request(path: string, options: RequestInit, context: ReturnType<typeof requestContext>) {
  const headers = new Headers(options.headers)
  headers.set('X-RAG-Request', '1')
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, { ...options, headers, signal: context.signal, credentials: 'same-origin' })
  } catch {
    context.check()
    throw new ApiError('无法连接服务，请稍后重试。', 0)
  }
  if (response.ok) return response
  let message = `请求失败（${response.status}）`
  try {
    const error = await response.json()
    if (typeof error.detail === 'string') message = error.detail
    else if (Array.isArray(error.detail)) message = error.detail.map((item: { msg: string }) => item.msg).join('；')
  } catch { /* Keep the HTTP status when there is no JSON body. */ }
  context.fail(new ApiError(message, response.status))
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const context = requestContext(options.signal)
  const response = await request(path, options, context)
  const value = response.status === 204 ? undefined : await response.json()
  context.check()
  return value as T
}

export async function downloadDocument(documentId: string, filename: string, revisionId?: string) {
  const context = requestContext()
  const path = revisionId ? `/documents/${encodeURIComponent(documentId)}/versions/${encodeURIComponent(revisionId)}/download` : `/documents/${encodeURIComponent(documentId)}/download`
  const response = await request(path, {}, context)
  const blob = await response.blob()
  context.check()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

export async function streamChat(body: Record<string, unknown>, signal: AbortSignal,
  onDraft: (text: string) => void): Promise<ChatResponse> {
  const context = requestContext(signal)
  const response = await request('/chat/stream', { method: 'POST', body: JSON.stringify(body) }, context)
  if (!response.body) throw new ApiError('回答连接未建立，可以重试原问题。', 502)
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    while (true) {
      const { done, value } = await reader.read()
      context.check()
      if (done) break
      buffer += decoder.decode(value, { stream: true }).replace(/\r/g, '')
      let end: number
      while ((end = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, end)
        buffer = buffer.slice(end + 2)
        const kind = frame.split('\n').find(line => line.startsWith('event:'))?.slice(6).trim()
        const data = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trim()).join('\n')
        if (!data) continue
        const payload = JSON.parse(data)
        if (kind === 'draft') onDraft(payload.text)
        if (kind === 'done') return payload as ChatResponse
        if (kind === 'error') context.fail(new ApiError(payload.detail || '生成失败', payload.status || 503))
      }
      if (buffer.length > 2 * 1024 * 1024) throw new ApiError('响应超出大小限制', 502)
    }
    throw new ApiError('连接提前结束，可以重试原问题。', 502)
  } catch (error) {
    context.check()
    if (error instanceof ApiError) throw error
    throw new ApiError('回答连接中断，可以重试原问题。', 502)
  } finally {
    await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}
