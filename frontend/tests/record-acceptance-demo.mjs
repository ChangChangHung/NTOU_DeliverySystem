import { readFile, writeFile, mkdir } from 'node:fs/promises'
import { createRequire } from 'node:module'
import path from 'node:path'

// Run once with --rehearse, then restart local_demo.py before recording.
const [folderArg, moduleArg, mode] = process.argv.slice(2)
if (!folderArg || !moduleArg) throw new Error('Usage: node record-acceptance-demo.mjs RUN_FOLDER PLAYWRIGHT_MODULE [--rehearse]')
const folder = path.resolve(folderArg)
const seed = JSON.parse(await readFile(path.join(folder, 'seed.json'), 'utf8'))
if (seed.metadata?.synthetic_data_only !== true || String(seed.demo_courier_ids) !== '2,3') throw new Error('Expected isolated Demo with couriers #2 and #3')
const fixtures = seed.fixtures.filter(item => item.scenario.startsWith('ready_'))
const require = createRequire(import.meta.url)
const { chromium } = require(path.join(path.resolve(moduleArg), 'index.js'))
const rehearsal = mode === '--rehearse'
const out = path.join(folder, rehearsal ? 'rehearsal' : 'recording')
await mkdir(out, { recursive: true })
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const context = await browser.newContext({ viewport: { width: 1920, height: 1080 },
  ...(rehearsal ? {} : { recordVideo: { dir: out, size: { width: 1920, height: 1080 } } }) })
const page = await context.newPage()
const started = Date.now(), events = [], responses = [], requests = [], errors = []
const seconds = () => +((Date.now() - started) / 1000).toFixed(2)
page.on('pageerror', error => errors.push(error.message))
page.on('request', req => {
  if (new URL(req.url()).pathname === '/api/process_order') requests.push({ seconds: seconds(), url: req.url(), body: req.postDataJSON() })
})
page.on('response', res => {
  if (new URL(res.url()).pathname === '/api/process_order') responses.push({ seconds: seconds(), url: res.url(), status: res.status(), request: res.request().postDataJSON() })
})
const a = page.frameLocator('#courier-a'), b = page.frameLocator('#courier-b')
const card = (frame, order, own = false) => frame.locator(own ? '[data-testid="accepted-order"]' : '.order')
  .filter({ has: frame.getByText(`買家 #${order.buyer_id}`, { exact: true }) })
  .filter({ has: frame.getByText(order.timestamp, { exact: true }) })
const pause = async ms => page.waitForTimeout(rehearsal ? Math.min(ms, 180) : ms)
async function caption(name, line1, line2) {
  events.push({ name, seconds: seconds(), line1, line2 })
  await page.locator('#caption').evaluate((element, lines) => {
    element.querySelector('strong').textContent = lines[0]
    element.querySelector('span').textContent = lines[1]
  }, [line1, line2])
}
async function click(locator) {
  await locator.waitFor({ state: 'visible' })
  await locator.scrollIntoViewIfNeeded()
  const box = await locator.boundingBox()
  if (!box) throw new Error('Target has no visible bounding box')
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 15 })
  await pause(450)
  await locator.click()
}
async function navigateDatabase(locator) {
  const navigation = page.waitForEvent('framenavigated', frame => frame.url() === 'http://127.0.0.1:8767/database')
  await click(locator)
  const frame = await navigation
  await frame.waitForLoadState('load')
}
async function shot(name) { await page.screenshot({ path: path.join(out, `${name}.jpg`) }) }
async function ready(frame) {
  await frame.getByRole('button', { name: '重新整理', exact: true }).waitFor()
  await frame.locator('button').filter({ hasText: /^重新整理$/ }).and(frame.locator(':enabled')).waitFor()
  if (await frame.getByRole('alert').count()) throw new Error('Unexpected load error')
}
async function moveOwn(frame, order) {
  await card(frame, order, true).waitFor()
  await frame.getByTestId('accepted-orders').evaluate(element => element.scrollIntoView({ behavior: 'smooth', block: 'center' }))
  await pause(1000)
}
async function dbState(expected) {
  const response = await page.request.get('http://127.0.0.1:8767/query')
  const body = await response.json()
  if (body.database !== seed.metadata.database || body.rows.length !== 3) throw new Error('Unexpected Demo database')
  for (const row of body.rows) {
    const courier = expected[String(row.buyer_id)]
    if (row.deliverer_id !== courier || row.order_state !== (courier === null ? 'PENDING' : 'PROCESSING')) throw new Error('Database state mismatch')
  }
  await writeFile(path.join(out, `database-${events.at(-1).name}.json`), JSON.stringify(body, null, 2))
  return body
}
try {
  await page.goto('http://127.0.0.1:8767/')
  await Promise.all([a,b].map(frame => frame.getByText('待接訂單 2 筆', { exact: true }).waitFor()))
  // Recording annotation only; the frames below are the actual courier pages.
  await page.evaluate(() => {
    const cursor = document.createElement('div')
    cursor.id = 'recording-cursor'
    cursor.style.cssText = 'position:fixed;left:20px;top:20px;z-index:1000;pointer-events:none;width:24px;height:24px'
    cursor.innerHTML = '<svg width="24" height="24" viewBox="0 0 24 24"><path d="M5 3L19 12L12 13L9 20L5 3Z" fill="white" stroke="black" stroke-width="1.5"/></svg>'
    document.body.appendChild(cursor)
  })
  // Cross-origin frame mouse events do not bubble; move the annotation with each recorded target.
  const originalMove = page.mouse.move.bind(page.mouse)
  page.mouse.move = async (x,y,opts) => {
    await page.locator('#recording-cursor').evaluate((el,pos) => { el.style.left=pos.x+'px';el.style.top=pos.y+'px' }, {x,y})
    return originalMove(x,y,opts)
  }
  await caption('intro', '我用兩個外送員頁面，測試接單後的結果。', '左邊是 #2，右邊是 #3；這次先用測試身分，還沒接登入。')
  await shot('01-two-couriers'); await pause(6500)
  await caption('database-before', '先查一下資料庫，這兩張單都還沒有人接。', '三筆餐點的狀態都是 PENDING，接單外送員也還是空的。')
  await navigateDatabase(page.getByRole('button',{name:'查詢資料庫',exact:true}))
  await page.frameLocator('#database').getByRole('heading',{name:'這次接單的資料庫紀錄'}).waitFor()
  await dbState({'1':null,'4':null}); await shot('02-database-before'); await pause(6500)
  await caption('courier-2-accepts', '先讓 #2 接買家 #1 的訂單。', '這張單有兩種餐點，要一起交給同一位外送員。')
  await click(page.getByRole('button',{name:'外送員接單頁',exact:true}))
  await pause(1500)
  await click(card(a,fixtures[0]).getByRole('button',{name:'接單',exact:true}))
  await a.getByText('接單成功！',{exact:true}).waitFor(); await ready(a)
  await pause(1800)
  await caption('courier-2-own-order', '接完後，這張單出現在 #2 的「我的已接訂單」。', '買家、下單時間和兩筆餐點都對得上。')
  await moveOwn(a,fixtures[0]); await shot('03-courier-2-own-order'); await pause(6000)
  await caption('stale-page-conflict', '#3 還沒更新頁面，再接同一張單看看。', '畫面出現紅字，提示這張單已經被接走了。')
  await click(card(b,fixtures[0]).getByRole('button',{name:'接單',exact:true}))
  await b.locator('.notice.error').waitFor(); await ready(b)
  if (await card(b,fixtures[0],true).count()) throw new Error('Rejected courier incorrectly owns order')
  await b.locator('.notice.error').evaluate(el=>el.scrollIntoView({block:'center',behavior:'smooth'}))
  await pause(800); await shot('04-real-409'); await pause(6500)
  await caption('courier-3-accepts', '接著讓 #3 接另一張，買家 #4 的訂單。', '這次接單成功，會放進 #3 自己的清單。')
  await click(card(b,fixtures[1]).getByRole('button',{name:'接單',exact:true}))
  await b.getByText('接單成功！',{exact:true}).waitFor(); await ready(b)
  await moveOwn(b,fixtures[1]); await shot('05-courier-3-own-order'); await pause(6000)
  await caption('refresh-own-orders', '兩邊都重新整理一次，確認訂單還在。', '#2 只有買家 #1 的單，#3 只有買家 #4 的單。')
  for (const frame of [a,b]) { await click(frame.getByRole('button',{name:'重新整理',exact:true})); await ready(frame) }
  await moveOwn(a,fixtures[0]); await moveOwn(b,fixtures[1])
  if (await card(a,fixtures[1],true).count() || await card(b,fixtures[0],true).count()) throw new Error('Courier lists overlap')
  await shot('06-both-own-orders'); await pause(6500)
  await caption('database-after', '現在從接單頁切到資料庫，再查一次。', '買家 #1 的兩筆餐點是 #2 接的；買家 #4 的單則是 #3。')
  await navigateDatabase(page.getByRole('button',{name:'查詢資料庫',exact:true}))
  const db = page.frameLocator('#database')
  await db.getByRole('heading',{name:'這次接單的資料庫紀錄'}).waitFor()
  await navigateDatabase(db.getByRole('link',{name:'重新查詢資料庫',exact:true}))
  await db.getByRole('heading',{name:'這次接單的資料庫紀錄'}).waitFor()
  const finalDB=await dbState({'1':2,'4':3}); await shot('07-database-after'); await pause(7000)
  await caption('actual-sql', '三筆餐點都變成 PROCESSING，和兩個接單頁一致。', '這裡可以展開剛才用的 SQL，查的是同一份 MySQL。')
  await click(db.getByText('查看查詢 SQL',{exact:true})); await shot('08-sql'); await pause(6500)
  await caption('offline-error', '再試一個例外：讓 #3 暫時連不上後端。', '按重新整理後會顯示載入失敗，不會說成「沒有訂單」。')
  await click(page.getByRole('button',{name:'外送員接單頁',exact:true}))
  const offline = route => route.abort('internetdisconnected')
  await context.route('http://127.0.0.1:5181/api/**',offline)
  await click(b.getByRole('button',{name:'重新整理',exact:true}))
  await b.getByRole('alert').waitFor()
  if (await b.getByText('目前沒有已接訂單',{exact:true}).count()) throw new Error('Error shown as empty orders')
  await shot('09-offline-error'); await pause(6500)
  await caption('connection-restored', '恢復連線，再重新整理。', '#3 剛接的單又能查到了，資料庫裡的接單紀錄也沒有改變。')
  await context.unroute('http://127.0.0.1:5181/api/**',offline)
  await click(b.getByRole('button',{name:'重新整理',exact:true})); await ready(b)
  await moveOwn(a,fixtures[0]); await moveOwn(b,fixtures[1]); await dbState({'1':2,'4':3})
  await shot('10-recovered'); await pause(7000)
  if (responses.length!==3 || String(responses.map(r=>r.status))!=='200,409,200' || errors.length) throw new Error('Unexpected responses or browser errors')
  events.push({name:'end',seconds:seconds()})
  const report={passed:true,rehearsal,started_at:new Date(started).toISOString(),viewport:{width:1920,height:1080},
    database:seed.metadata.database,fixtures:fixtures.map(f=>({buyer_id:f.buyer_id,timestamp:f.timestamp,order_ids:f.order_ids})),
    events,requests,responses,errors,final_database:finalDB,recording_note:'Both iframe views load the actual courier pages. Only the comparison header, cursor and captions are recording aids. Offline case blocks courier 3 API requests locally.'}
  await writeFile(path.join(out,'demo-events.json'),JSON.stringify(report,null,2)+'\n')
  console.log(JSON.stringify({passed:true,rehearsal,out,seconds:seconds(),responses:responses.map(r=>r.status)}))
} finally {
  const video = page.video()
  await context.close()
  if (video) await video.saveAs(path.join(out,'acceptance-walkthrough.webm'))
  await browser.close()
}
