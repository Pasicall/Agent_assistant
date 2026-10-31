import { api } from '../api'

export type Capability = { id: string; title: string; observable_outcome: string; prerequisite_ids: string[]; source_ids: string[]; coverage_status: string; state: string; state_evidence_ids: string[]; ordinal: number }
export type Source = { id: string; document_name: string; page: number | null; revision_id?: string; document_id?: string; state?: string; excerpt: string }
export type LearningJob = { id: string; workspace_id: string; kind: string; target_id: string; status: string; stage: string; error: string | null }
export type Criterion = { criterion_id: string; verdict: string; reason: string; student_evidence: { quote: string; start: number; end: number }[]; source_ids: string[] }
export type Attempt = { id: string; task_id: string; answer_text: string; submitted_at: string; hint_level_at_submit: number; assessment_status: string; invalidated: boolean; assessment: { criteria?: Criterion[]; feedback?: string; obstacle_candidates?: { code: string; alternative_explanation: string }[]; metadata?: { model: string } } }
export type LearningTask = { id: string; workspace_id: string; template_id: string; capability_id: string; status: string; kind: string; prompt: string; rubric: { id: string; description: string }[]; source_ids: string[]; restricted: boolean; attempts: Attempt[]; hint_level: number }
export type WorkspaceSummary = { id: string; goal_text: string; status: string; updated_at: string; current_task_id: string | null }
export type Workspace = WorkspaceSummary & { knowledge_base_id: string | null; selected_document_ids: string[]; state_version: number; capabilities: Capability[]; sources: Source[]; source_warning: string; jobs: LearningJob[]; tasks: { id: string; template_id: string; capability_id: string; status: string }[]; obstacles: { id: string; capability_id: string; obstacle_code: string; status: string; evidence_refs: string[]; counter_evidence_refs: string[] }[] }
export type LearningHistory = { attempts: Attempt[]; decisions: { id: string; reason: string; kind: string; rule_version: string; evidence_refs: string[]; status: string; created_at: string }[]; hints: { id: string; task_id: string; level: number; revealed_at: string }[] }
const base = '/learning'
const post = <T>(path: string, body: unknown = {}) => api<T>(base + path, { method: 'POST', body: JSON.stringify(body) })
export const learning = {
  list: () => api<WorkspaceSummary[]>(base + '/workspaces'),
  get: (id: string) => api<Workspace>(base + `/workspaces/${id}`),
  create: (body: unknown) => post<{ workspace_id: string }>('/workspaces', body),
  save: (w: Workspace) => api<Workspace>(base + `/workspaces/${w.id}/plan`, { method: 'PATCH', body: JSON.stringify({ state_version: w.state_version, capabilities: w.capabilities.map(({ id, title, observable_outcome, prerequisite_ids, source_ids, coverage_status }) => ({ id, title, observable_outcome, prerequisite_ids, source_ids, coverage_status })) }) }),
  confirm: (w: Workspace) => post<Workspace>(`/workspaces/${w.id}/confirm`, { state_version: w.state_version }),
  task: (id: string) => api<LearningTask>(base + `/tasks/${id}`),
  hint: (id: string, level: number, request_key: string) => post<{ level: number; text: string; sources: Source[] }>(`/tasks/${id}/hints`, { level, request_key }),
  submit: (id: string, answer_text: string, request_key: string) => post<Attempt>(`/tasks/${id}/attempts`, { answer_text, request_key }),
  history: (id: string) => api<LearningHistory>(base + `/workspaces/${id}/history`),
  retry: (id: string) => post<LearningJob>(`/jobs/${id}/retry`),
  next: (w: Workspace, skip: boolean) => post<Workspace>(`/workspaces/${w.id}/next`, { state_version: w.state_version, request_key: crypto.randomUUID(), skip }),
  correct: (w: Workspace, attempt_id: string, reason: string, key: string) => post<Workspace>(`/workspaces/${w.id}/corrections`, { state_version: w.state_version, request_key: key, attempt_id, reason }),
  remove: (id: string) => api<void>(base + `/workspaces/${id}`, { method: 'DELETE' }),
}
