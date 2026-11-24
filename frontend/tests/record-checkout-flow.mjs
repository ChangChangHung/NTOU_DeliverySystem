import { mkdir, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import path from 'node:path'

// The localhost gateway must allow only the dedicated test accounts and two new orders.
const [outArg, moduleArg, mode] = process.argv.slice(2)
if (!outArg || !moduleArg || !['--rehearse', '--record'].includes(mode)) throw Error('Usage: node record-checkout-flow.mjs OUTPUT PLAYWRIGHT_MODULE --rehearse|--record')
const out = path.resolve(outArg), rehearsal = mode === '--rehearse'
await mkdir(out, { recursive: true })
const { chromium } = createRequire(import.meta.url)(path.join(path.resolve(moduleArg), 'index.js'))
const fixtureResponse = await fetch('http://127.0.0.1:8770/fixture')
const fixture = await fixtureResponse.json()
if (!fixture.run || !fixture.accounts?.buyer || fixture.orders.length) throw Error('A fresh, bounded test fixture is required')
if (rehearsal && new URL(fixture.base).hostname !== '127.0.0.1') throw Error('Rehearsal must use localhost')
const catalog = await (await fetch('http://127.0.0.1:8770/api/view_all_meals')).json()
const restaurantId = catalog.find(meal => catalog.filter(other=>other.restaurant_id===meal.restaurant_id).length >= 2)?.restaurant_id
if (!restaurantId) throw Error('The demonstration requires a restaurant with two meals')
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const context = await browser.newContext({ viewport: { width: 1920, height: 1080 },
  ...(rehearsal ? {} : { recordVideo: { dir: out, size: { width: 1920, height: 1080 } } }) })
const page = await context.newPage()
page.setDefaultTimeout(45000)
page.on('dialog', dialog => dialog.accept())
const started = Date.now(), events = [], responses = [], errors = [], orders = []
const seconds = () => +((Date.now() - started) / 1000).toFixed(2)
const pause = ms => page.waitForTimeout(rehearsal ? Math.min(ms, 250) : ms)
page.on('pageerror', e => errors.push(e.message))
page.on('response', async response => {
  const endpoint = new URL(response.url()).pathname
  if (!['/api/add_new_order','/api/add_new_delivery','/api/process_order','/api/complete_order','/api/abandon_order'].includes(endpoint)) return
  responses.push({ seconds: seconds(), endpoint, status: response.status(), request: response.request().postDataJSON() })
})
const buyer = page.frameLocator('#buyer'), a = page.frameLocator('#a'), b = page.frameLocator('#b')
const card = (frame, order) => frame.locator('.order').filter({ hasText: order.timestamp })
const tracked = (frame, order) => frame.getByTestId('order-card').filter({ hasText: order.timestamp })
async function caption(name, line1, line2 = '') {
  events.push({ name, seconds: seconds(), line1, line2 })
  console.log(name)
  await page.locator('#caption').evaluate((el, lines) => {
    el.querySelector('strong').textContent = lines[0]; el.querySelector('small').textContent = lines[1]
  }, [line1,line2])
}
async function click(locator) {
  await locator.scrollIntoViewIfNeeded()
  const box = await locator.boundingBox()
  if (!box) throw Error('The requested control is not visible')
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 12 })
  await pause(250)
  await locator.click()
}
async function shot(name) { await page.screenshot({ path: path.join(out, `${name}.jpg`) }) }
async function show(name) { await click(page.getByRole('button', {name,exact:true})) }
async function check(condition, message) { if (!(await condition)) throw Error(message) }
async function readyLobby(frame) {
  await frame.getByRole('button',{name:'重新整理',exact:true}).and(frame.locator(':enabled')).waitFor()
  await check(frame.getByRole('alert').count().then(n=>n===0), 'Lobby load failed')
}
async function readyTracking(frame) {
  await frame.getByTestId('refresh-orders').and(frame.locator(':enabled')).waitFor()
  await check(frame.getByTestId('orders-error').count().then(n=>n===0), 'Tracking load failed')
}
async function createOrder(number, twoMeals) {
  if (number > 1) await click(buyer.getByRole('link',{name:'← 回點餐頁',exact:true}))
  const restaurant = buyer.locator(`[data-testid="restaurant-card"][data-restaurant-id="${restaurantId}"]`)
  await click(restaurant.getByRole('button',{name:'查看菜單 →',exact:true}))
  const menu = buyer.getByTestId('menu-item')
  await click(menu.nth(0).getByRole('button'))
  if (twoMeals) await click(menu.nth(1).getByRole('button'))
  const items = buyer.getByTestId('cart-item')
  if (twoMeals) await click(items.nth(1).getByRole('button',{name:'增加數量',exact:true}))
  for (const item of await items.all()) await item.getByTestId('cart-note').fill(`聯測 ${fixture.run} 第 ${number} 張，不需出餐`)
  await buyer.getByTestId('checkout-address').fill(`聯測 ${fixture.run} 第 ${number} 張，不需配送`)
  await caption(`cart-${number}`,number===1?'先選兩種餐點，改一下數量，再填送餐地址。':'再從點餐頁送出第二張測試單。', '餐點備註和送餐地址都會跟著訂單傳到後端。')
  await shot(`0${number}-cart`); await pause(4500)
  const orderResponse = page.waitForResponse(r=>new URL(r.url()).pathname==='/api/add_new_order'&&r.request().method()==='POST')
  await click(buyer.getByTestId('checkout-submit'))
  const response = await orderResponse, payload = await response.json()
  if (response.status()!==201 || !payload.orders?.length) throw Error('Checkout did not create an order')
  const order = { buyer_id:fixture.accounts.buyer, timestamp:payload.orders[0].timestamp,
    order_ids:payload.orders.map(x=>x.order_id), response_timestamp:payload.timestamp }
  orders.push(order)
  await tracked(buyer,order).waitFor()
  await readyTracking(buyer)
  await check(tracked(buyer,order).getAttribute('data-order-state').then(s=>s==='pending'),'Buyer order is not pending')
  await caption(`buyer-order-${number}`,'下單後會轉到「我的訂單」，可以看到餐點、地址和待接單狀態。','購物車只會在餐點與外送資料都確認成功後清空。')
  await shot(`0${number}-buyer-order`); await pause(4000)
  return order
}
async function readResults() {
  const response = await fetch('http://127.0.0.1:8770/results')
  if (!response.ok) throw Error('Result query failed')
  return response.json()
}

let passed = false, result
try {
  await page.goto('http://127.0.0.1:8770/')
  await buyer.getByTestId('restaurant-card').first().waitFor()
  await show('兩位外送員')
  await Promise.all([a,b].map(readyLobby))
  await show('買家頁面')
  await caption('intro','這次把點餐、購物車和外送員頁面接起來測試。', rehearsal?'先在本機試跑操作。':'前端連到組員的 Flask 後端，使用專門建立的測試帳號。')
  await pause(3500)
  const first = await createOrder(1,true)
  const second = await createOrder(2,false)
  await show('兩位外送員')
  await caption('polling','切到兩位外送員的大廳，等清單自己更新。','這裡沒有按重新整理；頁面每 15 秒會重新查詢。')
  await Promise.all([a,b].flatMap(frame=>[first,second].map(order=>card(frame,order).waitFor())))
  await Promise.all([a,b].map(readyLobby))
  await shot('03-auto-new-orders'); await pause(4000)
  // Refresh both before the conflict so courier B still has a current, then stale, card.
  await Promise.all([a,b].map(frame=>frame.getByRole('button',{name:'重新整理',exact:true}).click()))
  await Promise.all([a,b].map(readyLobby))
  await caption('duplicate',`先讓外送員 #${fixture.accounts.a} 接第一張，再讓 #${fixture.accounts.b} 接同一張。`,'第二個頁面還留著舊卡片，後端仍要擋住重複接單。')
  const accepted = page.waitForResponse(r=>new URL(r.url()).pathname==='/api/process_order'&&r.status()===200)
  await click(card(a,first).getByRole('button',{name:'接單',exact:true})); await accepted
  const conflict = page.waitForResponse(r=>new URL(r.url()).pathname==='/api/process_order'&&r.status()===409)
  await click(card(b,first).getByRole('button',{name:'接單',exact:true})); await conflict
  await b.locator('.notice.error').waitFor()
  await shot('04-duplicate-red'); await pause(5500)
  await readyLobby(b)
  await caption('second-accept',`外送員 #${fixture.accounts.b} 改接第二張。`,'第一張不會被搶走，兩張單會各自對應到接單的人。')
  await click(card(b,second).getByRole('button',{name:'接單',exact:true}))
  await readyLobby(b)
  await Promise.all([a,b].map(frame=>frame.getByRole('link',{name:'我的外送單',exact:true}).click()))
  await Promise.all([a,b].map(readyTracking))
  await tracked(a,first).waitFor(); await tracked(b,second).waitFor()
  if (await tracked(a,second).count() || await tracked(b,first).count()) throw Error('Courier ownership is mixed')
  await caption('own-orders','再開組員做的「我的外送單」，兩邊都查得到各自接的單。','不是只有大廳顯示成功；個別訂單頁也有餐點、地址和外送中狀態。')
  await shot('05-own-orders'); await pause(6000)
  await show('查詢後端紀錄')
  await page.getByRole('cell',{name:'PROCESSING',exact:true}).first().waitFor()
  result = await readResults()
  if (result.length!==2 || result.some((o,i)=>o.data.delivery.body[0].deliverer_id!==fixture.accounts[i===0?'a':'b']||o.data.meals.body.some(x=>x.order_state!=='processing'))) throw Error('API ownership mismatch')
  await caption('query','從接單頁切過來，重新查後端保存的訂單。','接單外送員和兩個頁面相同。這裡用查詢 API 讀取，沒有直接登入資料庫。')
  await shot('06-api-records'); await pause(6500)
  await show('外送員 B')
  await caption('abandon','再試一次棄單，確認訂單會回到待接清單。','這是原本外送員訂單頁已有的操作。')
  await click(tracked(b,second).getByTestId('abandon-order'))
  await readyTracking(b); await tracked(b,second).waitFor({state:'detached'})
  await click(b.getByRole('link',{name:'← 回接單大廳',exact:true}))
  await card(b,second).waitFor(); await readyLobby(b)
  await shot('07-return-to-pending'); await pause(3500)
  await click(card(b,second).getByRole('button',{name:'接單',exact:true}))
  await readyLobby(b)
  await click(b.getByRole('link',{name:'我的外送單',exact:true})); await readyTracking(b)
  await caption('complete-courier','重新接下第二張，從外送員頁按「完成訂單」。','完成後就不再顯示棄單和完成按鈕。')
  await click(tracked(b,second).getByTestId('complete-order'))
  await readyTracking(b)
  await check(tracked(b,second).getAttribute('data-order-state').then(s=>s==='completed'),'Courier completion failed')
  await shot('08-courier-complete'); await pause(4500)
  await show('買家頁面')
  await caption('buyer-sync','回買家頁看更新結果，再確認收到第一張的餐點。','第二張會顯示已完成；第一張也能由買家確認收餐。')
  await tracked(buyer,second).locator('[data-testid="order-state"]').filter({hasText:'已完成'}).waitFor()
  await readyTracking(buyer)
  await click(tracked(buyer,first).getByTestId('complete-order'))
  await readyTracking(buyer)
  await check(tracked(buyer,first).getAttribute('data-order-state').then(s=>s==='completed'),'Buyer completion failed')
  await shot('09-buyer-complete'); await pause(5000)
  await show('兩位外送員')
  await tracked(a,first).getByTestId('order-state').filter({hasText:'已完成'}).waitFor()
  await caption('complete-sync','兩個外送員頁也會更新成已完成。','從下單、接單到完成，買家和外送員看到的狀態一致。')
  await shot('10-completion-sync'); await pause(5000)
  await show('查詢後端紀錄')
  await page.getByRole('cell',{name:'COMPLETED',exact:true}).first().waitFor()
  result = await readResults()
  if (result.some(o=>o.data.meals.body.some(x=>x.order_state!=='completed'))) throw Error('Final API state mismatch')
  if (errors.length) throw Error('Browser runtime errors')
  await caption('final-query','最後再查一次，兩張訂單的餐點都已完成。','這次只操作專用測試單，原有資料另外比對。')
  await shot('11-final-query'); await pause(5000)
  passed = true
} catch (error) {
  await shot('failed-step')
  errors.push(String(error.stack || error))
  console.error(error)
} finally {
  events.push({name:'end',seconds:seconds()})
  await writeFile(path.join(out,'flow-results.json'),JSON.stringify({passed,rehearsal,base:fixture.base,accounts:fixture.accounts,
    started_at:new Date(started).toISOString(),events,responses,orders,errors,final:result},null,2)+'\n')
  const video=page.video()
  await context.close()
  if(video) await video.saveAs(path.join(out,'checkout-flow.webm'))
  await browser.close()
}
if(!passed) process.exitCode=1
