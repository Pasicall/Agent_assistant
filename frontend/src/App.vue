<script setup lang="ts">
import { removeLocal } from './storage'
import { onMounted, onUnmounted, ref } from 'vue'
import { ArrowRight, Layers3, LoaderCircle } from 'lucide-vue-next'
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
    <header class="brand auth-brand"><span class="brand-mark"><Layers3 :size="23" aria-hidden="true" /></span><span>知衡</span></header>
    <div class="auth-story">
      <p class="auth-intro">有据可查的学习工作台</p>
      <p>整理课程资料，核对回答中的原文，通过专题练习检验理解。</p>
    </div>
    <main class="auth-panel">
      <div class="auth-card">
        <h1>{{ authMode === 'login' ? '欢迎回来' : '创建账号' }}</h1>
        <div class="auth-tabs" aria-label="账号操作">
          <button :class="{ active: authMode === 'login' }" :aria-pressed="authMode === 'login'" @click="authMode = 'login'; authError = ''">登录账号</button>
          <button :class="{ active: authMode === 'register' }" :aria-pressed="authMode === 'register'" @click="authMode = 'register'; authError = ''">注册账号</button>
        </div>
        <form class="auth-form" @submit.prevent="signIn">
          <label v-if="authMode === 'register'">你的名字
            <input v-model="auth.name" autocomplete="name" placeholder="如何称呼你" required maxlength="80" />
          </label>
          <label>邮箱地址
            <input v-model="auth.email" type="email" autocomplete="email" placeholder="name@company.com" required />
          </label>
          <label>密码
            <input v-model="auth.password" type="password" :autocomplete="authMode === 'login' ? 'current-password' : 'new-password'"
              placeholder="至少 8 位字符" minlength="8" maxlength="128" required />
          </label>
          <div v-if="authError" class="alert error-alert" role="alert">{{ authError }}</div>
          <button type="submit" class="btn primary auth-submit" :disabled="authenticating">
            <LoaderCircle v-if="authenticating" :size="17" class="spin" />
            {{ authenticating ? '正在连接…' : authMode === 'login' ? '进入工作台' : '创建账号并进入' }}
            <ArrowRight v-if="!authenticating" :size="17" />
          </button>
        </form>
      </div>
    </main>
  </div>

  <WorkspaceShell v-else :key="user.id" :user="user" :upload-session="uploadSession" @signed-out="clearSession()" />
</template>
