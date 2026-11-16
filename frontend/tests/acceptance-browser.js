/**
 * Run with the project's cached Playwright CLI:
 * playwright-cli --session ntou-qa run-code --filename frontend/tests/acceptance-browser.js
 *
 * Prerequisites: run from the repository root, use the isolated schema created by
 * seed_acceptance_demo, and serve driver.html with VITE_DELIVERER_ID=2. Seeded buyer
 * 1 and meals 1 (80) / 2 (100) must exist. First accept the two initial pending seed
 * orders so the lobby is empty; initial-empty intentionally fails otherwise.
 * Start the local backend/frontend services and create artifacts/browser first.
 *
 * Local synthetic test data only. This runner creates two orders for seeded buyer 1,
 * meals 1/2, then accepts them through the UI. It never resets or drops the database.
 * Fault-injection scenarios intercept the browser only and are labelled in results.
 * The caller must treat `passed: false` or a failed case as a failed verification.
 */
async (page) => {
  const FRONTEND = 'http://127.0.0.1:5173/driver.html'
  const API = 'http://127.0.0.1:5000'
  const OUTPUT = 'artifacts/browser'
  const runId = `qa-${Date.now()}`
  const cases = []
  const createdOrders = []
  const acceptRequests = []
  const pageErrors = []
  let scenario = 'setup'
  let currentOrder
  let previousCreateTime = 0

  page.on('pageerror', (error) => pageErrors.push({ scenario, message: error.message }))
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/api/process_order' && request.method() === 'POST') {
      acceptRequests.push({ scenario, body: request.postDataJSON(), at: new Date().toISOString() })
    }
  })

  function assert(condition, message) {
    if (!condition) throw new Error(message)
  }

  async function ready() {
    await page.waitForFunction(() => {
      const button = [...document.querySelectorAll('button')].find(item => item.textContent.trim() === '重新整理')
      return button && !button.disabled
    }, undefined, { timeout: 25000 })
  }

  async function api(path, data) {
    const response = data === undefined
      ? await page.request.get(`${API}${path}`)
      : await page.request.post(`${API}${path}`, { data })
    const body = await response.json()
    assert(response.ok(), `${path} returned ${response.status()}: ${JSON.stringify(body)}`)
    return { status: response.status(), body }
  }

  function stamp(order) {
    return { buyer_id: '1', timestamp: order.timestamp }
  }

  function card(order) {
    return page.locator('.order').filter({ hasText: order.timestamp })
  }

  async function refresh() {
    await page.getByRole('button', { name: '重新整理', exact: true }).click()
    await ready()
  }

  async function state(order) {
    const meals = await api('/api/view_meals_of_order', stamp(order))
    const delivery = await api('/api/view_delivery', stamp(order))
    return { meals: meals.body, delivery: delivery.body }
  }

  async function createOrder(label) {
    // The existing schema uses buyer + second-resolution timestamp. Avoid colliding
    // with another synthetic order created by this runner; do not change that policy.
    const remaining = 1100 - (Date.now() - previousCreateTime)
    if (remaining > 0) await page.waitForTimeout(remaining)
    const created = await api('/api/add_new_order', {
      buyer_id: 1,
      meals: [
        { meal_id: 1, quantity: 1, note: `${runId} ${label} synthetic only` },
        { meal_id: 2, quantity: 2, note: 'QA 合成測試，不是真實外送訂單' },
      ],
    })
    previousCreateTime = Date.now()
    assert(created.status === 201 && created.body.success === true, 'Synthetic order was not created')
    const order = { buyer_id: '1', timestamp: created.body.timestamp, label }
    createdOrders.push(order)
    const delivery = await api('/api/add_new_delivery', {
      ...stamp(order), pickup_address: 'QA 合成測試資料｜海大測試地點，不需配送',
    })
    assert(delivery.status === 201, 'Synthetic Delivery was not created')
    return order
  }

  async function run(name, mode, operation) {
    scenario = name
    const started = Date.now()
    try {
      const detail = await operation()
      cases.push({ name, mode, passed: true, duration_ms: Date.now() - started, ...detail })
    } catch (error) {
      cases.push({ name, mode, passed: false, duration_ms: Date.now() - started, error: error.message })
      await page.screenshot({ path: `${OUTPUT}/qa-failure-${name}.png`, fullPage: true }).catch(() => {})
    }
  }

  async function mockAccept(name, handler, expected, expectedKind) {
    await run(name, 'browser fault injection; no real acceptance POST', async () => {
      assert(currentOrder, 'The runner has no synthetic order')
      await ready()
      const before = acceptRequests.length
      await page.route('**/api/process_order', handler)
      try {
        await card(currentOrder).getByRole('button', { name: '接單', exact: true }).click()
        await page.locator(`.notice.${expectedKind}`).filter({ hasText: expected }).waitFor({ timeout: 20000 })
        await ready()
        await page.waitForTimeout(250)
        assert(acceptRequests.length - before === 1, 'Acceptance POST was automatically resent')
        assert(await page.locator('.notice.success').count() === 0, 'Fault was shown as a successful acceptance')
        assert(await card(currentOrder).count() === 1, 'The pending synthetic order disappeared after a mocked failure')
        assert(await card(currentOrder).getByRole('button', { name: '接單', exact: true }).isEnabled(), 'Accept button did not recover')
        const after = await state(currentOrder)
        assert(after.meals.every(item => item.order_state === 'pending'), 'Mocked failure changed real order state')
        assert(after.delivery[0].deliverer_id === null, 'Mocked failure assigned the real order')
        return { accept_post_count: acceptRequests.length - before, notice: await page.locator('.notice').innerText(), backend_state: after }
      } finally {
        await page.unroute('**/api/process_order', handler)
      }
    })
  }

  await page.setViewportSize({ width: 1100, height: 850 })
  await page.goto(FRONTEND)
  await run('initial-empty', 'real local API', async () => {
    await ready()
    assert(await page.getByText('目前沒有待接訂單', { exact: true }).isVisible(), 'Initial queue was not empty')
    assert(await page.locator('.order').count() === 0, 'Initial queue contains cards')
    assert((await page.locator('.demo-account').innerText()).includes('#2'), 'This run expects demo courier 2')
    await page.screenshot({ path: `${OUTPUT}/qa-initial-empty.png`, fullPage: true })
    return { empty: true, demo_account: await page.locator('.demo-account').innerText() }
  })

  await run('real-order-two-meals', 'real local API; creates synthetic order', async () => {
    currentOrder = await createOrder('ui-validation')
    await refresh()
    assert(await card(currentOrder).count() === 1, 'Created order is not shown')
    assert(await card(currentOrder).locator('.item').count() === 2, 'Expected two meal rows')
    assert(await card(currentOrder).locator('.total').innerText() === '餐點小計：NT$ 280', 'Expected food subtotal 280')
    await page.screenshot({ path: `${OUTPUT}/qa-two-meals.png`, fullPage: true })
    return { order: currentOrder, subtotal: 280 }
  })

  await mockAccept('known-400', route => route.fulfill({
    status: 400, contentType: 'application/json', body: JSON.stringify({ success: false, message: 'QA 注入 400：請求資料錯誤' }),
  }), 'QA 注入 400：請求資料錯誤', 'error')
  await mockAccept('known-404', route => route.fulfill({
    status: 404, contentType: 'application/json', body: JSON.stringify({ success: false, message: 'QA 注入 404：找不到外送員' }),
  }), 'QA 注入 404：找不到外送員', 'error')
  await mockAccept('unknown-500', route => route.fulfill({
    status: 500, contentType: 'application/json', body: JSON.stringify({ success: false, message: 'QA injected server error' }),
  }), '尚無法確認接單結果', 'unknown')
  await mockAccept('unknown-non-json', route => route.fulfill({
    status: 200, contentType: 'text/html', body: '<html>QA injected non-JSON response</html>',
  }), '尚無法確認接單結果', 'unknown')
  await mockAccept('unknown-missing-identity', route => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ success: true }),
  }), '尚無法確認接單結果', 'unknown')
  await mockAccept('unknown-network-abort', route => route.abort('failed'), '尚無法確認接單結果', 'unknown')

  await mockAccept('unknown-timeout-15s', async route => {
    // Intentionally leave the request unresolved beyond the application's 15 s
    // timeout. The real API is not called and the browser must not resend it.
    await new Promise(resolve => setTimeout(resolve, 16000))
    await route.abort('timedout').catch(() => {})
  }, '尚無法確認接單結果', 'unknown')
  const timeoutCase = cases.find(item => item.name === 'unknown-timeout-15s')
  if (timeoutCase?.passed && timeoutCase.duration_ms < 14500) {
    timeoutCase.passed = false
    timeoutCase.error = 'The expected 15 second timeout was not observed'
  }

  for (const [name, path, responseFor] of [
    ['empty-order-meals', '/api/view_meals_of_order', () => []],
    ['empty-delivery', '/api/view_delivery', () => []],
    ['mixed-order-states', '/api/view_meals_of_order', real => real.map((item, index) => ({ ...item, order_state: index ? 'processing' : 'pending' }))],
  ]) {
    await run(name, 'browser payload fault injection; database unchanged', async () => {
      assert(currentOrder, 'The runner has no synthetic order')
      const real = await api(path, stamp(currentOrder))
      const handler = async route => {
        const body = route.request().postDataJSON()
        if (body.timestamp === currentOrder.timestamp && String(body.buyer_id) === '1') {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(responseFor(real.body)) })
        } else {
          await route.continue()
        }
      }
      await page.route(`**${path}`, handler)
      try {
        await refresh()
        assert(await page.getByRole('heading', { name: '外送員接單大廳' }).isVisible(), 'Page became blank')
        assert(await card(currentOrder).count() === 0, 'Invalid/non-pending order is still shown')
        assert(await page.getByText('目前沒有待接訂單', { exact: true }).isVisible(), 'Filtered queue did not show empty state')
      } finally {
        await page.unroute(`**${path}`, handler)
        await refresh()
      }
      assert(await card(currentOrder).count() === 1, 'Queue did not recover after removing payload injection')
      return { filtered: true, recovered: true }
    })
  }

  await run('mobile-390x844', 'real local API; viewport inspection', async () => {
    await page.setViewportSize({ width: 390, height: 844 })
    await ready()
    const dimensions = await page.evaluate(() => ({
      viewport: window.innerWidth,
      document: document.documentElement.scrollWidth,
      body: document.body.scrollWidth,
    }))
    assert(dimensions.document <= dimensions.viewport && dimensions.body <= dimensions.viewport, `Horizontal overflow: ${JSON.stringify(dimensions)}`)
    assert(await card(currentOrder).getByRole('button', { name: '接單', exact: true }).isVisible(), 'Mobile accept button not visible')
    await page.screenshot({ path: `${OUTPUT}/qa-mobile-390x844.png`, fullPage: true })
    await page.setViewportSize({ width: 1100, height: 850 })
    return dimensions
  })

  await run('rapid-click-single-post', 'real local API; browser route adds a deliberate 300 ms delay only', async () => {
    const before = acceptRequests.length
    const handler = async route => {
      await new Promise(resolve => setTimeout(resolve, 300))
      await route.continue()
    }
    await page.route('**/api/process_order', handler)
    try {
      // A synchronous burst also exercises the handler guard before Vue can paint
      // the disabled state. This does not use multiple users or simulate DB races.
      await card(currentOrder).getByRole('button', { name: '接單', exact: true }).evaluate(button => {
        for (let index = 0; index < 8; index++) button.click()
      })
      await page.getByRole('button', { name: '接單中……', exact: true }).waitFor({ timeout: 2000 })
      assert(await page.getByRole('button', { name: '重新整理', exact: true }).isDisabled(), 'Refresh remains enabled during acceptance')
      assert(await page.locator('button.accept').evaluateAll(buttons => buttons.every(button => button.disabled)), 'Some accept buttons remain enabled')
      await page.locator('.notice.success').filter({ hasText: '接單成功！' }).waitFor({ timeout: 10000 })
      await ready()
      await page.waitForTimeout(300)
      assert(acceptRequests.length - before === 1, `Expected one POST, observed ${acceptRequests.length - before}`)
      assert(await card(currentOrder).count() === 0, 'Successfully accepted order remained visible')
      const after = await state(currentOrder)
      assert(after.meals.length === 2 && after.meals.every(item => item.order_state === 'processing'), 'All meal rows were not accepted together')
      assert(after.delivery[0].deliverer_id === 2, 'Delivery is not assigned to demo courier 2')
      await page.screenshot({ path: `${OUTPUT}/qa-rapid-click-success.png`, fullPage: true })
      return { dom_clicks: 8, accept_post_count: acceptRequests.length - before, backend_state: after }
    } finally {
      await page.unroute('**/api/process_order', handler)
    }
  })

  await run('success-then-refresh-failure', 'real acceptance API; only subsequent list GET is fault-injected', async () => {
    currentOrder = await createOrder('refresh-after-success')
    await refresh()
    const before = acceptRequests.length
    let failReload = false
    const mealHandler = route => failReload
      ? route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ message: 'QA 注入：接單後清單載入失敗' }) })
      : route.continue()
    const acceptHandler = async route => {
      const response = await route.fetch()
      failReload = response.ok()
      await route.fulfill({ response })
    }
    await page.route('**/api/view_all_meals', mealHandler)
    await page.route('**/api/process_order', acceptHandler)
    try {
      await card(currentOrder).getByRole('button', { name: '接單', exact: true }).click()
      await page.locator('.notice.success').filter({ hasText: '接單成功！' }).waitFor({ timeout: 10000 })
      await page.getByRole('alert').filter({ hasText: 'QA 注入：接單後清單載入失敗' }).waitFor({ timeout: 10000 })
      await ready()
      assert(await page.locator('.notice.error').count() === 0, 'Refresh failure replaced confirmed acceptance with a failure')
      assert(acceptRequests.length - before === 1, 'Acceptance was resent after refresh failure')
      const after = await state(currentOrder)
      assert(after.meals.every(item => item.order_state === 'processing') && after.delivery[0].deliverer_id === 2, 'Real acceptance did not persist')
      await page.screenshot({ path: `${OUTPUT}/qa-success-refresh-failure.png`, fullPage: true })
      return { accept_post_count: acceptRequests.length - before, backend_state: after }
    } finally {
      await page.unroute('**/api/view_all_meals', mealHandler)
      await page.unroute('**/api/process_order', acceptHandler)
    }
  })

  await run('refresh-recovery', 'real local API; all fault routes removed', async () => {
    await refresh()
    assert(await page.getByRole('alert').count() === 0, 'Load error did not clear')
    assert(await page.getByText('目前沒有待接訂單', { exact: true }).isVisible(), 'Accepted orders were not hidden')
    assert(await page.locator('.order').count() === 0, 'Queue is not empty after acceptance')
    const finalStates = []
    for (const order of createdOrders) finalStates.push({ order, ...await state(order) })
    assert(finalStates.every(item => item.meals.every(meal => meal.order_state === 'processing') && item.delivery[0].deliverer_id === 2), 'Some synthetic orders were not consistently assigned')
    await page.screenshot({ path: `${OUTPUT}/qa-final-empty.png`, fullPage: true })
    return { final_states: finalStates }
  })

  const report = {
    schema: 'ntou.acceptance.browser-qa.v1',
    run_id: runId,
    completed_at: new Date().toISOString(),
    frontend: FRONTEND,
    backend: API,
    browser: await page.context().browser().version(),
    passed: cases.every(item => item.passed) && pageErrors.length === 0,
    total: cases.length,
    passed_cases: cases.filter(item => item.passed).length,
    cases,
    page_errors: pageErrors,
    accept_requests: acceptRequests,
    synthetic_orders: createdOrders,
    limits: [
      'Prerequisites: isolated seed_acceptance_demo schema, buyer 1, meals 1=80 and 2=100, demo courier 2, and initial pending seed orders already accepted. The initial queue must be empty.',
      'This is a local synthetic-data browser verification, not a shared/production environment test.',
      'HTTP failures, malformed payloads, network abort and timeout are deliberate browser route injections.',
      'The rapid-click test uses one page and one courier; it is not the two-transaction MySQL race test.',
      'No login, authorization, delivery fee, cancellation policy or entire buyer-to-completion workflow is claimed.',
    ],
  }
  return report
}
