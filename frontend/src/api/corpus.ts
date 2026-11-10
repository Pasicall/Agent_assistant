import { reactive } from 'vue'
import { api, type Document, type KnowledgeBase } from '../api'
import { errorText } from '../workspace'

export function useCorpus() {
  const state = reactive({
    bases: [] as KnowledgeBase[],
    documents: [] as Document[],
    loaded: false,
    readAt: '',
    loading: false,
    error: '',
  })
  let sequence = 0
  let inFlight: Promise<boolean> | undefined

  function invalidate() {
    sequence += 1
    inFlight = undefined
    state.loading = false
  }

  function refresh() {
    if (!inFlight) inFlight = read()
    return inFlight
  }

  async function read() {
    const id = ++sequence
    state.loading = true
    try {
      const [bases, documents] = await Promise.all([
        api<KnowledgeBase[]>('/knowledge-bases'),
        api<Document[]>('/documents'),
      ])
      if (id === sequence) {
        state.bases = bases
        state.documents = documents
        state.loaded = true
        state.readAt = new Date().toISOString()
        state.error = ''
      }
      return true
    } catch (error) {
      if (id === sequence) state.error = errorText(error)
      return false
    } finally {
      if (id === sequence) {
        state.loading = false
        inFlight = undefined
      }
    }
  }

  function putDocument(doc: Document) {
    invalidate()
    const index = state.documents.findIndex(item => item.id === doc.id)
    if (index < 0) {
      state.documents.unshift(doc)
      const base = state.bases.find(item => item.id === doc.knowledge_base_id)
      if (base) base.document_count += 1
    } else state.documents[index] = doc
  }

  return { state, refresh, invalidate, putDocument }
}

export type Corpus = ReturnType<typeof useCorpus>
