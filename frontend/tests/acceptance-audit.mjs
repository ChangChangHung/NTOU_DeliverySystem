import { mkdir } from 'node:fs/promises'
import path from 'node:path'

/** Browser response injections only. Every API request is intercepted in a fresh context. */
export async function runAudit({ browser, frontendUrls, seed, outputDir }) {
  const assert = (condition, message) => { if (!condition) throw new Error(message) }
  const frontendUrl = frontendUrls[0]
  assert(['127.0.0.1', 'localhost'].includes(new URL(frontendUrl).hostname), 'Audit requires localhost')
  const courier = seed.demo_courier_ids[0]
  const otherCourier = seed.demo_courier_ids[1]
  assert(Number.isSafeInteger(courier) && courier > 0 && courier !== otherCourier, 'Need two distinct demo couriers')
  const output = path.join(outputDir, 'audit')
  await mkdir(output, { recursive: true })
  const cases = [], pageErrors = [], unexpectedWrites = []
  const stamp = { buyer_id: 101, timestamp: '2026-11-15 22:00:01' }
  const differentTime = '2026-11-15 22:00:02'
  const catalog = [
    { meal_id: 1, meal_name: '稽核餐點 A', restaurant_id: 1, price: 80 },
    { meal_id: 2, meal_name: '稽核餐點 B', restaurant_id: 1, price: 100 },
  ]
  const row = (mealId, quantity, state) => ({ ...stamp, order_id: mealId + 1000,
    meal_id: mealId, quantity, order_state: state, note: null })
  const ready = async page => page.waitForFunction(() => [...document.querySelectorAll('button')]
    .some(button => button.textContent.trim() === '重新整理' && !button.disabled), null, { timeout: 25000 })

  async function run(name, configuration, verify) {
    const started = Date.now()
    const context = await browser.newContext({ viewport: configuration.viewport ?? { width: 1100, height: 850 } })
    const page = await context.newPage()
    const requests = []
    let mealReads = 0, deliveryReads = 0
    page.on('pageerror', error => pageErrors.push({ case: name, message: error.message }))
    try {
      await page.route('**/api/**', async route => {
        const request = route.request()
        const endpoint = new URL(request.url()).pathname
        requests.push({ endpoint, method: request.method() })
        const send = (body, status = 200) => route.fulfill({ status,
          contentType: 'application/json', body: JSON.stringify(body) })
        if (endpoint === '/api/view_all_meals') return send(configuration.catalog ?? catalog)
        if (endpoint === '/api/view_all_restaurants') return send([
          { restaurant_id: 1, restaurant_name: '稽核餐廳', restaurant_address: '合成地址' },
        ])
        if (endpoint === '/api/view_order_by_stamp') return send([stamp])
        if (endpoint === '/api/view_meals_of_order') {
          mealReads++
          const sequence = configuration.reads ?? [{ state: 'pending', owner: null }]
          const current = sequence[Math.min(mealReads - 1, sequence.length - 1)]
          const meals = configuration.meals ?? [row(1, 1, current.state), row(2, 2, current.state)]
            .map(item => ({ ...item, note: configuration.note ?? item.note }))
          return send(meals)
        }
        if (endpoint === '/api/view_delivery') {
          deliveryReads++
          const sequence = configuration.reads ?? [{ state: 'pending', owner: null }]
          const current = sequence[Math.min(mealReads - 1, sequence.length - 1)]
          return send([{ ...stamp, pickup_address: '稽核交付點', deliverer_id: current.owner,
            ...configuration.delivery }])
        }
        unexpectedWrites.push({ case: name, endpoint, method: request.method(), body: request.postData() })
        return send({ success: false, message: '稽核已攔截未預期的 API 呼叫' }, 500)
      })
      await page.goto(frontendUrl)
      await ready(page)
      const details = await verify(page, () => ({ mealReads, deliveryReads }))
      assert(!requests.some(request => request.endpoint === '/api/process_order'), 'Audit automatically sent an acceptance POST')
      cases.push({ name, mode: 'browser response injection; no backend API requests forwarded',
        passed: true, duration_ms: Date.now() - started, ...details, meal_reads: mealReads,
        delivery_reads: deliveryReads, acceptance_posts: 0 })
    } catch (error) {
      cases.push({ name, mode: 'browser response injection; no backend API requests forwarded',
        passed: false, duration_ms: Date.now() - started, error: error.message,
        meal_reads: mealReads, delivery_reads: deliveryReads })
      await page.screenshot({ path: path.join(output, `${name}.png`), fullPage: true }).catch(() => {})
    } finally {
      await context.close()
    }
  }

  async function rejects(page, message) {
    const text = await page.getByRole('alert').innerText()
    assert(text.includes(message), `Unexpected error: ${text}`)
    assert(await page.locator('.order').count() === 0, 'Bad response left an actionable pending card')
    assert(await page.getByTestId('accepted-order').count() === 0, 'Bad response appeared as an accepted order')
    assert(await page.getByText('目前沒有待接訂單', { exact: true }).count() === 0, 'Bad data was shown as an empty queue')
    assert(await page.getByText('目前沒有已接訂單', { exact: true }).count() === 0, 'Bad data was shown as no accepted orders')
    assert(await page.getByRole('button', { name: '重新整理', exact: true }).isEnabled(), 'Cannot recover by refreshing')
    return { error: text, bad_data_hidden: true }
  }

  await run('audit-meal-buyer-identity', {
    meals: [{ ...row(1, 1, 'pending'), buyer_id: 202 }],
  }, page => rejects(page, '餐點資料與查詢的訂單不一致'))
  await run('audit-meal-timestamp-identity', {
    meals: [{ ...row(1, 1, 'pending'), timestamp: differentTime }],
  }, page => rejects(page, '餐點資料與查詢的訂單不一致'))
  await run('audit-delivery-buyer-identity', {
    reads: [{ state: 'processing', owner: courier }], delivery: { buyer_id: 202 },
  }, page => rejects(page, '送餐資料與查詢的訂單不一致'))
  await run('audit-delivery-timestamp-identity', {
    reads: [{ state: 'processing', owner: courier }], delivery: { timestamp: differentTime },
  }, page => rejects(page, '送餐資料與查詢的訂單不一致'))

  await run('audit-recheck-after-acceptance', {
    reads: [{ state: 'pending', owner: courier }, { state: 'processing', owner: courier }],
  }, async (page, counts) => {
    assert(await page.getByRole('alert').count() === 0, 'Recovered assignment still has an error')
    assert(await page.locator('.order').count() === 0, 'Accepted order is still pending')
    assert(await page.getByTestId('accepted-order').count() === 1, 'Rechecked acceptance is missing')
    assert(counts().mealReads === 2 && counts().deliveryReads === 2, 'Expected exactly one pair of rechecks')
    return { recovered_as: 'own accepted order' }
  })
  await run('audit-recheck-after-abandonment', {
    reads: [{ state: 'processing', owner: null }, { state: 'pending', owner: null }],
  }, async (page, counts) => {
    assert(await page.getByRole('alert').count() === 0, 'Recovered pending order has an error')
    assert(await page.locator('.order').count() === 1, 'Rechecked pending order is missing')
    assert(await page.getByTestId('accepted-order').count() === 0, 'Abandoned order still appears as accepted')
    assert(counts().mealReads === 2 && counts().deliveryReads === 2, 'Expected exactly one pair of rechecks')
    return { recovered_as: 'pending order' }
  })
  for (const [name, state, owner] of [
    ['audit-persistent-pending-assigned', 'pending', courier],
    ['audit-persistent-processing-unassigned', 'processing', null],
  ]) {
    await run(name, { reads: [{ state, owner }] }, async (page, counts) => {
      const result = await rejects(page, '訂單狀態與外送員資料尚未一致')
      assert(counts().mealReads === 2 && counts().deliveryReads === 2, 'Recheck is missing or unbounded')
      return result
    })
  }
  await run('audit-unknown-order-state', {
    reads: [{ state: 'processng', owner: null }],
  }, page => rejects(page, '訂單餐點資料格式不正確'))
  await run('audit-unsafe-subtotal', {
    catalog: [{ ...catalog[0], price: Number.MAX_SAFE_INTEGER }, catalog[1]],
    meals: [row(1, 2, 'pending')],
  }, page => rejects(page, '訂單金額超出可正確計算的範圍'))
  await run('audit-unsafe-total', {
    catalog: [{ ...catalog[0], price: Number.MAX_SAFE_INTEGER }, { ...catalog[1], price: 1 }],
    meals: [row(1, 1, 'pending'), row(2, 1, 'pending')],
  }, page => rejects(page, '訂單金額超出可正確計算的範圍'))
  await run('audit-refresh-lifecycle-and-owner', {
    reads: [{ state: 'processing', owner: courier }, { state: 'cancelled', owner: courier },
      { state: 'pending', owner: null }, { state: 'processing', owner: otherCourier }],
  }, async page => {
    assert(await page.getByTestId('accepted-order').count() === 1, 'Initial own order is missing')
    for (const [pending, own] of [[0, 0], [1, 0], [0, 0]]) {
      await page.getByRole('button', { name: '重新整理', exact: true }).click()
      await ready(page)
      assert(await page.getByRole('alert').count() === 0, 'Valid lifecycle state caused an error')
      assert(await page.locator('.order').count() === pending, 'Pending list did not follow the latest response')
      assert(await page.getByTestId('accepted-order').count() === own, 'Own list kept a cancelled or another courier order')
    }
    return { transitions: ['own-processing', 'cancelled', 'pending', 'other-courier-processing'] }
  })
  await run('audit-mobile-long-content', {
    viewport: { width: 390, height: 844 },
    catalog: [{ ...catalog[0], meal_name: 'A'.repeat(40) }, catalog[1]],
    note: 'N'.repeat(100), delivery: { pickup_address: 'B'.repeat(100) },
    reads: [{ state: 'pending', owner: null }, { state: 'processing', owner: courier }],
  }, async page => {
    const dimensions = async () => page.evaluate(() => ({
      viewport: innerWidth, document: document.documentElement.scrollWidth, body: document.body.scrollWidth,
    }))
    const checkWidth = async () => {
      const result = await dimensions()
      assert(result.document <= result.viewport && result.body <= result.viewport,
        `Long text overflow: ${JSON.stringify(result)}`)
      return result
    }
    assert(await page.locator('.order').count() === 1, 'Need a pending card for the mobile check')
    const button = await page.locator('.order button.accept').boundingBox()
    assert(button && button.x >= 0 && button.x + button.width <= 390, 'Mobile accept button is outside the screen')
    const pending = await checkWidth()
    await page.screenshot({ path: path.join(output, 'mobile-long-pending.png'), fullPage: true })
    await page.getByRole('button', { name: '重新整理', exact: true }).click()
    await ready(page)
    assert(await page.getByTestId('accepted-order').count() === 1, 'Need an accepted card for the mobile check')
    const accepted = await checkWidth()
    await page.screenshot({ path: path.join(output, 'mobile-long-accepted.png'), fullPage: true })
    await page.setViewportSize({ width: 1100, height: 850 })
    const desktop = await checkWidth()
    await page.screenshot({ path: path.join(output, 'desktop-long-accepted.png'), fullPage: true })
    return { pending, accepted, desktop }
  })

  return {
    schema: 'ntou.acceptance.frontend-audit.v1', completed_at: new Date().toISOString(),
    passed: cases.length === 13 && cases.every(item => item.passed) && pageErrors.length === 0 && unexpectedWrites.length === 0,
    total: cases.length, passed_cases: cases.filter(item => item.passed).length,
    cases, page_errors: pageErrors, unexpected_writes: unexpectedWrites, backend_api_requests_forwarded: 0,
    limits: ['All audit API responses are injected in isolated browser contexts; no database writes or real transition races are claimed.'],
  }
}

export default runAudit
