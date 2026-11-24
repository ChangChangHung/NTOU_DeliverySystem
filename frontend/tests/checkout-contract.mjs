import assert from 'node:assert/strict'
import { readFile, writeFile, mkdir } from 'node:fs/promises'
import { createRequire } from 'node:module'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

const restaurants = [
  { restaurant_id: 10, restaurant_name: '測試便當店', restaurant_address: '校門旁測試地址' },
  { restaurant_id: 11, restaurant_name: '測試早餐店', restaurant_address: '宿舍旁測試地址' },
]
const meals = [
  { meal_id: 101, restaurant_id: 10, meal_name: '雞腿飯', price: 85 },
  { meal_id: 102, restaurant_id: 10, meal_name: '排骨飯', price: 75 },
  { meal_id: 103, restaurant_id: 11, meal_name: '蛋餅', price: 35 },
]
const timestamp = '2026-11-25 09:00:01'
const stampOf = row => ({ buyer_id: row.buyer_id, timestamp: row.timestamp })
const wait = ms => new Promise(resolve => setTimeout(resolve, ms))
async function eventually(check, message, timeout = 6000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) { if (await check()) return; await wait(30) }
  throw new Error(message)
}

/**
 * Every API response in this suite is synthetic. No request reaches a backend.
 * CLI config: { frontendUrl, outputDir, playwrightModule, channel?: 'chrome', caseFilter? }.
 * The frontend must use VITE_BUYER_ID=2 and VITE_DELIVERER_ID=2.
 */
export async function runCheckoutContract({ browser, frontendUrl, outputDir, caseFilter }) {
  const origin = new URL(frontendUrl).origin
  assert(['localhost', '127.0.0.1'].includes(new URL(origin).hostname), 'Only localhost frontend URLs are allowed')
  assert(!new URL(frontendUrl).username && !new URL(frontendUrl).password, 'URL credentials are not allowed')
  const output = path.join(outputDir, 'checkout-contract')
  await mkdir(output, { recursive: true })
  const cases = []

  async function run(name, options, verify) {
    if (caseFilter && !new RegExp(caseFilter).test(name)) return
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, serviceWorkers: 'block' })
    const page = await context.newPage()
    const state = { rows: [], delivery: null, calls: [], errors: [], unexpected: [], nextId: 501, ...options }
    page.setDefaultTimeout(7000)
    page.on('pageerror', error => state.errors.push(error.message))
    const count = endpoint => state.calls.filter(call => call.path === endpoint).length
    await context.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url())
      if (url.origin !== origin) { state.unexpected.push(url.origin); return route.abort('blockedbyclient') }
      if (!url.pathname.startsWith('/api/')) return route.continue()
      const body = req.method() === 'POST' ? req.postDataJSON() : undefined
      state.calls.push({ path: url.pathname, method: req.method(), body })
      const respond = (status, json) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(json) })
      switch (url.pathname) {
        case '/api/view_all_restaurants': return respond(200, restaurants)
        case '/api/view_all_meals': return respond(200, meals)
        case '/api/view_order_by_stamp': return respond(200, state.rows.length ? [stampOf(state.rows[0])] : [])
        case '/api/view_meals_of_order': {
          if (state.mealsReadFailure) return respond(500, { error: '測試用讀取失敗' })
          let result = state.rows.filter(row => String(row.buyer_id) === String(body.buyer_id) && row.timestamp === body.timestamp)
          if (state.extraRow && result.length) result = [...result, { ...result[0], order_id: 999 }]
          return respond(result.length ? 200 : 404, result.length ? result : { error: '找不到訂單' })
        }
        case '/api/view_delivery':
          if (state.deliveryHtml404) return route.fulfill({ status: 404, contentType: 'text/html', body: '<html>Not Found</html>' })
          if (state.deliveryReadFailure) return respond(500, { error: '測試用讀取失敗' })
          return respond(state.delivery ? 200 : 404, state.delivery ? [state.delivery] : { success: false, message: '找不到外送資料' })
        case '/api/add_new_order': {
          if (state.createDelay) await wait(state.createDelay)
          state.rows = body.meals.map(item => ({ ...item, note: item.note || null, buyer_id: Number(body.buyer_id),
            order_id: state.nextId++, timestamp, order_state: 'pending' }))
          if (state.createHtml404) return route.fulfill({ status: 404, contentType: 'text/html', body: '<html>Not Found</html>' })
          if (state.createLost) return route.abort('connectionreset')
          if (state.create500) return respond(500, { error: '寫入結果待確認' })
          return respond(201, { success: true, timestamp: '2026-11-25 09:00:00', orders: state.rows })
        }
        case '/api/add_new_delivery': {
          if (state.deliveryFailure) return respond(500, { error: '測試用地址寫入失敗' })
          state.delivery = { buyer_id: Number(body.buyer_id), timestamp: body.timestamp, pickup_address: body.pickup_address, deliverer_id: null }
          if (state.deliveryLost) return route.abort('connectionreset')
          return respond(201, { success: true, delivery: state.delivery })
        }
        case '/api/complete_order':
        case '/api/cancel_order':
        case '/api/abandon_order': {
          if (state.actionDelay) await wait(state.actionDelay)
          state.rows.forEach(row => { row.order_state = url.pathname.includes('complete') ? 'completed' : url.pathname.includes('cancel') ? 'cancelled' : 'pending' })
          if (url.pathname.includes('abandon') && state.delivery) state.delivery.deliverer_id = null
          const field = url.pathname.includes('complete') ? 'completed_orders' : url.pathname.includes('cancel') ? 'cancelled_orders' : 'updated_orders'
          return respond(200, { success: true, ...stampOf(state.rows[0]), [field]: state.rows, deliverer_id: state.delivery?.deliverer_id, message: '已更新訂單' })
        }
        default: state.unexpected.push(url.pathname); return respond(500, { error: '未定義的測試 API' })
      }
    })
    async function menu() {
      await page.goto(origin)
      await page.getByTestId('restaurant-card').filter({ hasText: '測試便當店' }).getByRole('button').click()
      await page.getByTestId('menu-item').filter({ hasText: '雞腿飯' }).waitFor()
    }
    async function add(name = '雞腿飯') {
      await page.getByTestId('menu-item').filter({ hasText: name }).getByRole('button').click()
    }
    async function prepare() {
      await menu(); await add()
      await page.getByTestId('checkout-address').fill('校門口測試交餐點')
    }
    async function submit() { await page.getByTestId('checkout-submit').click() }
    const started = Date.now()
    try {
      await verify({ page, context, state, count, menu, add, prepare, submit, origin })
      assert.equal(state.unexpected.length, 0, `Unexpected requests: ${state.unexpected.join(', ')}`)
      assert.equal(state.errors.length, 0, `Browser errors: ${state.errors.join(', ')}`)
      cases.push({ name, passed: true, mode: 'mock_api', duration_ms: Date.now() - started, requests: state.calls })
    } catch (error) {
      await page.screenshot({ path: path.join(output, `${String(cases.length + 1).padStart(2, '0')}-failed.png`), fullPage: true }).catch(() => {})
      cases.push({ name, passed: false, mode: 'mock_api', duration_ms: Date.now() - started, error: error.message,
        requests: state.calls, page_errors: state.errors, unexpected_requests: state.unexpected })
    } finally { await context.close() }
    console.log(JSON.stringify({ test: name, passed: cases.at(-1).passed, error: cases.at(-1).error }))
  }

  await run('餐廳與菜單使用既有資料庫欄位', {}, async ({ page, menu }) => {
    await menu()
    assert.equal(await page.getByTestId('menu-item').count(), 2)
    assert.equal(await page.getByTestId('menu-item').filter({ hasText: '蛋餅' }).count(), 0)
    assert.match(await page.getByTestId('menu-item').filter({ hasText: '雞腿飯' }).innerText(), /85/)
  })
  await run('購物車重整後保留，減到零會移除', {}, async ({ page, menu, add }) => {
    await menu(); await add(); await add('排骨飯')
    const chicken = page.getByTestId('cart-item').filter({ hasText: '雞腿飯' })
    await chicken.getByRole('button', { name: '增加數量', exact: true }).click()
    await chicken.getByTestId('cart-note').fill('不要香菜')
    assert.equal(await chicken.getByTestId('cart-note').getAttribute('maxlength'), '100')
    await page.getByTestId('checkout-address').fill('保留地址')
    await page.reload()
    await page.getByTestId('cart-item').filter({ hasText: '雞腿飯' }).waitFor()
    assert.match(await page.getByTestId('cart-total').innerText(), /245/)
    assert.equal(await page.getByTestId('checkout-address').inputValue(), '保留地址')
    assert.equal(await chicken.getByTestId('cart-note').inputValue(), '不要香菜')
    await page.getByTestId('cart-item').filter({ hasText: '排骨飯' }).getByRole('button', { name: '減少數量', exact: true }).click()
    assert.equal(await page.getByTestId('cart-item').count(), 1)
    assert.match(await page.getByTestId('cart-total').innerText(), /170/)
  })
  await run('空地址不能下單，地址輸入限制為 120 字', {}, async ({ page, count, menu, add, submit }) => {
    await menu(); await add(); await submit()
    assert.equal(count('/api/add_new_order'), 0)
    assert.equal(await page.getByTestId('checkout-address').getAttribute('maxlength'), '120')
    assert.equal(await page.getByTestId('cart-item').count(), 1)
  })
  await run('以餐點列的時間建立外送資料，成功後轉到訂單頁', {}, async ({ page, state, count, prepare, submit }) => {
    await prepare(); await submit()
    await page.waitForURL('**/buyer-orders.html*')
    assert.equal(count('/api/add_new_order'), 1)
    const delivery = state.calls.find(call => call.path === '/api/add_new_delivery')
    assert.equal(delivery.body.timestamp, timestamp)
    assert.equal(delivery.body.buyer_id, 2)
    assert.equal(delivery.body.pickup_address, '校門口測試交餐點')
    await page.goto(new URL('/', page.url()).href)
    assert.equal(await page.getByTestId('cart-item').count(), 0)
  })
  await run('同時間混入別筆餐點時停止補建外送資料', { extraRow: true }, async ({ page, count, prepare, submit }) => {
    await prepare(); await submit()
    await page.getByTestId('checkout-error').waitFor()
    assert.equal(count('/api/add_new_order'), 1)
    assert.equal(count('/api/add_new_delivery'), 0)
    await page.reload()
    await page.getByTestId('checkout-recover').click()
    await page.getByTestId('checkout-error').waitFor()
    assert.equal(count('/api/add_new_order'), 1)
    assert.equal(count('/api/add_new_delivery'), 0)
  })
  await run('地址已寫入但回應遺失，讀回後完成下單', { deliveryLost: true }, async ({ page, count, prepare, submit }) => {
    await prepare(); await submit()
    await page.waitForURL('**/buyer-orders.html*')
    assert.equal(count('/api/add_new_order'), 1)
    assert.equal(count('/api/add_new_delivery'), 1)
    assert(count('/api/view_delivery') >= 1)
  })
  await run('地址寫入失敗後重整，只重試外送資料', { deliveryFailure: true }, async ({ page, state, count, prepare, submit }) => {
    await prepare(); await submit()
    await page.getByTestId('checkout-recover').waitFor()
    await page.reload()
    await page.getByTestId('checkout-recover').waitFor()
    state.deliveryFailure = false
    await page.getByTestId('checkout-recover').click()
    await page.waitForURL('**/buyer-orders.html*')
    assert.equal(count('/api/add_new_order'), 1)
    assert.equal(count('/api/add_new_delivery'), 2)
  })
  for (const [name, options] of [
    ['建單回應遺失時保留待確認狀態，不再送出', { createLost: true }],
    ['建單回傳 500 時不假設沒有寫入', { create500: true }],
    ['建單回傳 HTML 404 時仍保留待確認紀錄', { createHtml404: true }],
  ]) await run(name, options, async ({ page, count, prepare, submit }) => {
    await prepare(); await submit()
    await page.getByTestId('checkout-unknown').waitFor()
    await page.reload()
    await page.getByTestId('checkout-unknown').waitFor()
    const button = page.getByTestId('checkout-submit')
    assert(await button.count() === 0 || !await button.isEnabled())
    assert.equal(count('/api/add_new_order'), 1)
    assert.equal(count('/api/add_new_delivery'), 0)
  })
  await run('送單期間連點不會再建立一張訂單', { createDelay: 400 }, async ({ page, count, prepare }) => {
    await prepare()
    await page.getByTestId('checkout-submit').dblclick()
    await page.waitForURL('**/buyer-orders.html*')
    assert.equal(count('/api/add_new_order'), 1)
    assert.equal(count('/api/add_new_delivery'), 1)
  })

  await run('同一買家的兩個分頁同時下單，只建立一張訂單', { createDelay: 700 }, async ({ page, context, count, prepare, origin }) => {
    await prepare()
    const other = await context.newPage()
    await other.goto(origin)
    await other.getByTestId('checkout-submit').waitFor()
    assert(await page.getByTestId('checkout-submit').isEnabled())
    assert(await other.getByTestId('checkout-submit').isEnabled())
    // Dispatch both clicks together before waiting for either response.
    await Promise.all([page, other].map(tab => tab.getByTestId('checkout-submit').dispatchEvent('click')))
    await eventually(() => count('/api/add_new_delivery') === 1, '兩頁下單未完成')
    await eventually(async () => [page.url(), other.url()].some(url => url.includes('/buyer-orders.html')), '成功下單的分頁未跳轉')
    assert.equal(count('/api/add_new_order'), 1)
    assert.equal(count('/api/add_new_delivery'), 1)
    await other.close()
  })

  for (const [label, entry] of [['買家', 'buyer-orders.html'], ['外送員', 'deliverer-orders.html']]) {
    const initial = () => ({
      rows: [{ buyer_id: 2, timestamp, order_id: 501, meal_id: 101, quantity: 1, note: null, order_state: 'processing' }],
      delivery: { buyer_id: 2, timestamp, pickup_address: '校門口測試交餐點', deliverer_id: 2 },
    })
    for (const failure of ['mealsReadFailure', 'deliveryReadFailure']) await run(
      `${label}頁${failure === 'mealsReadFailure' ? '餐點' : '外送資料'}讀取失敗時保留清單`, initial(), async ({ page, state, origin }) => {
        await page.goto(`${origin}/${entry}`)
        await page.getByTestId('order-card').waitFor()
        state[failure] = true
        await page.getByTestId('refresh-orders').click()
        await page.getByTestId('orders-error').waitFor()
        assert.equal(await page.getByTestId('order-card').count(), 1)
        assert(!await page.getByTestId('complete-order').isEnabled())
        assert.equal(await page.getByText('目前沒有訂單', { exact: true }).count(), 0)
        state[failure] = false
        if (failure === 'deliveryReadFailure') {
          state.deliveryHtml404 = true
          await page.getByTestId('retry-orders').click()
          await eventually(async () => await page.getByTestId('refresh-orders').isEnabled(), 'HTML 404 後未結束更新')
          assert.equal(await page.getByTestId('orders-error').count(), 1)
          assert.equal(await page.getByTestId('order-card').count(), 1)
          assert(!await page.getByTestId('complete-order').isEnabled())
          state.deliveryHtml404 = false
        }
        await page.getByTestId('retry-orders').click()
        await eventually(async () => await page.getByTestId('orders-error').count() === 0, '重試後錯誤未消失')
        assert(await page.getByTestId('complete-order').isEnabled())
      })
    await run(`${label}頁完成訂單只送出一次，重讀後顯示已完成`, { ...initial(), actionDelay: 400 }, async ({ page, count, origin }) => {
      page.on('dialog', dialog => dialog.accept())
      await page.goto(`${origin}/${entry}`)
      await page.getByTestId('complete-order').waitFor()
      await page.getByTestId('complete-order').dblclick()
      await eventually(async () => await page.getByTestId('order-card').getAttribute('data-order-state') === 'completed', '訂單未更新為已完成')
      assert.equal(count('/api/complete_order'), 1)
      assert.equal(await page.getByTestId('complete-order').count(), 0)
    })
  }

  await run('取消後保留原外送員，兩邊都能查看但不能再操作', {
    rows: [{ buyer_id: 2, timestamp, order_id: 501, meal_id: 101, quantity: 1, note: null, order_state: 'cancelled' }],
    delivery: { buyer_id: 2, timestamp, pickup_address: '校門口測試交餐點', deliverer_id: 2 },
  }, async ({ page, count, origin }) => {
    for (const entry of ['buyer-orders.html', 'deliverer-orders.html']) {
      await page.goto(`${origin}/${entry}`)
      await page.getByTestId('order-card').waitFor()
      assert.equal(await page.getByTestId('order-card').getAttribute('data-order-state'), 'cancelled')
      assert.equal(await page.getByTestId('orders-error').count(), 0)
      for (const action of ['cancel-order', 'complete-order', 'abandon-order']) {
        assert.equal(await page.getByTestId(action).count(), 0)
      }
    }
    assert.equal(count('/api/cancel_order') + count('/api/complete_order') + count('/api/abandon_order'), 0)
  })

  await run('待確認下單可由使用者查看紀錄後歸檔，不會重送或刪單', { createLost: true }, async ({ page, count, prepare, submit, menu, add }) => {
    await prepare(); await submit()
    await page.getByTestId('checkout-unknown').waitFor()
    assert(!await page.getByTestId('checkout-end-attempt').isEnabled())
    const popupPromise = page.waitForEvent('popup')
    await page.getByTestId('checkout-view-orders').click()
    const orderPage = await popupPromise
    await orderPage.getByTestId('order-card').waitFor()
    assert.equal(await orderPage.getByTestId('order-card').getAttribute('data-order-state'), 'pending')
    await orderPage.close()
    assert(await page.getByTestId('checkout-end-attempt').isEnabled())
    page.once('dialog', dialog => dialog.accept())
    await page.getByTestId('checkout-end-attempt').click()
    await eventually(async () => await page.getByTestId('checkout-unknown').count() === 0, '歸檔後仍停在待確認狀態')
    const history = await page.evaluate(() => {
      const key = Object.keys(localStorage).find(value => value.startsWith('ntou:checkout:v1:') && value.endsWith(':history'))
      return key ? JSON.parse(localStorage.getItem(key)) : null
    })
    assert.equal(history.entries.length, 1)
    assert.equal(history.entries[0].reason, 'user_ended_checkout')
    assert.equal(history.entries[0].serverOrderChanged, false)
    assert.equal(history.entries[0].attempt.phase, 'create_unknown')
    await menu(); await add()
    await page.getByTestId('checkout-address').fill('下一次下單的地址')
    assert(await page.getByTestId('checkout-submit').isEnabled())
    assert.equal(count('/api/add_new_order'), 1)
    assert.equal(count('/api/add_new_delivery'), 0)
    assert.equal(count('/api/cancel_order'), 0)
    assert.equal(count('/api/delete_order'), 0)
  })

  const report = { completed_at: new Date().toISOString(), frontend_origin: origin, mode: 'mock_api',
    note: '所有 API 都由測試攔截，不會建立或修改遠端資料。', passed: cases.every(item => item.passed),
    passed_count: cases.filter(item => item.passed).length, total: cases.length, cases }
  await writeFile(path.join(output, 'results.json'), JSON.stringify(report, null, 2) + '\n')
  return report
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const configPath = process.argv[2]
  if (!configPath) throw new Error('Usage: node checkout-contract.mjs CONFIG.json')
  const config = JSON.parse(await readFile(configPath, 'utf8'))
  const require = createRequire(import.meta.url)
  const { chromium } = require(path.join(path.resolve(config.playwrightModule), 'index.js'))
  const browser = await chromium.launch({ channel: config.channel || 'chrome', headless: true })
  try {
    const report = await runCheckoutContract({ browser, ...config })
    console.log(JSON.stringify({ passed: report.passed, count: `${report.passed_count}/${report.total}` }))
    if (!report.passed) process.exitCode = 1
  } finally { await browser.close() }
}
