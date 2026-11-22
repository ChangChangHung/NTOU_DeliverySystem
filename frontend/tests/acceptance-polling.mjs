import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

const INTERVAL_MS = 15000
const API = {
  catalog: '/api/view_all_meals', restaurants: '/api/view_all_restaurants',
  stamps: '/api/view_order_by_stamp', meals: '/api/view_meals_of_order',
  delivery: '/api/view_delivery', accept: '/api/process_order',
}
const assert = (condition, message) => { if (!condition) throw new Error(message) }
const delay = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds))

// These waits run in Node, so they do not depend on the paused browser clock.
async function eventually(check, message, timeout = 10000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    if (await check()) return
    await delay(10)
  }
  throw new Error(message)
}

/**
 * Synthetic browser tests only: every /api/ request is intercepted, never forwarded.
 * The production 15-second interval stays unchanged; Playwright advances browser time.
 */
export async function runPolling({ browser, frontendUrl, outputDir, courierId = 2 }) {
  const url = new URL(frontendUrl)
  assert(['127.0.0.1', 'localhost'].includes(url.hostname), 'Polling tests require localhost')
  assert(!url.username && !url.password, 'Frontend URL must not contain credentials')
  assert(Number.isSafeInteger(courierId) && courierId > 0, 'Need a positive synthetic courier ID')
  const output = path.join(outputDir, 'polling')
  await mkdir(output, { recursive: true })
  const cases = [], pageErrors = [], unexpectedRequests = []
  const otherCourier = courierId + 1
  const order = (buyer, state = 'pending', owner = null) => ({
    buyer_id: buyer, timestamp: `2026-11-23 12:00:${String(buyer % 60).padStart(2, '0')}`,
    state, owner,
  })
  const pendingA = order(101), pendingB = order(102)
  const ownA = order(103, 'processing', courierId)
  const ownB = order(104, 'processing', courierId)
  const another = order(105, 'processing', otherCourier)
  const catalog = [{ meal_id: 1, meal_name: '自動更新測試餐點', restaurant_id: 1, price: 80 }]
  const restaurants = [{ restaurant_id: 1, restaurant_name: '合成測試餐廳', restaurant_address: '合成地址' }]
  const stamp = item => ({ buyer_id: item.buyer_id, timestamp: item.timestamp })
  const match = (item, value) => String(item.buyer_id) === String(value.buyer_id)
    && item.timestamp === value.timestamp

  async function run(name, configuration, verify) {
    const started = Date.now()
    const context = await browser.newContext({ viewport: { width: 1100, height: 850 }, serviceWorkers: 'block' })
    const page = await context.newPage()
    const requests = [], holds = [], rounds = []
    let readRounds = 0, acceptancePosts = 0, acceptedState
    page.on('pageerror', error => pageErrors.push({ case: name, message: error.message }))
    const current = () => acceptedState ?? configuration.rounds?.[Math.min(readRounds - 1, configuration.rounds.length - 1)]
      ?? [pendingA, ownA]
    const counts = () => ({ read_rounds: readRounds, acceptance_posts: acceptancePosts, api_requests: requests.length })

    try {
      assert(page.clock && typeof page.clock.install === 'function'
        && typeof page.clock.fastForward === 'function', 'Installed Playwright needs page.clock support')
      const epoch = Date.parse('2026-11-23T04:00:00Z')
      await page.clock.install({ time: new Date(epoch) })
      await page.clock.pauseAt(new Date(epoch + 1000))
      await page.addInitScript(() => {
        const originalFetch = window.fetch.bind(window)
        window.__pollingProbe = { requests: [], hidden: false }
        // Headless Chrome has no tab UI; visibility events use the real listener path
        // with a controlled visibilityState. Offline tests use context.setOffline().
        Object.defineProperty(document, 'visibilityState', {
          configurable: true, get: () => window.__pollingProbe.hidden ? 'hidden' : 'visible',
        })
        Object.defineProperty(document, 'hidden', {
          configurable: true, get: () => window.__pollingProbe.hidden,
        })
        window.fetch = async (input, options) => {
          const endpoint = new URL(typeof input === 'string' ? input : input.url, location.href).pathname
          if (!endpoint.startsWith('/api/')) return originalFetch(input, options)
          const entry = { endpoint, aborted: false, settled: false }
          window.__pollingProbe.requests.push(entry)
          const abort = () => { entry.aborted = true }
          if (options?.signal?.aborted) abort()
          options?.signal?.addEventListener('abort', abort, { once: true })
          try { return await originalFetch(input, options) } finally {
            entry.settled = true
            options?.signal?.removeEventListener('abort', abort)
          }
        }
      })
      await context.route('**/api/**', async route => {
        const request = route.request()
        const endpoint = new URL(request.url()).pathname
        const method = request.method()
        if (endpoint === API.catalog) {
          readRounds++
          rounds.push(structuredClone(current()))
        }
        const round = readRounds
        requests.push({ endpoint, method, round })
        const expectedMethod = [API.meals, API.delivery, API.accept].includes(endpoint) ? 'POST' : 'GET'
        const send = async (body, status = 200) => {
          // An intentionally aborted fetch may already have lost its route.
          await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) }).catch(() => {})
        }
        if (!Object.values(API).includes(endpoint) || method !== expectedMethod) {
          unexpectedRequests.push({ case: name, endpoint, method })
          return send({ success: false, message: '測試攔截未預期的 API 呼叫' }, 500)
        }
        if (endpoint === API.accept) acceptancePosts++
        const hold = configuration.holds?.find(item => !item.used && item.endpoint === endpoint && item.round === round)
        if (hold) {
          hold.used = true
          let release
          const gate = new Promise(resolve => { release = resolve })
          holds.push({ endpoint, round, release, released: false })
          await gate
        }
        if (configuration.failRounds?.includes(round) && endpoint === API.catalog) {
          return send({ message: '合成暫時錯誤' }, 503)
        }
        if (endpoint === API.catalog) return send(catalog)
        if (endpoint === API.restaurants) return send(restaurants)
        const snapshot = rounds[round - 1] ?? []
        if (endpoint === API.stamps) return send(snapshot.map(stamp))
        const body = request.postDataJSON()
        if (endpoint === API.accept) {
          const target = snapshot.find(item => match(item, body))
          if (!target || body.deliverer_id !== courierId) {
            unexpectedRequests.push({ case: name, endpoint, method, invalid_synthetic_identity: true })
            return send({ message: '測試接單識別不符' }, 400)
          }
          if (configuration.acceptStatus === 409) {
            acceptedState = snapshot.map(item => match(item, target)
              ? { ...item, state: 'processing', owner: otherCourier } : item)
            return send({ success: false, message: '此訂單已被其他外送員接走' }, 409)
          }
          // A server may commit before its response is lost; prove this never retries POST.
          acceptedState = snapshot.map(item => match(item, target)
            ? { ...item, state: 'processing', owner: courierId } : item)
          if (configuration.acceptStatus === 500) return send({ message: '合成未知接單結果' }, 500)
          return send({ success: true, ...stamp(target), deliverer_id: courierId })
        }
        const target = snapshot.find(item => match(item, body))
        if (!target) {
          unexpectedRequests.push({ case: name, endpoint, method, invalid_synthetic_identity: true })
          return send({ message: '測試查詢識別不符' }, 404)
        }
        if (endpoint === API.meals) return send([{ ...stamp(target), meal_id: 1, quantity: 1,
          note: null, order_state: target.state }])
        return send([{ ...stamp(target), pickup_address: `合成送餐點 ${target.buyer_id}`, deliverer_id: target.owner }])
      })

      const ready = async () => eventually(async () => await page.getByRole('button', { name: '重新整理', exact: true })
        .isEnabled().catch(() => false), 'Lobby did not finish its read')
      const waitRounds = async expected => eventually(() => readRounds === expected, `Expected ${expected} read rounds; got ${readRounds}`)
      const waitHold = async (endpoint, round) => eventually(() => holds.some(item => item.endpoint === endpoint && item.round === round),
        `Missing held request in round ${round}`)
      const release = (endpoint, round) => {
        const item = holds.find(item => item.endpoint === endpoint && item.round === round && !item.released)
        assert(item, `No held request to release in round ${round}`)
        item.released = true
        item.release()
      }
      const tick = async milliseconds => {
        await page.clock.fastForward(milliseconds)
        // Drain browser microtasks and give intercepted requests one Node event turn.
        await page.evaluate(() => Promise.resolve())
        await delay(30)
      }
      const cards = async (pending, owned) => {
        const actual = await page.evaluate(() => ({
          pending: [...document.querySelectorAll('.order .order-title strong')].map(item => item.textContent.trim()),
          owned: [...document.querySelectorAll('[data-testid="accepted-order"] .order-title strong')].map(item => item.textContent.trim()),
        }))
        const labels = values => values.map(value => `買家 #${value}`).sort()
        assert(JSON.stringify(actual.pending.sort()) === JSON.stringify(labels(pending)), 'Pending cards did not match the latest snapshot')
        assert(JSON.stringify(actual.owned.sort()) === JSON.stringify(labels(owned)), 'Own accepted cards did not match the latest snapshot')
      }
      const manual = async () => page.getByRole('button', { name: '重新整理', exact: true }).click({ force: true })
      const accept = async () => page.locator('.order button.accept').first().evaluate(button => {
        // Same-turn repeated clicks exercise the handler guard before Vue patches disabled.
        button.click()
        button.click()
      })
      const hidden = async value => page.evaluate(value => {
        window.__pollingProbe.hidden = value
        document.dispatchEvent(new Event('visibilitychange'))
      }, value)
      const unmount = async () => page.evaluate(() => {
        const app = document.getElementById('app').__vue_app__
        if (!app) throw new Error('Need the local Vue app to test component unmount')
        app.unmount()
      })
      const aborted = async endpoint => eventually(() => page.evaluate(endpoint => window.__pollingProbe.requests
        .some(item => item.endpoint === endpoint && item.aborted), endpoint), `Unmount or acceptance did not abort ${endpoint}`)
      await page.goto(frontendUrl)
      const details = await verify({ page, context, ready, waitRounds, waitHold, release, tick, cards,
        manual, accept, hidden, unmount, aborted, counts })
      cases.push({ name, passed: true, duration_ms: Date.now() - started, ...counts(), ...details })
    } catch (error) {
      cases.push({ name, passed: false, duration_ms: Date.now() - started, ...counts(), error: error.message })
      await page.screenshot({ path: path.join(output, `${name}.png`), fullPage: true, timeout: 5000 }).catch(() => {})
    } finally {
      for (const item of holds) item.release()
      await context.close()
    }
  }

  await run('initial-and-multiple-refreshes', {
    holds: [{ endpoint: API.catalog, round: 1 }],
    rounds: [[pendingA, ownA, another], [pendingA, pendingB, ownA, another], [ownB, another]],
  }, async t => {
    await t.waitHold(API.catalog, 1)
    assert(await t.page.getByText('載入中……', { exact: true }).isVisible(), 'Initial loading was not visible')
    assert(await t.page.locator('.order button.accept:enabled').count() === 0, 'Initial load allowed acceptance')
    t.release(API.catalog, 1)
    await t.ready()
    await t.cards([101], [103])
    await t.tick(INTERVAL_MS - 1)
    assert(t.counts().read_rounds === 1, 'Automatic read started before 15 seconds')
    await t.tick(1)
    await t.waitRounds(2)
    await t.ready()
    await t.cards([101, 102], [103])
    await t.tick(INTERVAL_MS)
    await t.waitRounds(3)
    await t.ready()
    await t.cards([], [104])
    assert(t.counts().acceptance_posts === 0, 'Polling submitted an acceptance')
    return { interval_boundary_ms: INTERVAL_MS, added_and_removed_pending: true, replaced_own_orders: true, other_courier_filtered: true }
  })

  await run('slow-background-keeps-cards-and-serializes', {
    holds: [{ endpoint: API.catalog, round: 2 }, { endpoint: API.restaurants, round: 2 }],
  }, async t => {
    await t.ready()
    await t.tick(INTERVAL_MS)
    await t.waitHold(API.catalog, 2)
    await t.cards([101], [103])
    assert(await t.page.locator('.order button.accept').isEnabled(), 'Background refresh disabled a valid acceptance')
    assert((await t.page.getByTestId('refresh-status').innerText()).includes('正在更新'), 'Background refresh status is missing')
    await t.tick(9000)
    t.release(API.catalog, 2)
    await t.waitHold(API.restaurants, 2)
    await t.tick(9000)
    assert(t.counts().read_rounds === 2, 'Slow read overlapped a new timer round')
    await t.cards([101], [103])
    t.release(API.restaurants, 2)
    await t.ready()
    await t.tick(INTERVAL_MS - 1)
    assert(t.counts().read_rounds === 2, 'Next interval did not start at read completion')
    await t.tick(1)
    await t.waitRounds(3)
    await t.ready()
    return { simulated_read_duration_ms: 18000, cards_retained: true, no_overlapping_rounds: true }
  })

  await run('manual-refresh-resets-next-interval', {}, async t => {
    await t.ready()
    await t.tick(7000)
    await t.manual()
    await t.waitRounds(2)
    await t.ready()
    await t.tick(INTERVAL_MS - 1)
    assert(t.counts().read_rounds === 2, 'Old timer survived manual refresh')
    await t.tick(1)
    await t.waitRounds(3)
    await t.ready()
    return { manual_refresh_worked: true, previous_timer_cleared: true }
  })

  await run('accept-interrupts-background-and-posts-once', {
    holds: [{ endpoint: API.catalog, round: 2 }, { endpoint: API.accept, round: 2 }],
  }, async t => {
    await t.ready()
    await t.tick(INTERVAL_MS)
    await t.waitHold(API.catalog, 2)
    await t.accept()
    await t.waitHold(API.accept, 2)
    await t.aborted(API.catalog)
    assert(t.counts().acceptance_posts === 1, 'Repeated click submitted more than one POST')
    await t.tick(14000)
    assert(t.counts().read_rounds === 2, 'Timer read overlapped acceptance')
    t.release(API.accept, 2)
    await t.waitRounds(3)
    await t.ready()
    await t.cards([], [101, 103])
    t.release(API.catalog, 2)
    await t.tick(1)
    await t.cards([], [101, 103])
    await t.tick(INTERVAL_MS - 1)
    await t.waitRounds(4)
    await t.ready()
    assert(t.counts().acceptance_posts === 1, 'Automatic refresh repeated the acceptance POST')
    await t.page.screenshot({ path: path.join(output, 'accepted-after-background-race.png'), fullPage: true })
    return { stale_read_aborted: true, post_count: 1, immediate_post_refresh: true, stale_result_ignored: true }
  })

  for (const [name, status, notice, own] of [
    ['accept-conflict-refreshes-without-retry', 409, '已被其他外送員接走', [103]],
    ['accept-unknown-refreshes-without-retry', 500, '尚無法確認接單結果', [101, 103]],
  ]) {
    await run(name, { acceptStatus: status }, async t => {
      await t.ready()
      await t.accept()
      await t.waitRounds(2)
      await t.ready()
      assert((await t.page.getByRole('status').innerText()).includes(notice), 'Acceptance outcome notice is missing')
      await t.cards([], own)
      await t.tick(INTERVAL_MS)
      await t.waitRounds(3)
      await t.ready()
      assert(t.counts().acceptance_posts === 1, 'Acceptance error was automatically retried')
      return { response_status: status, post_count: 1, outcome_refreshed: true }
    })
  }

  await run('background-error-retains-cards-and-recovers', {
    failRounds: [2], holds: [{ endpoint: API.catalog, round: 3 }],
  }, async t => {
    await t.ready()
    await t.tick(INTERVAL_MS)
    await t.waitRounds(2)
    await t.ready()
    await t.cards([101], [103])
    assert(await t.page.getByRole('alert').isVisible(), 'Background failure was not shown')
    assert(await t.page.locator('.order button.accept').isDisabled(), 'Stale failed data allowed acceptance')
    await t.accept()
    assert(t.counts().acceptance_posts === 0, 'Disabled stale card sent a POST')
    await t.tick(INTERVAL_MS)
    await t.waitHold(API.catalog, 3)
    assert(await t.page.getByRole('alert').isVisible(), 'Error disappeared before recovery succeeded')
    assert(await t.page.locator('.order button.accept').isDisabled(), 'Recovery in progress allowed acceptance')
    t.release(API.catalog, 3)
    await t.ready()
    assert(await t.page.getByRole('alert').count() === 0, 'Error did not clear after recovery')
    assert(await t.page.locator('.order button.accept').isEnabled(), 'Recovery did not re-enable acceptance')
    return { stale_cards_retained: true, acceptance_blocked_until_success: true, automatic_recovery: true }
  })

  await run('initial-error-recovers-automatically', { failRounds: [1] }, async t => {
    await t.ready()
    assert(await t.page.getByRole('alert').isVisible(), 'Initial failure was not shown')
    assert(await t.page.locator('.order button.accept:enabled').count() === 0, 'Initial failure allowed acceptance')
    await t.tick(INTERVAL_MS)
    await t.waitRounds(2)
    await t.ready()
    await t.cards([101], [103])
    assert(await t.page.getByRole('alert').count() === 0, 'Initial failure did not recover')
    return { automatic_recovery: true }
  })

  await run('manual-refresh-recovers-background-error', { failRounds: [2] }, async t => {
    await t.ready()
    await t.tick(INTERVAL_MS)
    await t.waitRounds(2)
    await t.ready()
    assert(await t.page.getByRole('alert').isVisible(), 'Expected synthetic failure')
    await t.manual()
    await t.waitRounds(3)
    await t.ready()
    assert(await t.page.getByRole('alert').count() === 0, 'Manual recovery left an error')
    await t.cards([101], [103])
    return { manual_recovery: true }
  })

  await run('hidden-and-offline-pause-until-visible-online', {}, async t => {
    await t.ready()
    await t.hidden(true)
    await t.tick(60000)
    assert(t.counts().read_rounds === 1, 'Hidden page kept polling')
    await t.context.setOffline(true)
    await t.hidden(false)
    await t.tick(60000)
    assert(t.counts().read_rounds === 1, 'Visible offline page resumed polling')
    assert(await t.page.locator('.order button.accept').isDisabled(), 'Offline page allowed acceptance')
    await t.context.setOffline(false)
    await t.waitRounds(2)
    await t.ready()
    await t.hidden(true)
    await t.hidden(false)
    await t.waitRounds(3)
    await t.ready()
    return { hidden_pause_ms: 60000, offline_pause_ms: 60000, immediate_visible_resume: true, immediate_online_resume: true }
  })

  await run('offline-during-read-prevents-next-round', {
    holds: [{ endpoint: API.catalog, round: 2 }],
  }, async t => {
    await t.ready()
    await t.tick(INTERVAL_MS)
    await t.waitHold(API.catalog, 2)
    await t.context.setOffline(true)
    t.release(API.catalog, 2)
    await t.ready()
    await t.tick(60000)
    assert(t.counts().read_rounds === 2, 'A read finishing offline scheduled another round')
    await t.context.setOffline(false)
    await t.waitRounds(3)
    await t.ready()
    return { current_read_finished: true, next_round_paused: true, online_recovered: true }
  })

  await run('unmount-clears-idle-timer-and-listeners', {}, async t => {
    await t.ready()
    await t.unmount()
    await t.tick(60000)
    await t.hidden(true)
    await t.hidden(false)
    await t.context.setOffline(true)
    await t.context.setOffline(false)
    await t.tick(60000)
    assert(t.counts().read_rounds === 1, 'Unmounted page restarted API reads')
    return { no_requests_after_unmount: true, visibility_and_online_listeners_inactive: true }
  })

  await run('unmount-aborts-active-read', {
    holds: [{ endpoint: API.catalog, round: 2 }],
  }, async t => {
    await t.ready()
    await t.tick(INTERVAL_MS)
    await t.waitHold(API.catalog, 2)
    const requestsBefore = t.counts().api_requests
    await t.unmount()
    await t.aborted(API.catalog)
    t.release(API.catalog, 2)
    await t.tick(60000)
    assert(t.counts().api_requests === requestsBefore, 'Unmounted read continued its API chain')
    return { active_read_aborted: true, no_requests_after_unmount: true }
  })

  await run('unmount-aborts-acceptance-without-followup', {
    holds: [{ endpoint: API.accept, round: 1 }],
  }, async t => {
    await t.ready()
    await t.accept()
    await t.waitHold(API.accept, 1)
    const requestsBefore = t.counts().api_requests
    await t.unmount()
    await t.aborted(API.accept)
    t.release(API.accept, 1)
    await t.tick(60000)
    assert(t.counts().api_requests === requestsBefore, 'Unmounted acceptance started a follow-up read')
    assert(t.counts().acceptance_posts === 1, 'Unmounted acceptance retried its POST')
    return { active_acceptance_aborted: true, no_requests_after_unmount: true, post_count: 1 }
  })

  return {
    schema: 'ntou.acceptance.polling-browser.v1', completed_at: new Date().toISOString(),
    passed: cases.every(item => item.passed) && pageErrors.length === 0 && unexpectedRequests.length === 0,
    total: cases.length, passed_cases: cases.filter(item => item.passed).length,
    cases, page_errors: pageErrors, unexpected_requests: unexpectedRequests,
    refresh_interval_ms: INTERVAL_MS, backend_api_requests_forwarded: 0,
    limits: [
      'All API responses are synthetic in isolated browser contexts; no database or real backend race is claimed.',
      'Browser time is advanced with Playwright clock while the component interval remains 15000 ms.',
      'Visibility uses controlled document visibility properties plus real visibilitychange listeners; offline uses Chrome context.setOffline.',
      'Unmount invokes the local Vue app lifecycle through its development mount handle.',
    ],
  }
}

export default runPolling

// node frontend/tests/acceptance-polling.mjs /absolute/path/to/input.json
// Input: { frontendUrl, outputDir, courierId, playwrightModule, channel?: "chrome" }
if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  assert(process.argv[2], 'Supply a polling test input JSON path')
  const input = JSON.parse(await readFile(process.argv[2], 'utf8'))
  const require = createRequire(import.meta.url)
  const { chromium } = require(path.join(input.playwrightModule, 'index.js'))
  let browser
  for (const signal of ['SIGTERM', 'SIGINT']) {
    process.once(signal, async () => {
      await browser?.close().catch(() => {})
      process.exit(1)
    })
  }
  try {
    browser = await chromium.launch({ channel: input.channel ?? 'chrome', headless: true })
    const report = await runPolling({ browser, ...input })
    report.browser = browser.version()
    await writeFile(path.join(input.outputDir, 'polling-results.json'), JSON.stringify(report, null, 2) + '\n', { flag: 'wx' })
    console.log(JSON.stringify({ passed: report.passed, total: report.total, passed_cases: report.passed_cases,
      failed_cases: report.cases.filter(item => !item.passed).map(item => ({ name: item.name, error: item.error })),
      page_errors: report.page_errors, unexpected_requests: report.unexpected_requests,
      backend_api_requests_forwarded: report.backend_api_requests_forwarded }))
    process.exitCode = report.passed ? 0 : 1
  } finally {
    await browser?.close()
  }
}
