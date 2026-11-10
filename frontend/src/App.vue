<script setup lang="ts">
import { removeLocal } from './storage'
import { onMounted, onUnmounted, ref } from 'vue'
import { ArrowRight, FileSearch, FileText, Layers3, LoaderCircle, ShieldCheck } from 'lucide-vue-next'
import { api, ApiError, bindApiSession, type User } from './api'
import { errorText, type UploadSession } from './workspace'
import WorkspaceShell from './components/WorkspaceShell.vue'

const user = ref<User | null>(null)
const restoring = ref(true)
const authMode = ref<'login' | 'register'>('login')
const auth = ref({ email: '', password: '', name: '' })
const authenticating = ref(false)
const authError = ref('')
const uploadSession = ref<UploadSession>({ owner: '', target: '', files: [] })

function clearSession(expired = false) {
  bindApiSession()
  if (!expired) uploadSession.value = { owner: '', target: '', files: [] }
  user.value = null
  auth.value.password = ''
  authError.value = expired ? '登录已过期，请重新登录。' : ''
}

function signedIn(account: User) {
  if (uploadSession.value.owner !== account.id) uploadSession.value = { owner: account.id, target: '', files: [] }
  bindApiSession(() => clearSession(true))
  user.value = account
}

async function signIn() {
  if (authenticating.value) return
  authError.value = ''
  authenticating.value = true
  try {
    const payload = authMode.value === 'register' ? auth.value : { email: auth.value.email, password: auth.value.password }
    const { user: account } = await api<{ user: User }>(`/auth/${authMode.value}`, { method: 'POST', body: JSON.stringify(payload) })
    signedIn(account)
  } catch (error) { authError.value = errorText(error) }
  finally { authenticating.value = false }
}

onMounted(async () => {
  removeLocal('zhiheng_token')
  try { signedIn(await api<User>('/auth/me')) }
  catch (error) {
    if (!(error instanceof ApiError && error.status === 401)) authError.value = errorText(error)
  } finally { restoring.value = false }
})
onUnmounted(() => bindApiSession())
</script>

<template>
  <div v-if="restoring" class="restore-screen"><div class="brand-mark"><Layers3 :size="27" /></div><LoaderCircle class="spin" :size="21" /><p>正在连接知识工作台…</p></div>
  <div v-else-if="!user" class="auth-shell">
    <div class="auth-story">
      <a class="brand auth-brand" href="#"><span class="brand-mark"><Layers3 :size="25" /></span><span>知衡<small>KNOWLEDGE OS</small></span></a>
      <div class="story-copy"><span class="eyebrow"><span class="mini-line"></span> KNOWLEDGE, WITH EVIDENCE.</span><h1>让每一份知识，<br>成为有据可循的答案<span>。</span></h1><p>连接分散的文档，检索真正相关的证据，<br>在一个工作台里完成资料管理、问答与专题学习。</p></div>
      <div class="story-diagram"><div class="diagram-source"><FileText :size="22" /><span>学习资料</span><small>PDF · Word · 图片 · 文本</small></div><span class="diagram-line"></span><div class="diagram-engine"><Layers3 :size="29" /><span>知衡</span></div><span class="diagram-line"></span><div class="diagram-source"><ShieldCheck :size="22" /><span>可追溯的答案</span><small>原文引用 · 版本可查</small></div></div>
      <div class="story-footer"><span><ShieldCheck :size="15" /> 用户数据隔离</span><span><FileSearch :size="15" /> 原文证据溯源</span></div>
    </div>
    <div class="auth-panel"><div class="auth-card"><span class="eyebrow">YOUR KNOWLEDGE WORKSPACE</span><h2>{{ authMode === 'login' ? '欢迎回来' : '创建你的知识空间' }}</h2><p>{{ authMode === 'login' ? '登录知衡，继续探索你的知识。' : '从第一份文档开始，积累可信赖的知识。' }}</p>
      <div class="auth-tabs"><button :class="{ active: authMode === 'login' }" @click="authMode = 'login'; authError = ''">登录账号</button><button :class="{ active: authMode === 'register' }" @click="authMode = 'register'; authError = ''">注册账号</button></div>
      <form class="auth-form" @submit.prevent="signIn"><label v-if="authMode === 'register'">你的名字<input v-model="auth.name" autocomplete="name" placeholder="如何称呼你" required maxlength="80" /></label><label>邮箱地址<input v-model="auth.email" type="email" autocomplete="email" placeholder="name@company.com" required /></label><label>密码<input v-model="auth.password" type="password" :autocomplete="authMode === 'login' ? 'current-password' : 'new-password'" placeholder="至少 8 位字符" minlength="8" maxlength="128" required /></label><div v-if="authError" class="alert error-alert" role="alert">{{ authError }}</div><button type="submit" class="btn primary auth-submit" :disabled="authenticating"><LoaderCircle v-if="authenticating" :size="17" class="spin" />{{ authenticating ? '正在连接…' : authMode === 'login' ? '进入工作台' : '创建账号并进入' }}<ArrowRight v-if="!authenticating" :size="17" /></button></form>
    </div><p class="auth-copyright">知衡 · 让知识流动，让答案可信。</p></div>
  </div>

  <WorkspaceShell v-else :key="user.id" :user="user" :upload-session="uploadSession" @signed-out="clearSession()" />
</template>
