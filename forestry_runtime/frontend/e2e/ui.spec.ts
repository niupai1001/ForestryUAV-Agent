import { expect, Page, Route, test } from '@playwright/test'

type MockOptions = {
  run?: Record<string, unknown> | null
  runPost?: (route: Route) => Promise<void>
  eventPage?: (after: number) => Record<string, unknown>
}

const chatId = 'browser-contract-chat'

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

async function installApi(page: Page, options: MockOptions = {}) {
  const run = options.run ?? null
  await page.route('**/*', async route => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    if (
      path.startsWith('/ui/') || path.startsWith('/@vite/') ||
      path.includes('/node_modules/') || path.startsWith('/src/') ||
      path.endsWith('.tsx') || path.endsWith('.ts') || path.endsWith('.css')
    ) return route.continue()
    if (path === '/projects') return json(route, { projects: [] })
    if (path === '/sessions') {
      if (request.method() === 'POST') return json(route, { chat_id: chatId })
      return json(route, { sessions: [{ chat_id: chatId, title: 'UI contract', run }] })
    }
    if (path === '/assets') return json(route, { assets: [] })
    if (path === '/workspace') return json(route, { workspace: '.', grants: [] })
    if (path === '/runs' && request.method() === 'POST' && options.runPost) {
      return options.runPost(route)
    }
    if (/\/runs\/[^/]+\/turns$/.test(path)) return json(route, { turns: [] })
    if (/\/runs\/[^/]+\/events$/.test(path) && options.eventPage) {
      return json(route, options.eventPage(Number(url.searchParams.get('after') || 0)))
    }
    return json(route, { detail: `Unexpected mock route: ${request.method()} ${path}` }, 404)
  })
}

test('ui.send paints pending input before acknowledgement and restores failed input', async ({ page }, testInfo) => {
  let releaseAcknowledgement: () => void = () => {}
  const heldAcknowledgement = new Promise<void>(resolve => { releaseAcknowledgement = resolve })
  await installApi(page, {
    runPost: async route => {
      await heldAcknowledgement
      await json(route, { detail: 'injected submit failure' }, 503)
    },
  })
  await page.goto('.')
  const editor = page.getByPlaceholder('输入任务要求…')
  await editor.fill('立即显示这条消息')
  const submittedAt = Date.now()
  await page.getByRole('button', { name: '发送' }).click()
  await expect(page.getByText('立即显示这条消息', { exact: true })).toBeVisible()
  await expect(page.getByText('正在提交…')).toBeVisible()
  const paintDelayMs = Date.now() - submittedAt
  releaseAcknowledgement()
  await expect(page.getByText('injected submit failure')).toBeVisible()
  await expect(editor).toHaveValue('立即显示这条消息')
  await testInfo.attach('ui-send-observation.json', {
    body: JSON.stringify({ paintDelayMs, visibleBeforeAcknowledgement: true, failedInputRestored: true }),
    contentType: 'application/json',
  })
})

test('ui.reconnect paginates, deduplicates and restores the terminal state', async ({ page }, testInfo) => {
  const initialRun = {
    id: 'run-ui-reconnect', chat_id: chatId, state: 'running', model_calls: 1,
    created_at: 1, updated_at: 1, state_version: 1,
  }
  const events = Array.from({ length: 10_005 }, (_, index) => ({
    seq: index + 1,
    type: index === 10_004 ? 'done' : 'run_state',
    state: index === 10_004 ? 'completed' : 'running',
  }))
  await installApi(page, {
    run: initialRun,
    eventPage: after => {
      const pageEvents = events.slice(after, after + 1000)
      const next = pageEvents.at(-1)?.seq ?? after
      return {
        events: pageEvents,
        next,
        has_more: next < events.length,
        run: { ...initialRun, state: next >= events.length ? 'completed' : 'running', state_version: 2 },
      }
    },
  })
  await page.goto('.')
  await expect(page.getByText('10005 events · 0 turns')).toBeVisible({ timeout: 45_000 })
  await expect(page.locator('.topbar .state')).toContainText('completed')
  await page.reload()
  await expect(page.getByText('10005 events · 0 turns')).toBeVisible({ timeout: 45_000 })
  await expect(page.locator('.topbar .state')).toContainText('completed')
  await testInfo.attach('ui-reconnect-observation.json', {
    body: JSON.stringify({ eventCount: 10_005, uniqueSequenceCount: 10_005, terminalAfterReload: 'completed' }),
    contentType: 'application/json',
  })
})
