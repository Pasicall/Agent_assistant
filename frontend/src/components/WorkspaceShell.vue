<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { BookOpen, ChevronRight, Layers3, Menu, MessageSquare } from 'lucide-vue-next'
import { api, type Capabilities, type User } from '../api'
import { usePreferences } from '../preferences'
import { useCorpus } from '../api/corpus'
import { errorText, type UploadSession, type View } from '../workspace'
import ConfirmAction from './ConfirmAction.vue'
import WorkspaceDialog from './WorkspaceDialog.vue'
import LibraryWorkspace from './LibraryWorkspace.vue'
import ChatWorkspace from './ChatWorkspace.vue'
import EvaluationWorkspace from './EvaluationWorkspace.vue'
import LearningWorkspace from './learning/LearningWorkspace.vue'

const props = defineProps<{ user: User; uploadSession: UploadSession }>()
const emit = defineEmits<{ signedOut: [] }>()
const corpus = useCorpus()
const capabilities = ref<Capabilities>()
const capabilityError = ref('')
const { preferences, theme, storageUnavailable, reset } = usePreferences(props.user.id)
const view = ref<View>(preferences.startPage === 'chat' ? 'chat' : 'library')
const visited = ref(new Set<View>([view.value]))
let navigated = false
let startupApplied = false
const mobileNav = ref(false)
const account = ref(false)
const security = ref(false)
const accountError = ref('')
const signingOut = ref(false)
const selectedLibrary = ref('')
const chatEntry = ref<{ scope: string }>()
const titles = { library: '资料库', chat: '问答', learning: '专题学习', evaluations: '开发工具' }
const nav = [
  { id: 'library', icon: BookOpen },
  { id: 'chat', icon: MessageSquare },
  { id: 'learning', icon: BookOpen },
] as const

function go(next: View) {
  navigated = true
  view.value = next
  visited.value.add(next)
  mobileNav.value = false
}
function ask(scope: string) {
  chatEntry.value = { scope }
  go('chat')
}
function uploadIn(id: string) {
  if (id) selectedLibrary.value = id
  go('library')
}

async function loadCapabilities() {
  capabilityError.value = ''
  try {
    capabilities.value = await api('/capabilities')
    if (!startupApplied && !navigated && preferences.startPage === 'learning' && capabilities.value?.learning_enabled) {
      view.value = 'learning'
      visited.value.add('learning')
    }
    startupApplied = true
  }
  catch (error) { capabilityError.value = errorText(error) }
}

async function signOut() {
  if (signingOut.value) return
  signingOut.value = true
  accountError.value = ''
  try {
    await api('/auth/logout', { method: 'POST' })
    emit('signedOut')
  } catch (error) { accountError.value = errorText(error) }
  finally { signingOut.value = false }
}

async function revoke() {
  await api('/auth/sessions', { method: 'DELETE' })
  emit('signedOut')
}

onMounted(() => { void loadCapabilities(); void corpus.refresh() })
</script>

<template>
  <div class="app-shell" :data-theme="theme" :data-font-size="preferences.fontSize">
    <aside class="sidebar">
      <button class="brand" @click="go('library')">
        <span class="brand-mark"><Layers3 :size="23" /></span><span>知衡<small>KNOWLEDGE OS</small></span>
      </button>
      <nav aria-label="工作空间">
        <template v-for="item in nav" :key="item.id">
          <button v-if="item.id !== 'learning' || capabilities?.learning_enabled" class="nav-link"
            :class="{ active: view === item.id }" :aria-current="view === item.id ? 'page' : undefined" @click="go(item.id)">
            <component :is="item.icon" :size="19" /><span>{{ titles[item.id] }}</span>
          </button>
        </template>
      </nav>
      <div class="sidebar-bottom">
        <button class="nav-link" @click="account = true"><span class="avatar small" aria-hidden="true">{{ user.name.slice(0, 1) || 'U' }}</span><span>账号与设置</span></button>
      </div>
    </aside>

    <div class="main-shell">
      <header class="topbar">
        <div class="breadcrumb">
          <button class="icon-button mobile-menu" aria-label="打开导航" @click="mobileNav = true"><Menu :size="20" /></button>
          <span>工作空间</span><ChevronRight :size="13" /><strong>{{ titles[view] }}</strong>
        </div>
      </header>
      <div v-if="capabilityError" class="global-error alert error-alert" role="alert">
        <span>功能状态读取失败。{{ capabilityError }}</span><button class="text-button" @click="loadCapabilities">重试</button>
      </div>

      <LibraryWorkspace v-show="view === 'library'" v-model:selected="selectedLibrary" :active="view === 'library'"
        :corpus="corpus" :capabilities="capabilities" :upload-session="uploadSession" @ask="ask" />
      <ChatWorkspace v-if="visited.has('chat')" v-show="view === 'chat'" :active="view === 'chat'" :corpus="corpus"
        :user="user" :entry="chatEntry" :enter-sends="preferences.enterSends" @upload="uploadIn" />
      <LearningWorkspace v-if="capabilities?.learning_enabled && visited.has('learning')" v-show="view === 'learning'"
        :active="view === 'learning'" :knowledge-bases="corpus.state.bases" :documents="corpus.state.documents"
        :user-id="user.id" :model-ready="capabilities.learning_assessment_configured" @upload="uploadIn" />
      <EvaluationWorkspace v-if="capabilities?.evaluation_enabled && visited.has('evaluations')"
        v-show="view === 'evaluations'" :active="view === 'evaluations'" :corpus="corpus" />
    </div>

    <WorkspaceDialog v-if="mobileNav" title="工作空间" drawer @close="mobileNav = false">
      <div class="action-stack">
        <button class="btn secondary" @click="go('library')">资料库</button>
        <button class="btn secondary" @click="go('chat')">问答</button>
        <button v-if="capabilities?.learning_enabled" class="btn secondary" @click="go('learning')">专题学习</button>
        <button class="btn secondary" @click="mobileNav = false; account = true">账号与设置</button>
      </div>
    </WorkspaceDialog>
    <WorkspaceDialog v-if="account" title="账号与设置" drawer :busy="signingOut" @close="account = false">
      <p class="muted">仅保存在当前浏览器</p>
      <p v-if="storageUnavailable" class="workspace-notice" role="status">本次有效，刷新后可能恢复原设置</p>
      <fieldset class="settings-group action-stack">
        <legend>通用与外观</legend>
        <label>主题<select v-model="preferences.theme"><option value="light">浅色</option><option value="dark">深色</option><option value="system">跟随系统</option></select></label>
        <label>正文字号<select v-model="preferences.fontSize"><option value="standard">标准（16px）</option><option value="large">大号（18px）</option></select></label>
        <label>登录后打开<select v-model="preferences.startPage">
          <option value="library">资料库</option><option value="chat">问答</option>
          <option v-if="capabilities?.learning_enabled || preferences.startPage === 'learning'" value="learning" :disabled="!capabilities?.learning_enabled">专题学习</option>
        </select></label>
        <small>下次登录或重新打开工作台时生效。</small>
        <p v-if="preferences.startPage === 'learning' && !capabilities?.learning_enabled" role="status">首选专题学习当前不可用，启动时会进入资料库。首选项已保留。</p>
      </fieldset>
      <fieldset class="settings-group action-stack">
        <legend>问答</legend>
        <label>回车键行为<select v-model="preferences.enterSends">
          <option :value="true">Enter 发送，Shift + Enter 换行</option>
          <option :value="false">Enter 换行，通过发送按钮提交</option>
        </select></label>
      </fieldset>
      <button class="btn secondary" @click="reset">恢复默认设置</button>
      <fieldset class="settings-group action-stack">
        <legend>账号与安全</legend>
        <p><strong>{{ user.name }}</strong><br>{{ user.email }}</p>
        <p v-if="accountError" class="alert error-alert" role="alert">{{ accountError }} 请重试退出。</p>
        <button class="btn secondary" :disabled="signingOut" @click="signOut">{{ signingOut ? '正在退出…' : '退出登录' }}</button>
        <button class="btn secondary" :disabled="signingOut" @click="security = true">退出所有设备</button>
      </fieldset>
      <button v-if="capabilities?.evaluation_enabled" class="btn secondary" @click="account = false; go('evaluations')">开发工具</button>
    </WorkspaceDialog>
    <ConfirmAction v-if="security" title="退出所有设备" description="这会撤销此账号在所有设备上的登录，包含当前设备。"
      label="退出所有设备" :action="revoke" @close="security = false" />
  </div>
</template>
