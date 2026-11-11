// Run after npm --prefix frontend run build.
// Reuses an installed Playwright: PLAYWRIGHT_MODULE=/path/to/playwright node scripts/check-frontend-interactions.cjs
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const { readFile } = require('node:fs/promises')
const { resolve } = require('node:path')
const { createHash } = require('node:crypto')
const assert = require('node:assert/strict')
const root = resolve(__dirname, '../frontend/dist')
const origin = 'https://zhiheng.test'
const date = '2026-11-10T01:00:00Z'
const owner = { id: 'owner', name: '验收用户', email: 'owner@test.local' }
const caps = { max_upload_mb: 1, learning_enabled: true, learning_assessment_configured: false, evaluation_enabled: false, folder_sync_available: false }
const kb = { id: 'kb', name: '事务笔记', document_count: 1, created_at: date }
const doc = (id = 'd1', name = '事务.txt') => ({ id, name, knowledge_base_id: 'kb', status: 'ready', active_revision_id: 'v1', chunk_count: 1, created_at: date, size_bytes: 100 })
const citations = [1, 2].map(i => ({ chunk_id: 'chunk' + i, document_id: 'd1', document_name: '事务.txt', knowledge_base_id: 'kb', page: i, revision_id: 'v1', score: 1, text: ('原文依据' + i + '：隔离级别约束并发事务。').repeat(i === 1 ? 90 : 2) }))
const task = id => ({ id, workspace_id: 'w', template_id: id, capability_id: 'cap1', status: id === 't1' ? 'completed' : 'pending', prompt: id === 't1' ? '第一题说明事务隔离性' : '第二题分析并发异常', rubric: [{ id: 'r', description: '说明原理' }], source_ids: ['s1'], restricted: false, hint_level: 0, attempts: [] })
const attempt = status => ({ id: 'a1', task_id: 't1', answer_text: '我的事务分析', submitted_at: date, hint_level_at_submit: 2, assessment_status: status, invalidated: false, assessment: status === 'completed' ? { feedback: '已完成本次评价', criteria: [{ criterion_id: 'r', verdict: 'partial', reason: '补充并发时序', student_evidence: [], source_ids: ['s1'] }] } : {} })
const workspace = () => ({
  id: 'w', goal_text: '理解数据库事务并分析并发异常', status: 'draft', updated_at: date, current_task_id: null,
  knowledge_base_id: 'kb', selected_document_ids: ['d1'], state_version: 1, source_warning: '', jobs: [], tasks: [task('t1'), task('t2')], obstacles: [],
  capabilities: [1, 2].map(i => ({ id: 'cap' + i, title: '能力' + i, observable_outcome: '解释并发', coverage_status: 'covered', source_ids: ['s' + i], prerequisite_ids: [], state: 'unverified', ordinal: i })),
  sources: [1, 2].map(i => ({ id: 's' + i, document_id: 'd1', document_name: '事务.txt', revision_id: 'v1', page: i, state: 'current', excerpt: '学习原文' + i })),
})
const file = name => ({ name, mimeType: 'text/plain', buffer: Buffer.from(name) })

async function setup(browser, width, options = {}) {
  const page = await browser.newPage({ viewport: { width, height: 850 } })
  page.setDefaultTimeout(6000)
  const s = { user: owner, caps: { ...caps }, bases: [kb, { ...kb, id: 'queue', name: '处理队列' }], docs: [doc(), { ...doc('dq', '处理中.txt'), knowledge_base_id: 'queue', active_revision_id: null, status: 'queued' }],
    requests: [], uploads: [], errors: [], historyFails: false, corpusFails: false, sourceFails: 0, sourceMissing: false, downloadFails: false,
    versions: [{ id: 'v1', name: '事务.txt', status: 'ready', active: true, created_at: date }], chunkFails: false,
    workspace: workspace(), attempts: [], confirmFails: true, revokeFails: false, ...options }
  page.on('pageerror', e => s.errors.push(e.message))
  await page.addInitScript(({ prefs, denyStorage }) => {
    if (prefs) for (const [key, value] of Object.entries(prefs)) localStorage.setItem(key, JSON.stringify(value))
    if (denyStorage) for (const key of ['getItem', 'setItem', 'removeItem']) Storage.prototype[key] = () => { throw new DOMException('Storage unavailable', 'SecurityError') }
    const original = window.fetch.bind(window)
    window.__chatRequests = []
    window.fetch = (url, init) => {
      if (!String(url).endsWith('/api/chat/stream')) return original(url, init)
      window.__chatRequests.push(JSON.parse(init.body))
      const body = new ReadableStream({ start(controller) {
        window.__stream = controller
        init.signal?.addEventListener('abort', () => { try { controller.error(new DOMException('Aborted', 'AbortError')) } catch {} })
      } })
      return Promise.resolve(new Response(body, { headers: { 'content-type': 'text/event-stream' } }))
    }
  }, options)
  await page.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname, method = request.method()
    if (url.origin !== origin) return route.abort()
    if (!path.startsWith('/api/')) {
      const target = path === '/' ? '/index.html' : path
      return route.fulfill({ body: await readFile(root + target), contentType: target.endsWith('.js') ? 'text/javascript' : target.endsWith('.css') ? 'text/css' : 'text/html' })
    }
    s.requests.push({ path, method, body: request.postData() })
    const json = (body, status = 200) => route.fulfill({ status, json: body })
    if (path === '/api/auth/me') return json(s.user)
    if (path === '/api/auth/login') return json({ user: s.user })
    if (path === '/api/auth/logout') return route.fulfill({ status: 204 })
    if (path === '/api/auth/sessions') return s.revokeFails ? json({ detail: '退出暂时失败' }, 503) : route.fulfill({ status: 204 })
    if (path === '/api/capabilities') {
      if (s.capDelay) await s.capDelay
      return json(s.caps)
    }
    if (path === '/api/knowledge-bases') return s.corpusFails ? json({ detail: '读取暂时失败' }, 503) : json(s.bases)
    if (path === '/api/documents') return s.corpusFails ? json({ detail: '读取暂时失败' }, 503) : json(s.docs)
    if (path === '/api/documents/upload') {
      const parts = request.postDataBuffer().toString()
      const name = parts.match(/filename="([^"]+)"/)[1]
      s.uploads.push(name)
      const added = { ...doc('upload' + s.uploads.length, name), sha256: createHash('sha256').update(name).digest('hex') }
      if (s.expireUpload) { s.expireUpload = false; return json({ detail: '登录已过期' }, 401) }
      s.docs.push(added)
      if (name === 'A.txt' && s.uploads.filter(n => n === name).length === 1) return json({ detail: '提交结果不明' }, 503)
      return json({ document: added, duplicate: false }, 201)
    }
    if (path.endsWith('/chunks')) return s.chunkFails ? json({ detail: '预览暂时不可读' }, 503) : json([{ id: 'chunk1', page: 1, ordinal: 0, text: '已读取的旧版文本' }])
    if (path.endsWith('/versions')) return json(s.versions)
    if (path.endsWith('/versions/v1/download')) return s.downloadFails ? json({ detail: '引用版本已不可用' }, 410) : route.fulfill({ body: 'version one', contentType: 'application/octet-stream' })
    if (path === '/api/documents/d1' && method === 'DELETE') { s.docs = s.docs.filter(d => d.id !== 'd1'); return route.fulfill({ status: 204 }) }
    if (path === '/api/conversations') return s.historyFails ? json({ detail: '历史列表暂时失败' }, 503) : json(['old', 'gone', 'c1'].map(id => ({ id, title: { old: '无范围旧会话', gone: '库已删除会话', c1: '已保存问答' }[id], created_at: date })))
    if (path.startsWith('/api/conversations/')) {
      if (s.sourceFails) { s.sourceFails--; return json({ detail: '来源读取暂时失败' }, 503) }
      return json({ messages: [
        { id: 'q1', role: 'user', content: '历史问题' },
        { id: 'm1', role: 'assistant', content: '历史回答', citations: s.sourceMissing ? [] : (s.citations || citations) },
      ] })
    }
    if (path === '/api/learning/workspaces') {
      if (s.caps.learning_general_enabled && method === 'POST') {
        s.createdGoal = request.postDataJSON()
        s.workspace.goal_text = s.createdGoal.goal_text
        return json({ workspace_id: 'w' }, 202)
      }
      return json(!s.caps.learning_general_enabled || s.createdGoal ? [s.workspace] : [])
    }
    if (path === '/api/learning/workspaces/w') return json(s.workspace)
    if (path.endsWith('/w/history')) return json({ attempts: s.attempts, hints: [], decisions: s.attempts.some(a => a.assessment_status === 'completed') ? [{ id: 'decision', trigger_attempt_id: 'a1', reason: '通过变式验证迁移', status: 'active' }] : [] })
    if (path.endsWith('/w/plan')) {
      s.workspace = { ...s.workspace, ...request.postDataJSON(), state_version: s.workspace.state_version + 1 }
      return json(s.workspace)
    }
    if (path.endsWith('/w/confirm')) {
      if (s.confirmFails) return json({ detail: '确认暂时失败' }, 503)
      if (s.workspace.plan_version === 2) {
        const payload = request.postDataJSON()
        assert.deepEqual(payload.capability_ids, ['cap1'])
        assert.equal(payload.state_version, s.workspace.state_version)
        const job = { id: 'practice1', kind: 'practice', status: 'queued', input_version: ++s.workspace.state_version,
          capability_ids: payload.capability_ids, stage: '正在核验练习' }
        s.workspace = { ...s.workspace, status: 'preparing', jobs: [job] }
        return json({ ...s.workspace, job }, 202)
      }
      s.workspace = { ...s.workspace, status: 'active', current_task_id: 't1' }
      return json(s.workspace)
    }
    if (path.endsWith('/jobs/practice1/retry')) {
      s.workspace.status = 'preparing'
      const job = s.workspace.jobs[0]
      Object.assign(job, { status: 'queued', error: null, stage: '正在核验练习' })
      return json(job)
    }
    if (path.endsWith('/tasks/t1') && s.workspace.plan_version === 2) return json({ ...task('t1'), prompt: '根据合外力和质量计算加速度' })
    if (path.endsWith('/tasks/t1') || path.endsWith('/tasks/t2')) return json({ ...task(path.split('/').pop()), hint_level: s.hintLevel || 0 })
    if (path.endsWith('/t1/hints')) { s.hintLevel = request.postDataJSON().level; return json({ level: s.hintLevel, text: '先核对并发时序', sources: [] }) }
    if (path.endsWith('/t1/attempts')) {
      assert.equal(request.postDataJSON().answer_text, '我的事务分析')
      s.attempts = [attempt('queued')]
      s.workspace.jobs = [{ id: 'job1', kind: 'assessment', target_id: 'a1', status: 'queued' }]
      return json(s.attempts[0], 202)
    }
    if (path.endsWith('/attempts/a1')) return json(s.attempts[0])
    if (path.endsWith('/jobs/job1/retry')) {
      s.attempts = [attempt('queued')]
      s.workspace.jobs = [{ id: 'job1', kind: 'assessment', target_id: 'a1', status: 'queued' }]
      return json(s.workspace.jobs[0])
    }
    throw new Error('Unexpected request ' + method + ' ' + path)
  })
  const btn = name => page.getByRole('button', { name, exact: true })
  const visibleText = text => page.getByText(text, { exact: true }).filter({ visible: true }).first().waitFor()
  const nav = async name => {
    if (page.viewportSize().width < 900) {
      await btn('打开导航').click()
      await page.getByRole('dialog').getByRole('button', { name, exact: true }).click()
    } else if (name === '账号与设置') await btn(name).click()
    else await page.getByRole('navigation').getByRole('button', { name, exact: true }).click()
  }
  const fit = async () => assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'horizontal overflow: ' + width)
  const refresh = async () => {
    // Allow the previous visible-page refresh to settle before simulating return to the tab.
    await page.waitForTimeout(150)
    await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')))
  }
  const frame = (kind, data) => page.evaluate(({ kind, data }) => window.__stream.enqueue(new TextEncoder().encode('event: ' + kind + '\ndata: ' + JSON.stringify(data) + '\n\n')), { kind, data })
  const finish = () => frame('done', { answer: '最终回答\n' + '最终内容\n'.repeat(90), conversation_id: 'c1', message_id: 'm1', citations })
  await page.goto(origin)
  await page.locator('.app-shell').waitFor()
  return { page, s, btn, nav, fit, visibleText, refresh, frame, finish }
}

async function core(browser, width) {
  const t = await setup(browser, width)
  const { page, s, btn, nav, fit, visibleText, refresh, frame, finish } = t
  await visibleText('事务.txt')
  const choose = async (click, files) => {
    const [picker] = await Promise.all([page.waitForEvent('filechooser'), click()])
    await picker.setFiles(files)
  }
  await choose(() => btn('上传资料').click(), [file('A.txt'), file('B.txt')])
  await btn('核对并重试').waitFor()
  await btn('上传资料').click()
  await choose(() => btn('选择文件并替换待办项').click(), [])
  assert.equal(await page.locator('.upload-queue li').count(), 2)
  await btn('上传资料').click()
  await choose(() => btn('选择文件并替换待办项').click(), [file('bad.exe')])
  await page.getByText(/所选文件均无法上传/).waitFor()
  assert.equal(await page.locator('.upload-queue li').count(), 2)
  const transfer = await page.evaluateHandle(() => { const transfer = new DataTransfer(); transfer.items.add(new File(['C'], 'C.txt')); return transfer })
  await page.locator('.library-list').dispatchEvent('drop', { dataTransfer: transfer })
  await btn('返回当前批次').click()
  await btn('核对并重试').click()
  await visibleText('已接收 · 已在目标库找到相同内容')
  assert.deepEqual(s.uploads, ['A.txt'])
  await btn('继续待提交文件').click()
  await page.locator('.library-list').getByRole('button', { name: 'B.txt', exact: true }).waitFor()
  assert.deepEqual(s.uploads, ['A.txt', 'B.txt'])
  await page.getByLabel('搜索资料名称').fill('nonexistent')
  await btn('清除搜索').click()
  assert.equal(await page.getByLabel('搜索资料名称').inputValue(), '')
  const fileRow = btn('事务.txt')
  await fileRow.click()
  await visibleText('已读取的旧版文本')
  const details = page.getByRole('dialog', { name: '事务.txt', exact: true })
  assert.equal(await details.evaluate(el => el.matches(':modal')), width < 1200)
  assert.equal(await fileRow.getAttribute('aria-expanded'), 'true')
  s.docs[0] = { ...s.docs[0], name: '新版本.txt', active_revision_id: 'v2' }
  s.versions = [{ id: 'v2', name: '新版本.txt', status: 'ready', active: true, created_at: date }]
  s.chunkFails = true
  await refresh()
  await page.getByText(/下方保留最近成功读取的文本/).waitFor()
  await visibleText('解析文本 · 事务.txt')
  await visibleText('已读取的旧版文本')
  await page.keyboard.press('Escape')
  assert(await btn('新版本.txt').evaluate(el => el === document.activeElement))
  s.docs[0] = doc(); s.chunkFails = false; s.versions = [{ id: 'v1', name: '事务.txt', status: 'ready', active: true, created_at: date }]
  await refresh()
  await btn('基于此库提问').click()
  assert.equal(await page.getByLabel('本次范围').inputValue(), 'kb')
  const q = page.getByLabel('输入问题')
  await page.locator('.chat-history > summary').click()
  await btn('无范围旧会话').click()
  await q.fill('继续旧问题')
  await visibleText('请先选择本次提问范围')
  assert(await btn('发送问题').isDisabled())
  await page.getByLabel('本次范围').selectOption('')
  assert.equal(await q.inputValue(), '继续旧问题')
  await page.evaluate(() => localStorage.setItem('chat-scope:owner:gone', 'deleted'))
  await page.locator('.chat-history > summary').click()
  await btn('库已删除会话').click()
  await visibleText('原知识库已不可用')
  await page.getByLabel('本次范围').selectOption('queue')
  await page.getByText('此范围暂无可用资料', { exact: false }).waitFor()
  await btn('查看资料处理状态').click()
  assert.equal(await page.getByLabel('当前知识库').inputValue(), 'queue')
  await page.getByText('资料正在处理，显示可用后即可基于此库提问。').waitFor()
  await nav('问答')
  await page.getByLabel('本次范围').selectOption('kb')
  s.corpusFails = true; await refresh()
  await page.getByText(/资料范围读取失败。\s*保留上次读取结果/).waitFor()
  assert.equal(await page.getByLabel('本次范围').inputValue(), 'kb')
  s.corpusFails = false; await btn('重试读取').click()
  await q.fill('事务是什么')
  await q.dispatchEvent('keydown', { key: 'Enter', isComposing: true })
  assert.equal(await page.evaluate(() => window.__chatRequests.length), 0)
  await btn('发送问题').click()
  await frame('draft', { text: '尚未完成' })
  await visibleText('尚未完成')
  await btn('停止生成').click()
  await visibleText('已停止接收，问题已保留')
  assert.equal(await page.getByText('尚未完成', { exact: true }).count(), 0)
  await btn('重试原问题').click()
  await page.evaluate(() => window.__stream.close())
  await visibleText('回答连接中断，可重试原问题')
  assert.equal(await q.inputValue(), '事务是什么')
  await btn('重试原问题').evaluate(el => { el.click(); el.click() })
  await frame('draft', { text: '长回答\n'.repeat(180) })
  const messages = page.locator('.messages')
  await page.waitForFunction(() => { const el = document.querySelector('.messages'); return el.scrollHeight > el.clientHeight * 2 })
  await messages.evaluate(el => { el.scrollTop = 100; el.dispatchEvent(new Event('scroll')) })
  await btn('回到最新回答').waitFor()
  const before = await messages.evaluate(el => el.scrollTop)
  await frame('draft', { text: '追加片段\n'.repeat(10) })
  s.historyFails = true
  await finish()
  await page.getByText(/最终回答/).first().waitFor()
  assert(Math.abs(await messages.evaluate(el => el.scrollTop) - before) < 3, 'stream/final must preserve scroll position')
  const requests = await page.evaluate(() => window.__chatRequests)
  assert.equal(requests.length, 3)
  assert.deepEqual(requests[0], requests[1]); assert.deepEqual(requests[1], requests[2])
  assert.equal(await page.locator('.message.user').count(), 1)
  await page.locator('.chat-history > summary').click()
  await btn('重试读取历史').waitFor()
  s.historyFails = false; await btn('重试读取历史').click()
  assert.equal(await page.evaluate(() => window.__chatRequests.length), 3)
  await btn('回到最新回答').click()
  const cite = page.locator('.message-citations button').nth(1)
  s.sourceFails = 1
  await cite.click()
  await btn('重试读取来源').click()
  await page.locator('#source-chunk2.focused').waitFor()
  const header = page.locator('.dialog-heading')
  const bounds = await header.boundingBox()
  assert(bounds.y >= 0 && bounds.y < 30, 'drawer close header remains visible')
  const selectedBounds = await page.locator('#source-chunk2').boundingBox()
  assert(selectedBounds.y >= bounds.y + bounds.height - 2 && selectedBounds.y < 850, 'selected citation must be visible')
  const sourcePanel = page.getByRole('dialog', { name: '回答来源' })
  assert.equal(await sourcePanel.evaluate(el => el.matches(':modal')), width < 1200)
  if (width >= 1200) {
    assert((await messages.boundingBox()).width >= 560)
    assert(await cite.isVisible())
  } else {
    assert.equal((await sourcePanel.boundingBox()).width, width)
  }
  assert.equal(await page.locator('#source-chunk1').count(), 0)
  await sourcePanel.locator('.citation-option').first().click()
  await sourcePanel.locator('#source-chunk1.focused').waitFor()
  await sourcePanel.locator('.citation-option').nth(1).click()
  await sourcePanel.locator('#source-chunk2.focused').waitFor()
  const [download] = await Promise.all([page.waitForEvent('download'), page.locator('#source-chunk2').getByRole('button').click()])
  assert.equal(download.suggestedFilename(), '事务.txt')
  assert(s.requests.some(r => r.path === '/api/documents/d1/versions/v1/download'))
  s.downloadFails = true
  await page.locator('#source-chunk2').getByRole('button').click()
  await visibleText('此版本的原文已不可用。')
  assert(!s.requests.some(r => r.path === '/api/documents/d1/download'))
  await page.keyboard.press('Escape')
  assert(await cite.evaluate(el => el === document.activeElement))
  s.citations = citations.map(c => ({ ...c, revision_id: undefined }))
  const downloadsBefore = s.requests.filter(r => r.path.includes('/download')).length
  await cite.click()
  await page.locator('#source-chunk2').getByRole('button').click()
  await visibleText('此引用未记录原文版本，无法下载对应版本。')
  assert.equal(s.requests.filter(r => r.path.includes('/download')).length, downloadsBefore)
  await page.keyboard.press('Escape')
  await q.fill('修改前')
  await btn('发送问题').click(); await btn('停止生成').click(); await btn('重试原问题').waitFor()
  await q.fill('修改后')
  assert.equal(await btn('重试原问题').count(), 0)
  await btn('发送问题').click(); await btn('停止生成').click()
  const amended = await page.evaluate(() => window.__chatRequests.slice(-2))
  assert.notEqual(amended[0].request_id, amended[1].request_id)
  await q.fill('保留的问答草稿')
  await page.setViewportSize({ width, height: 390 })
  await q.fill('软键盘缩小视口时的草稿')
  await btn('发送问题').scrollIntoViewIfNeeded()
  const sendBounds = await btn('发送问题').boundingBox()
  assert(sendBounds.y >= 0 && sendBounds.y + sendBounds.height <= 390)
  await fit()
  await q.fill('保留的问答草稿')
  await page.setViewportSize({ width, height: 850 })
  await learning(t)
  await settings(t)
  assert.deepEqual(s.errors, [])
  console.log(JSON.stringify({ width, cases: 'upload/scope/retry/preview/scroll/source/learning/settings', uploads: s.uploads.length, same_question_chat_requests: requests.length, learning_confirm_requests: s.requests.filter(r => r.path.endsWith('/confirm')).length, assessment_retries: s.requests.filter(r => r.path.endsWith('/job1/retry')).length, next_requests: s.requests.filter(r => r.path.endsWith('/next')).length }))
  await page.close()
}

async function learning(t) {
  const { page, s, btn, nav, visibleText, refresh, fit } = t
  await nav('专题学习'); await btn('继续学习').click()
  await page.getByText(/已核对 0 \/ 2 项/).waitFor()
  await btn('查看待核对项').click()
  assert.equal(await page.evaluate(() => document.activeElement.id), 'cap-cap1')
  const cards = page.locator('.learning-plan > article')
  await cards.nth(0).getByText('查看原文依据', { exact: true }).click()
  await visibleText('学习原文1')
  assert.equal(await cards.nth(0).locator('.bound-sources').getByText('学习原文2', { exact: true }).count(), 0)
  for (const card of await cards.all()) await card.getByRole('checkbox', { name: '已核对此项能力及其原文依据' }).check()
  assert(await btn('确认目标并开始').isEnabled())
  await cards.nth(0).getByText('编辑目标与依据', { exact: true }).click()
  await cards.nth(0).getByRole('group', { name: '原文依据' }).getByRole('checkbox').nth(1).check()
  await page.getByText(/已核对 1 \/ 2 项/).waitFor()
  await cards.nth(0).getByLabel('能力名称').fill('保存后的能力名称')
  await cards.nth(0).getByRole('checkbox', { name: '已核对此项能力及其原文依据' }).check()
  await btn('确认目标并开始').click()
  await page.getByText(/确认暂时失败/).waitFor()
  await visibleText('草稿已保存。')
  assert.equal(s.workspace.capabilities[0].title, '保存后的能力名称')
  assert.equal(await cards.nth(0).getByLabel('能力名称').inputValue(), '保存后的能力名称')
  s.confirmFails = false
  await btn('确认目标并开始').click()
  await visibleText('第一题说明事务隔离性')
  await page.getByText('需要提示', { exact: true }).click(); await btn('2 · 查看相关原文').click()
  await visibleText('先核对并发时序')
  await page.getByLabel('你的作答').fill('我的事务分析'); await btn('提交作答').click()
  await visibleText('等待评价')
  await page.getByText('本次题目与作答', { exact: true }).click()
  await visibleText('我的事务分析')
  for (const [status, title] of [['running', '正在评价'], ['failed', '评价失败']]) {
    s.attempts = [attempt(status)]; s.workspace.jobs[0].status = status
    await refresh(); await visibleText(title); await visibleText('我的事务分析')
  }
  await page.getByText('评价模型未配置，请由部署者配置后重试原评价。本次作答已保留。').waitFor()
  await btn('重试原评价').click(); await visibleText('等待评价')
  s.attempts = [attempt('completed')]; s.workspace.jobs[0].status = 'completed'; s.workspace.current_task_id = 't2'
  await refresh()
  await visibleText('已完成本次评价'); await visibleText('关键改进点')
  await btn('继续下一题').click()
  await visibleText('第二题分析并发异常')
  await page.getByLabel('你的作答').fill('第二题草稿')
  await btn('学习记录').click(); await btn('回看作答').click()
  await page.getByRole('dialog').getByText('本次题目与作答', { exact: true }).click()
  await visibleText('我的事务分析')
  await page.getByRole('dialog').getByRole('button', { name: '返回当前任务', exact: true }).click()
  assert.equal(await page.getByLabel('你的作答').inputValue(), '第二题草稿')
  assert.equal(s.requests.filter(r => r.path.endsWith('/next')).length, 0)
  assert.equal(s.requests.filter(r => r.path.endsWith('/job1/retry')).length, 1)
  await fit()
}

async function settings(t) {
  const { page, s, btn, nav, visibleText, fit } = t
  await nav('问答')
  const q = page.getByLabel('输入问题')
  assert.equal(await q.inputValue(), '保留的问答草稿')
  await nav('账号与设置')
  await page.getByLabel('主题').selectOption('dark')
  await page.getByLabel('正文字号').selectOption('large')
  await page.getByLabel('登录后打开').selectOption('chat')
  await page.getByLabel('回车键行为').selectOption('false')
  if (process.env.UI_SCREENSHOT_DIR) await page.screenshot({ animations: 'disabled', path: process.env.UI_SCREENSHOT_DIR + '/settings-' + page.viewportSize().width + '.png' })
  await page.keyboard.press('Escape')
  assert.equal(await page.locator('.app-shell').getAttribute('data-theme'), 'dark')
  assert.equal(await q.evaluate(el => getComputedStyle(el).fontSize), '18px')
  const count = await page.evaluate(() => window.__chatRequests.length)
  await q.press('Enter')
  assert.equal(await page.evaluate(() => window.__chatRequests.length), count)
  await page.getByText('Enter 换行 · 点击发送按钮提交', { exact: true }).waitFor()
  await nav('账号与设置'); await btn('恢复默认设置').click(); await page.keyboard.press('Escape')
  assert.equal(await page.locator('.app-shell').getAttribute('data-theme'), 'light')
  assert.equal((await q.inputValue()).trim(), '保留的问答草稿')
  await nav('专题学习')
  assert.equal(await page.getByLabel('你的作答').inputValue(), '第二题草稿')
  await nav('账号与设置'); await page.getByLabel('主题').selectOption('system')
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.waitForFunction(() => document.querySelector('.app-shell').dataset.theme === 'dark')
  await page.emulateMedia({ colorScheme: 'light' })
  await page.waitForFunction(() => document.querySelector('.app-shell').dataset.theme === 'light')
  await page.getByLabel('主题').selectOption('dark')
  await page.getByLabel('登录后打开').selectOption('chat')
  await fit()
  await page.reload(); await page.getByLabel('输入问题').waitFor()
  assert.equal(await page.locator('.app-shell').getAttribute('data-theme'), 'dark')
  await nav('账号与设置'); await btn('退出所有设备').click()
  await page.getByRole('dialog', { name: '退出所有设备', exact: true }).getByRole('button', { name: '取消', exact: true }).click()
  await page.getByRole('dialog', { name: '账号与设置', exact: true }).waitFor()
  s.revokeFails = true; await btn('退出所有设备').click()
  await page.getByRole('dialog', { name: '退出所有设备', exact: true }).getByRole('button', { name: '退出所有设备', exact: true }).click()
  await visibleText('退出暂时失败')
  s.revokeFails = false
  await page.getByRole('dialog', { name: '退出所有设备', exact: true }).getByRole('button', { name: '退出所有设备', exact: true }).click()
  await visibleText('欢迎回来')
  const login = async () => {
    await page.getByLabel('邮箱地址').fill(s.user.email); await page.getByLabel('密码', { exact: true }).fill('test-password'); await btn('进入工作台').click()
  }
  await login(); await page.getByLabel('输入问题').waitFor()
  assert.equal(await page.locator('.app-shell').getAttribute('data-theme'), 'dark')
  await nav('账号与设置'); await btn('退出登录').click()
  s.user = { ...owner, id: 'other', email: 'other@test.local' }
  await login(); await page.getByRole('heading', { name: kb.name, exact: true }).waitFor()
  assert.equal(await page.locator('.app-shell').getAttribute('data-theme'), 'light')
}

async function accountResume(browser, width) {
  const { page, s, btn, visibleText } = await setup(browser, width, { expireUpload: true })
  const select = async names => {
    const [picker] = await Promise.all([page.waitForEvent('filechooser'), btn('上传资料').click()])
    await picker.setFiles(names.map(file))
  }
  const login = async () => {
    await page.getByLabel('邮箱地址').fill(s.user.email)
    await page.getByLabel('密码', { exact: true }).fill('test-password')
    await btn('进入工作台').click()
  }
  await select(['first.txt', 'second.txt'])
  await visibleText('登录已过期，请重新登录。')
  await login()
  await btn('重试此文件').click()
  await btn('继续待提交文件').click()
  await page.locator('.library-list').getByRole('button', { name: 'second.txt', exact: true }).waitFor()
  assert.deepEqual(s.uploads, ['first.txt', 'first.txt', 'second.txt'])
  s.expireUpload = true
  await select(['third.txt', 'fourth.txt'])
  await visibleText('登录已过期，请重新登录。')
  s.user = { ...owner, id: 'other' }
  await login()
  await page.getByRole('heading', { name: kb.name, exact: true }).waitFor()
  assert.equal(await page.locator('.upload-queue').count(), 0)
  assert.deepEqual(s.errors, [])
  await page.close()
  console.log(JSON.stringify({ width, cases: 'same-account-queue-resume/cross-account-queue-isolation', result: 'passed' }))
}

async function preferenceEdges(browser, width) {
  const blocked = await setup(browser, width, { denyStorage: true })
  await blocked.nav('账号与设置')
  await blocked.visibleText('本次有效，刷新后可能恢复原设置')
  await blocked.page.getByLabel('主题').selectOption('dark')
  await blocked.page.keyboard.press('Escape')
  assert.equal(await blocked.page.locator('.app-shell').getAttribute('data-theme'), 'dark')
  await blocked.nav('问答'); await blocked.page.getByLabel('输入问题').fill('存储不可用仍能输入')
  assert.deepEqual(blocked.s.errors, [])
  await blocked.page.close()
  const fallback = await setup(browser, width, { caps: { ...caps, learning_enabled: false }, prefs: { 'zhiheng-preferences:owner': { startPage: 'learning', theme: 'bad', fontSize: 'bad', enterSends: 'bad' } } })
  await fallback.page.getByRole('heading', { name: kb.name, exact: true }).waitFor()
  await fallback.nav('账号与设置')
  await fallback.visibleText('首选专题学习当前不可用，启动时会进入资料库。首选项已保留。')
  assert.equal(await fallback.page.getByLabel('主题').inputValue(), 'light')
  assert.equal(await fallback.page.getByLabel('正文字号').inputValue(), 'standard')
  assert.equal(await fallback.page.getByLabel('回车键行为').inputValue(), 'true')
  await fallback.page.close()
  let release
  const delayed = await setup(browser, width, { capDelay: new Promise(resolve => { release = resolve }), prefs: { 'zhiheng-preferences:owner': { startPage: 'learning' } } })
  await delayed.nav('问答')
  const capabilityResponse = delayed.page.waitForResponse(r => r.url().endsWith('/capabilities'))
  release(); await capabilityResponse
  await delayed.page.getByLabel('输入问题').waitFor()
  assert(await delayed.page.getByLabel('输入问题').isVisible())
  await delayed.page.close()
  console.log(JSON.stringify({ width, cases: 'denied-storage/invalid-preferences/disabled-learning/startup-navigation-race', result: 'passed' }))
}

async function layoutEdges(browser) {
  const docs = Array.from({ length: 32 }, (_, i) => doc('row-' + i, '物理资料 ' + i + '.txt'))
  const { page, btn, fit } = await setup(browser, 1280, { docs })
  const row = btn('物理资料 18.txt')
  await row.evaluate(el => el.scrollIntoView({ block: 'center' }))
  const anchor = await page.locator('.library-row').evaluateAll(rows => rows.findIndex(el => el.getBoundingClientRect().bottom > 0))
  const anchorRow = page.locator('.library-row').nth(anchor)
  const top = (await anchorRow.boundingBox()).y
  await row.click()
  const panel = page.getByRole('dialog', { name: '物理资料 18.txt', exact: true })
  await panel.waitFor()
  assert(Math.abs((await anchorRow.boundingBox()).y - top) <= 2, 'opening details should preserve the visible list row')
  await page.keyboard.press('Escape')
  assert(Math.abs((await anchorRow.boundingBox()).y - top) <= 2, 'closing details should preserve the visible list row')
  assert(await row.evaluate(el => document.activeElement === el))

  await row.click()
  for (const width of [1200, 1199, 900, 899, 768, 767, 1200]) {
    await page.setViewportSize({ width, height: 850 })
    await page.waitForFunction(expected => document.querySelector('.library-page dialog[open]')?.matches(':modal') === expected, width < 1200)
    assert.equal(await btn('打开导航').isVisible(), width < 900)
    const panelWidth = (await panel.boundingBox()).width
    if (width >= 1200) assert.equal(panelWidth, 360)
    else if (width < 768) assert.equal(panelWidth, width)
    else assert(panelWidth < width)
    await fit()
  }
  assert(await panel.getByRole('button', { name: '关闭' }).isVisible())
  await page.keyboard.press('Escape')
  await page.close()
  console.log(JSON.stringify({ cases: 'details-scroll-anchor/1200-900-768-layout-breakpoints', result: 'passed' }))
}

async function generalLearning(browser, width) {
  const draft = { ...workspace(), plan_version: 2, tasks: [] }
  draft.capabilities[0].coverage_details = { gap: '', evidence: [{ source_id: 's1', quote: '质量和加速度的关系' }] }
  const { page, s, btn, nav, visibleText, fit } = await setup(browser, width, {
    caps: { ...caps, learning_general_enabled: true, learning_assessment_configured: true },
    workspace: draft,
    bases: [kb],
  })
  await nav('专题学习')
  await btn('新建专题').click()
  const goal = page.getByLabel('学习目标')
  assert.equal(await goal.inputValue(), '')
  assert.equal(await page.getByText(/当前支持数据库事务专题/).count(), 0)
  const text = '根据牛顿第二定律计算物体加速度并解释单位'
  await goal.fill(text)
  await page.getByRole('group', { name: '选择资料' }).getByRole('checkbox', { name: /事务.txt/ }).check()
  await btn('生成能力草稿').click()
  const card = page.locator('#cap-cap1')
  await card.getByText('查看原文依据', { exact: true }).click()
  await card.getByText(/核验原句.*质量和加速度的关系/).waitFor()
  assert.equal(s.createdGoal.goal_text, text)
  await card.getByText('编辑目标与依据', { exact: true }).click()
  await card.getByLabel('能力名称').fill('计算加速度')
  await card.getByText('待复核', { exact: true }).filter({ visible: true }).first().waitFor()
  const review = card.getByRole('checkbox', { name: '已核对此项能力及其原文依据' })
  await review.check()
  await btn('确认目标并开始').click()
  await page.getByText(/确认暂时失败/).waitFor()
  s.confirmFails = false
  await btn('确认目标并开始').click()
  await page.getByRole('heading', { name: '正在准备练习' }).waitFor()
  const confirms = s.requests.filter(item => item.path.endsWith('/w/confirm')).map(item => JSON.parse(item.body))
  assert.deepEqual(confirms[0], confirms[1])
  assert.deepEqual(confirms[1].capability_ids, ['cap1'])
  assert.equal(s.requests.filter(item => item.path.endsWith('/w/plan')).length, 1)
  s.workspace.status = 'draft'
  Object.assign(s.workspace.jobs[0], { status: 'failed', error: '练习核验失败', failed_capability_id: 'cap1' })
  await btn('查看失败能力').click()
  assert.equal(await page.evaluate(() => document.activeElement.id), 'cap-cap1')
  assert(await review.isChecked())
  await btn('重试准备练习').click()
  await page.getByRole('heading', { name: '正在准备练习' }).waitFor()
  Object.assign(s.workspace, { status: 'active', current_task_id: 't1', tasks: [task('t1')] })
  s.workspace.jobs[0].status = 'completed'
  await visibleText('根据合外力和质量计算加速度')
  await page.getByLabel('你的作答').fill('加速度等于合外力除以质量')
  assert(await btn('提交作答').isEnabled())
  await fit()
  assert.deepEqual(s.errors, [])
  await page.close()
  console.log(JSON.stringify({ width, cases: 'general-create/review/confirm-replay/practice-retry', result: 'passed' }))
}

;(async () => {
  const browser = await chromium.launch(process.env.CHROME_EXECUTABLE ? { executablePath: process.env.CHROME_EXECUTABLE } : { channel: 'chrome' })
  try {
    for (const width of [1280, 390]) { await core(browser, width); await preferenceEdges(browser, width); await accountResume(browser, width); await generalLearning(browser, width) }
    await layoutEdges(browser)
  } finally { await browser.close() }
})().catch(error => { console.error(error); process.exitCode = 1 })
