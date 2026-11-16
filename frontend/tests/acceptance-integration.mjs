import { mkdir } from 'node:fs/promises'
import path from 'node:path'

/** Run against a fresh local seed and three Vite instances (courier A/B/invalid ID). */
export default async function runIntegration({ browser, frontendUrls, apiBase, seed, outputDir }) {
  const assert = (condition, message) => { if (!condition) throw new Error(message) }
  for (const url of [apiBase, ...frontendUrls]) {
    assert(['localhost', '127.0.0.1'].includes(new URL(url).hostname), 'Only localhost services are allowed')
  }
  assert(seed.metadata?.synthetic_data_only === true, 'A synthetic seed is required')
  const fixture = seed.fixtures.find(item => item.scenario === 'ready_multi_meal')
  const couriers = seed.demo_courier_ids
  assert(fixture?.meal_ids.length >= 2 && couriers?.length === 2, 'Missing buyer, meals or two courier IDs')
  assert(couriers[0] !== couriers[1], 'Courier IDs must differ')
  await mkdir(outputDir, { recursive: true })

  const cases = [], orders = [], pageErrors = [], requests = [], apiCalls = []
  const contexts = [], pages = []
  const runId = `integration-${Date.now()}`
  let scenario = 'setup', lastCreate = Date.now(), delayedOrder, mealCatalog = []
  const stamp = order => ({ buyer_id: order.buyer_id, timestamp: order.timestamp })
  const card = (page, order) => page.locator('.order')
    .filter({ has: page.getByText(`買家 #${order.buyer_id}`, { exact: true }) })
    .filter({ has: page.getByText(order.timestamp, { exact: true }) })
  const ownCard = (page, order) => page.getByTestId('accepted-order')
    .filter({ has: page.getByText(`買家 #${order.buyer_id}`, { exact: true }) })
    .filter({ has: page.getByText(order.timestamp, { exact: true }) })
  const acceptButton = (page, order) => card(page, order).getByRole('button', { name: '接單', exact: true })
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms))
  const orderRequests = order => requests.filter(item => String(item.body.buyer_id) === String(order.buyer_id)
    && item.body.timestamp === order.timestamp)

  async function ready(page) {
    await page.waitForFunction(() => [...document.querySelectorAll('button')]
      .some(button => button.textContent.trim() === '重新整理' && !button.disabled), null, { timeout: 25000 })
    assert(await page.getByRole('alert').count() === 0, 'Lobby failed to load')
  }
  async function refresh(page) {
    await ready(page)
    await page.getByRole('button', { name: '重新整理', exact: true }).click()
    await ready(page)
  }
  async function api(endpoint, data) {
    const response = data === undefined
      ? await contexts[0].request.get(`${apiBase}${endpoint}`)
      : await contexts[0].request.post(`${apiBase}${endpoint}`, { data })
    const body = await response.json()
    apiCalls.push({ scenario, endpoint, request: data, status: response.status(), body })
    assert(response.ok(), `${endpoint}: HTTP ${response.status()}`)
    return { status: response.status(), body }
  }
  async function state(order) {
    const meals = (await api('/api/view_meals_of_order', stamp(order))).body
    const delivery = (await api('/api/view_delivery', stamp(order))).body
    return { meals, delivery }
  }
  async function assigned(order, courier) {
    const result = await state(order)
    assert(result.meals.length === 2 && result.meals.every(meal => meal.order_state === 'processing'), 'Not all meal rows were accepted')
    assert(result.delivery.length === 1 && result.delivery[0].deliverer_id === courier, 'Delivery has the wrong courier')
    order.expected_deliverer_id = courier
    order.expected_states = ['PROCESSING', 'PROCESSING']
    return result
  }
  async function ownOrder(owner, other, order) {
    const own = ownCard(owner, order)
    assert(await own.count() === 1, 'Accepted order is missing from its courier page')
    assert(await ownCard(other, order).count() === 0, 'Another courier can see this order as their own')
    const rows = (await own.locator('.item > span:first-child').allTextContents()).map(row => row.trim())
    assert(rows.length === order.expected_items.length, 'Own order has the wrong meal count')
    for (const item of order.expected_items) {
      const prefix = `${item.meal_name} × ${item.quantity}（`
      assert(rows.some(row => row.startsWith(prefix) && row.endsWith('）')), 'Own order has the wrong meal or quantity')
    }
    return { owner_card_count: 1, other_courier_card_count: 0, meal_rows: rows }
  }
  async function createOrder(label, withDelivery = true) {
    // The current order key has second precision; keep new orders in different seconds.
    const remaining = 1100 - (Date.now() - lastCreate)
    if (remaining > 0) await pause(remaining)
    const created = await api('/api/add_new_order', {
      buyer_id: fixture.buyer_id,
      meals: fixture.meal_ids.slice(0, 2).map((meal_id, index) => ({
        meal_id, quantity: index + 1, note: `${runId} ${label}｜合成測試資料`,
      })),
    })
    lastCreate = Date.now()
    assert(created.status === 201 && created.body.success === true, 'Order creation failed')
    const order = {
      buyer_id: fixture.buyer_id, timestamp: created.body.timestamp, label,
      order_ids: created.body.orders.map(item => item.order_id),
      expected_deliverer_id: null, expected_states: ['PENDING', 'PENDING'],
      expected_items: created.body.orders.map(item => {
        const meal = mealCatalog.find(meal => meal.meal_id === item.meal_id)
        assert(meal, 'Created meal is absent from the catalog')
        return { meal_id: item.meal_id, meal_name: meal.meal_name, quantity: item.quantity }
      }),
    }
    assert(typeof order.timestamp === 'string', 'Order response omitted timestamp')
    assert(!orders.some(item => item.timestamp === order.timestamp), 'Order timestamp collided')
    orders.push(order)
    if (withDelivery) await addDelivery(order)
    return order
  }
  async function addDelivery(order) {
    const result = await api('/api/add_new_delivery', {
      ...stamp(order), pickup_address: '聯測交付點（合成資料，不需配送）',
    })
    assert(result.status === 201, 'Delivery creation failed')
  }
  const acceptanceResponse = (page, order) => page.waitForResponse(response => {
    const request = response.request()
    if (new URL(response.url()).pathname !== '/api/process_order' || request.method() !== 'POST') return false
    const body = request.postDataJSON()
    return String(body.buyer_id) === String(order.buyer_id) && body.timestamp === order.timestamp
  }, { timeout: 20000 })
  const responseDetail = async response => ({ status: response.status(), body: await response.json() })
  async function run(name, mode, operation) {
    scenario = name
    const started = Date.now()
    try {
      const detail = await operation()
      cases.push({ name, mode, passed: true, duration_ms: Date.now() - started, ...detail })
    } catch (error) {
      cases.push({ name, mode, passed: false, duration_ms: Date.now() - started, error: error.message })
      await Promise.all(pages.map((page, index) => page.screenshot({
        path: path.join(outputDir, `failed-${name}-${index}.png`), fullPage: true,
      }).catch(() => {})))
    }
  }

  try {
    for (let index = 0; index < frontendUrls.length; index++) {
      const context = await browser.newContext({ viewport: { width: 1100, height: 850 } })
      contexts.push(context)
      const page = await context.newPage()
      pages.push(page)
      page.on('pageerror', error => pageErrors.push({ scenario, page: index, message: error.message }))
      page.on('request', request => {
        if (new URL(request.url()).pathname === '/api/process_order' && request.method() === 'POST') {
          requests.push({ scenario, page: index, body: request.postDataJSON() })
        }
      })
      await page.goto(frontendUrls[index])
    }
    const [a, b, invalid] = pages
    await run('two-courier-pages', 'real local API; separate browser contexts', async () => {
      mealCatalog = (await api('/api/view_all_meals')).body
      for (const [index, page] of [a, b].entries()) {
        await ready(page)
        const text = await page.locator('.demo-account').innerText()
        assert(new RegExp(`#${couriers[index]}(?:\\s|（)`).test(text), 'Page uses the wrong courier ID')
        assert(await page.getByRole('heading', { name: '我的已接訂單', exact: true }).isVisible(), 'Own orders section is missing')
      }
      const otherOrder = seed.fixtures.find(item => item.scenario === 'hidden_already_accepted')
      assert(otherOrder, 'Need an order already assigned to another courier')
      const otherState = await state(otherOrder)
      assert(!couriers.includes(otherState.delivery[0].deliverer_id), 'Filter fixture must belong to another courier')
      for (const page of [a, b]) assert(await ownCard(page, otherOrder).count() === 0, 'Own list contains another courier order')
      return { courier_ids: couriers, initial_card_counts: await Promise.all([a, b].map(p => p.locator('.order').count())),
        other_courier_order_filtered: stamp(otherOrder) }
    })

    await run('delivery-created-after-order', 'real order and Delivery APIs', async () => {
      delayedOrder = await createOrder('delayed-delivery', false)
      for (const page of [a, b]) {
        await refresh(page)
        assert(await card(page, delayedOrder).count() === 0, 'Order without Delivery is visible')
      }
      await addDelivery(delayedOrder)
      for (const page of [a, b]) {
        await refresh(page)
        assert(await card(page, delayedOrder).count() === 1, 'Order did not appear after Delivery creation')
        assert(await card(page, delayedOrder).locator('.item').count() === 2, 'Meal rows are missing')
      }
      await a.screenshot({ path: path.join(outputDir, 'delivery-created.png'), fullPage: true })
      return { order: stamp(delayedOrder) }
    })

    await run('stale-second-courier-409', 'two pages; real 200 then real 409', async () => {
      assert(delayedOrder, 'Previous case did not create an order')
      const firstResponse = acceptanceResponse(a, delayedOrder)
      await acceptButton(a, delayedOrder).click()
      const first = await responseDetail(await firstResponse)
      assert(first.status === 200, 'First courier did not accept')
      await ready(a)
      assert(await card(b, delayedOrder).count() === 1, 'Second page must still show its stale card')
      const secondResponse = acceptanceResponse(b, delayedOrder)
      await acceptButton(b, delayedOrder).click()
      const second = await responseDetail(await secondResponse)
      assert(second.status === 409, 'Stale second acceptance must return 409')
      await ready(b)
      assert(await b.locator('.notice.error').count() === 1, 'Second courier did not see the rejection')
      assert(await card(a, delayedOrder).count() === 0 && await card(b, delayedOrder).count() === 0, 'Accepted order remained visible')
      const firstOwn = await ownOrder(a, b, delayedOrder)
      await b.screenshot({ path: path.join(outputDir, 'stale-second-courier.png'), fullPage: true })
      const firstPersisted = await assigned(delayedOrder, couriers[0])

      const bOrder = await createOrder('courier-b-own-order')
      await Promise.all([a, b].map(refresh))
      const bResponse = acceptanceResponse(b, bOrder)
      await acceptButton(b, bOrder).click()
      const bAccepted = await responseDetail(await bResponse)
      assert(bAccepted.status === 200, 'Second courier could not accept another order')
      await ready(b)
      await refresh(a)
      const secondOwn = await ownOrder(b, a, bOrder)
      const secondPersisted = await assigned(bOrder, couriers[1])
      await Promise.all([a, b].map(refresh))
      await ownOrder(a, b, delayedOrder)
      await ownOrder(b, a, bOrder)
      await Promise.all([a, b].map((page, index) => page.screenshot({
        path: path.join(outputDir, `own-orders-${index}.png`), fullPage: true,
      })))
      return { responses: [first, second, bAccepted], first_own_order: firstOwn, second_own_order: secondOwn,
        persisted: firstPersisted, second_order: stamp(bOrder), second_persisted: secondPersisted,
        both_own_lists_survive_refresh: true }
    })

    await run('two-pages-compete', 'real acceptance requests; barrier delays sending until both clicks arrive', async () => {
      const order = await createOrder('two-pages-compete')
      await Promise.all([a, b].map(refresh))
      let arrivals = 0, release, timedOut = false
      const gate = new Promise(resolve => { release = resolve })
      const timer = setTimeout(() => { timedOut = true; release() }, 8000)
      const handler = async route => {
        arrivals++
        if (arrivals === 2) release()
        await gate
        await route.continue()
      }
      await Promise.all([a, b].map(page => page.route('**/api/process_order', handler)))
      try {
        const pendingResponses = [acceptanceResponse(a, order), acceptanceResponse(b, order)]
        await Promise.all([a, b].map(page => acceptButton(page, order).click()))
        const responses = await Promise.all((await Promise.all(pendingResponses)).map(responseDetail))
        assert(arrivals === 2 && !timedOut, 'Both UI requests did not reach the barrier')
        assert(responses.map(r => r.status).sort().join(',') === '200,409', 'Expected exactly one 200 and one 409')
        await Promise.all([a, b].map(ready))
        const winner = responses.findIndex(r => r.status === 200)
        assert(await pages[winner].locator('.notice.success').count() === 1, 'Winner has no success message')
        assert(await pages[1 - winner].locator('.notice.error').count() === 1, 'Losing courier has no error message')
        assert((await Promise.all([a, b].map(p => card(p, order).count()))).every(count => count === 0), 'Competed order remained visible')
        const own = await ownOrder(pages[winner], pages[1 - winner], order)
        await Promise.all([a, b].map((p, i) => p.screenshot({ path: path.join(outputDir, `compete-${i}.png`), fullPage: true })))
        return { order: stamp(order), requests: orderRequests(order), responses, own_order: own, persisted: await assigned(order, couriers[winner]) }
      } finally {
        clearTimeout(timer)
        release()
        await Promise.all([a, b].map(page => page.unroute('**/api/process_order', handler)))
      }
    })

    await run('response-lost-after-commit', 'real acceptance commits; only its browser response is aborted', async () => {
      const order = await createOrder('lost-response')
      await refresh(a)
      let upstream
      const handler = async route => {
        const response = await route.fetch()
        upstream = await responseDetail(response)
        await route.abort('failed')
      }
      await a.route('**/api/process_order', handler)
      try {
        await acceptButton(a, order).click()
        await a.locator('.notice.unknown').waitFor()
        await ready(a)
        await pause(400)
        assert(upstream?.status === 200, 'Real acceptance did not commit before the simulated disconnect')
        assert(orderRequests(order).length === 1, 'Client resent the acceptance POST')
        assert(await card(a, order).count() === 0, 'Refresh did not hide the committed order')
        await refresh(b)
        const recoveredOwn = await ownOrder(a, b, order)
        await a.screenshot({ path: path.join(outputDir, 'response-lost.png'), fullPage: true })
        return { order: stamp(order), upstream_response: upstream, accept_post_count: orderRequests(order).length,
          recovered_own_order: recoveredOwn, persisted: await assigned(order, couriers[0]) }
      } finally { await a.unroute('**/api/process_order', handler) }
    })

    await run('own-orders-load-error-recovery', 'browser-injected JSON 500; real API resumes after removing the route', async () => {
      const aOrder = orders.find(order => order.label === 'delayed-delivery')
      const bOrder = orders.find(order => order.label === 'courier-b-own-order')
      assert(aOrder?.expected_deliverer_id === couriers[0] && bOrder?.expected_deliverer_id === couriers[1], 'Need one accepted order per courier')
      await ownOrder(a, b, aOrder)
      await ownOrder(b, a, bOrder)
      const acceptCount = requests.length
      const message = '聯測模擬：訂單清單暫時讀取失敗'
      let injectedResponses = 0
      const handler = async route => {
        injectedResponses++
        await route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ message }) })
      }
      await a.route('**/api/view_order_by_stamp', handler)
      try {
        await ready(a)
        await a.getByRole('button', { name: '重新整理', exact: true }).click()
        await a.getByRole('alert').filter({ hasText: message }).waitFor()
        assert(injectedResponses === 1, 'Expected exactly one injected list failure')
        assert(await a.getByTestId('accepted-orders').count() === 0, 'Load failure left the own-orders section visible')
        assert(await a.getByText('目前沒有已接訂單', { exact: true }).count() === 0, 'Load failure was presented as an empty own-orders list')
        assert(await a.getByRole('button', { name: '重新整理', exact: true }).isEnabled(), 'Refresh did not recover after the load failure')
        await a.screenshot({ path: path.join(outputDir, 'own-orders-load-error.png'), fullPage: true })
      } finally {
        await a.unroute('**/api/view_order_by_stamp', handler)
        await a.getByRole('button', { name: '重新整理', exact: true }).click()
        await ready(a)
      }
      const recovered = await ownOrder(a, b, aOrder)
      await ownOrder(b, a, bOrder)
      assert(requests.length === acceptCount, 'List recovery sent an acceptance POST')
      await a.screenshot({ path: path.join(outputDir, 'own-orders-recovered.png'), fullPage: true })
      return { simulated_failure: { endpoint: '/api/view_order_by_stamp', status: 500, count: injectedResponses },
        own_order: stamp(aOrder), other_courier_order: stamp(bOrder), recovered_own_order: recovered,
        accept_post_count: requests.length - acceptCount }
    })

    await run('invalid-courier-setting', 'invalid VITE_DELIVERER_ID; real pending cards', async () => {
      assert(invalid, 'A third frontend URL with invalid courier configuration is required')
      await ready(invalid)
      assert(await invalid.getByText('示範外送員帳號設定無效', { exact: false }).isVisible(), 'Invalid courier notice is missing')
      const buttons = invalid.locator('button.accept')
      assert(await buttons.count() > 0, 'Need a pending card to test the disabled accept button')
      assert(await buttons.evaluateAll(items => items.every(button => button.disabled)), 'Invalid courier can click accept')
      await buttons.first().evaluate(button => button.click())
      await pause(200)
      assert(!requests.some(item => item.page === 2), 'Invalid courier sent an acceptance request')
      assert(await invalid.getByTestId('accepted-orders').count() === 0, 'Invalid courier has an own-orders section')
      return { accept_post_count: 0 }
    })

    const cartNames = ['hidden_in_cart', 'hidden_mixed_cart']
    await run('in-cart-hidden', 'real IN_CART fixture rows', async () => {
      const cartFixtures = cartNames.map(name => {
        const matches = seed.fixtures.filter(item => item.scenario === name)
        assert(matches.length === 1, `Expected exactly one ${name} fixture`)
        return matches[0]
      })
      await refresh(a)
      for (const item of cartFixtures) {
        assert(await card(a, item).count() === 0, 'IN_CART order appeared in the lobby')
        assert(await ownCard(a, item).count() === 0, 'IN_CART order appeared in accepted orders')
      }
      return { hidden_orders: cartFixtures.map(stamp) }
    })
  } finally {
    await Promise.all(contexts.map(context => context.close()))
  }
  const expectedCases = ['two-courier-pages', 'delivery-created-after-order', 'stale-second-courier-409',
    'two-pages-compete', 'response-lost-after-commit', 'own-orders-load-error-recovery', 'invalid-courier-setting', 'in-cart-hidden']
  return {
    schema: 'ntou.acceptance.integration.v1', run_id: runId, completed_at: new Date().toISOString(),
    passed: cases.length === expectedCases.length
      && expectedCases.every(name => cases.filter(item => item.name === name && item.passed).length === 1)
      && pageErrors.length === 0,
    total: cases.length, passed_cases: cases.filter(item => item.passed).length,
    cases, page_errors: pageErrors, orders, accept_requests: requests, api_calls: apiCalls,
    limits: ['Local synthetic data and explicit courier IDs; login/roles are not tested.',
      'The two-page barrier coordinates HTTP requests; separate MySQL tests verify row-lock behavior.',
      'The lost-response case deliberately aborts the response after the real API commits.',
      'The own-orders load error is an injected browser response; the backend does not produce that 500.'],
  }
}
