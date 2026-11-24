// 點餐頁沿用既有 Flask API；示範帳號尚未串接登入。
const configuredBuyerId = String(import.meta.env.VITE_BUYER_ID ?? '2')
export const BUYER_ID = /^[1-9]\d*$/.test(configuredBuyerId) ? Number(configuredBuyerId) : Number.NaN
export const API_SCOPE = String(import.meta.env.VITE_API_SCOPE ?? '').trim() || window.location.origin
export const CHECKOUT_STORAGE_KEY = `ntou:checkout:v1:${encodeURIComponent(API_SCOPE)}:buyer:${BUYER_ID}`
export const CHECKOUT_HISTORY_KEY = `${CHECKOUT_STORAGE_KEY}:history`
export const CHECKOUT_LOCK_KEY = `${CHECKOUT_STORAGE_KEY}:lock`

export async function withCheckoutLock<T>(operation: () => Promise<T>): Promise<T> {
  // 不支援 Web Locks 的瀏覽器仍由頁面自己的 submitting 防連點。
  if (!navigator.locks?.request) return operation()
  return navigator.locks.request(CHECKOUT_LOCK_KEY, { mode: 'exclusive', ifAvailable: true }, async lock => {
    if (!lock) throw new Error('另一個分頁正在處理下單，請先等它完成，再查看訂單紀錄。')
    return operation()
  })
}

export interface Restaurant {
  id: number
  name: string
  description: string
}

export interface MenuItem {
  id: number
  name: string
  price: number
  restaurantId: number
}

export interface CartItem extends MenuItem {
  quantity: number
  note: string
}

interface OrderMeal {
  meal_id: number
  quantity: number
  note: string | null
}

export interface OrderIdentity {
  buyerId: number
  timestamp: string
  orderIds: number[]
}

export type CheckoutPhase = 'creating' | 'create_unknown' | 'verify_order' | 'delivery' | 'delivery_unknown' | 'complete'

export interface CheckoutAttempt {
  phase: CheckoutPhase
  buyerId: number
  address: string
  meals: OrderMeal[]
  order?: OrderIdentity
  startedAt?: string
  unconfirmed?: {
    reason: 'request_unconfirmed' | 'response_mismatch'
    httpStatus?: number
    candidateRows?: { buyerId: number | null; timestamp: string | null; orderId: number | null }[]
  }
}

interface ArchivedCheckout {
  archivedAt: string
  reason: 'user_ended_checkout'
  serverOrderChanged: false
  attempt: CheckoutAttempt
}

export interface SavedCheckout {
  version: 1
  buyerId: number
  scope: string
  cart: CartItem[]
  address: string
  restaurantId: number | null
  attempt: CheckoutAttempt | null
}

export const validBuyer = Number.isSafeInteger(BUYER_ID) && BUYER_ID > 0
const TIMEOUT_MS = 15000
const timestampPattern = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function positiveInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0
}

function id(value: unknown): number | null {
  const number = typeof value === 'string' && /^[1-9]\d*$/.test(value) ? Number(value) : value
  return positiveInteger(number) ? number : null
}

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly hasJsonBody: boolean, readonly explicitlyRejected = false) {
    super(message)
  }
}

async function requestJson(path: string, body?: unknown): Promise<unknown> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), TIMEOUT_MS)
  try {
    const response = await fetch(path, {
      method: body === undefined ? 'GET' : 'POST',
      ...(body === undefined ? {} : {
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
      signal: controller.signal,
    })
    let data: unknown
    try {
      data = await response.json()
    } catch {
      if (!response.ok) throw new ApiError(`伺服器回應異常（HTTP ${response.status}）。`, response.status, false)
      throw new Error('伺服器回傳的資料格式不完整。')
    }
    if (!response.ok) {
      const message = record(data) && typeof data.message === 'string' ? data.message
        : record(data) && typeof data.error === 'string' ? data.error
          : `伺服器回應異常（HTTP ${response.status}）。`
      throw new ApiError(message, response.status, record(data), record(data) && data.success === false)
    }
    return data
  } catch (error) {
    if (controller.signal.aborted) throw new Error('等待伺服器回應逾時。')
    throw error
  } finally {
    window.clearTimeout(timeout)
  }
}

export async function loadCatalog(): Promise<{ restaurants: Restaurant[]; meals: MenuItem[] }> {
  const [restaurantRows, mealRows] = await Promise.all([
    requestJson('/api/view_all_restaurants'),
    requestJson('/api/view_all_meals'),
  ])
  if (!Array.isArray(restaurantRows) || !Array.isArray(mealRows)) {
    throw new Error('餐廳或菜單資料格式不完整，請重新載入。')
  }
  const restaurants = restaurantRows.map((value: unknown): Restaurant => {
    if (!record(value) || !positiveInteger(value.restaurant_id)
      || typeof value.restaurant_name !== 'string' || typeof value.restaurant_address !== 'string') {
      throw new Error('餐廳資料不完整，請重新載入。')
    }
    return { id: value.restaurant_id, name: value.restaurant_name, description: value.restaurant_address }
  })
  const meals = mealRows.map((value: unknown): MenuItem => {
    if (!record(value) || !positiveInteger(value.meal_id) || !positiveInteger(value.restaurant_id)
      || typeof value.meal_name !== 'string' || typeof value.price !== 'number'
      || !Number.isSafeInteger(value.price) || value.price < 0
      || !restaurants.some(restaurant => restaurant.id === value.restaurant_id)) {
      throw new Error('菜單資料不完整，請重新載入。')
    }
    return { id: value.meal_id, name: value.meal_name, price: value.price, restaurantId: value.restaurant_id }
  })
  if (new Set(restaurants.map(item => item.id)).size !== restaurants.length
    || new Set(meals.map(item => item.id)).size !== meals.length) {
    throw new Error('餐廳或菜單資料有重複編號，請重新載入。')
  }
  return { restaurants, meals }
}

function isMeal(value: unknown): value is OrderMeal {
  return record(value) && positiveInteger(value.meal_id) && positiveInteger(value.quantity)
    && value.quantity <= 99 && (value.note === null || (typeof value.note === 'string' && value.note.length <= 100))
}

function isIdentity(value: unknown): value is OrderIdentity {
  return record(value) && value.buyerId === BUYER_ID && typeof value.timestamp === 'string'
    && timestampPattern.test(value.timestamp) && Array.isArray(value.orderIds) && value.orderIds.length > 0
    && value.orderIds.every(positiveInteger) && new Set(value.orderIds).size === value.orderIds.length
}

function isAttempt(value: unknown): value is CheckoutAttempt {
  if (!record(value) || value.buyerId !== BUYER_ID || typeof value.address !== 'string'
    || !value.address.trim() || value.address.length > 120 || !Array.isArray(value.meals)
    || !value.meals.length || !value.meals.every(isMeal)
    || new Set(value.meals.map(item => item.meal_id)).size !== value.meals.length
    || !['creating', 'create_unknown', 'verify_order', 'delivery', 'delivery_unknown', 'complete'].includes(String(value.phase))
    || (value.startedAt !== undefined && (typeof value.startedAt !== 'string' || value.startedAt.length > 40))) return false
  if (value.unconfirmed !== undefined) {
    const facts = value.unconfirmed
    if (!record(facts) || !['request_unconfirmed', 'response_mismatch'].includes(String(facts.reason))
      || (facts.httpStatus !== undefined && !positiveInteger(facts.httpStatus))
      || (facts.candidateRows !== undefined && (!Array.isArray(facts.candidateRows)
        || !facts.candidateRows.every((row: unknown) => record(row)
          && (row.buyerId === null || positiveInteger(row.buyerId))
          && (row.orderId === null || positiveInteger(row.orderId))
          && (row.timestamp === null || (typeof row.timestamp === 'string' && timestampPattern.test(row.timestamp))))))) return false
  }
  return value.phase === 'creating' || value.phase === 'create_unknown' ? value.order === undefined : isIdentity(value.order)
}

function isCartItem(value: unknown): value is CartItem {
  return record(value) && positiveInteger(value.id) && positiveInteger(value.restaurantId)
    && typeof value.name === 'string' && typeof value.price === 'number' && Number.isSafeInteger(value.price)
    && value.price >= 0 && positiveInteger(value.quantity) && value.quantity <= 99
    && typeof value.note === 'string' && value.note.length <= 100
}

export function emptyCheckout(): SavedCheckout {
  return { version: 1, buyerId: BUYER_ID, scope: API_SCOPE, cart: [], address: '', restaurantId: null, attempt: null }
}

export function readCheckout(): SavedCheckout {
  let raw: string | null
  try {
    raw = localStorage.getItem(CHECKOUT_STORAGE_KEY)
  } catch {
    throw new Error('瀏覽器無法讀取購物車紀錄，請先允許這個網站使用本機儲存空間。')
  }
  if (!raw) return emptyCheckout()
  let value: unknown
  try { value = JSON.parse(raw) } catch { /* 保留原紀錄，避免覆蓋未完成的下單。 */ }
  if (!record(value) || value.version !== 1 || value.buyerId !== BUYER_ID || value.scope !== API_SCOPE
    || !Array.isArray(value.cart) || !value.cart.every(isCartItem)
    || new Set(value.cart.map(item => item.id)).size !== value.cart.length
    || typeof value.address !== 'string' || value.address.length > 120
    || !(value.restaurantId === null || positiveInteger(value.restaurantId))
    || !(value.attempt === null || isAttempt(value.attempt))) {
    throw new Error('購物車紀錄無法讀取。請先到「我的訂單」確認是否已下單，避免重複送出。')
  }
  const saved = value as unknown as SavedCheckout
  // 關頁、重整或斷線時，不能把尚未收到回應的建單當作沒有成功。
  if (saved.attempt?.phase === 'creating') saved.attempt.phase = 'create_unknown'
  return saved
}

export function writeCheckout(value: SavedCheckout): void {
  try {
    localStorage.setItem(CHECKOUT_STORAGE_KEY, JSON.stringify(value))
  } catch {
    throw new Error('購物車紀錄暫時無法保存，請先確認瀏覽器儲存空間，再繼續下單。')
  }
}

function copyAttempt(attempt: CheckoutAttempt): CheckoutAttempt {
  // 歸檔只保留下單事實，不複製任意回應欄位或憑證。
  return {
    phase: attempt.phase, buyerId: attempt.buyerId, address: attempt.address,
    meals: attempt.meals.map(item => ({ meal_id: item.meal_id, quantity: item.quantity, note: item.note })),
    ...(attempt.startedAt ? { startedAt: attempt.startedAt } : {}),
    ...(attempt.order ? { order: { buyerId: attempt.order.buyerId, timestamp: attempt.order.timestamp,
      orderIds: [...attempt.order.orderIds] } } : {}),
    ...(attempt.unconfirmed ? { unconfirmed: {
      reason: attempt.unconfirmed.reason,
      ...(attempt.unconfirmed.httpStatus === undefined ? {} : { httpStatus: attempt.unconfirmed.httpStatus }),
      ...(attempt.unconfirmed.candidateRows ? { candidateRows: attempt.unconfirmed.candidateRows.map(row => ({
        buyerId: row.buyerId, timestamp: row.timestamp, orderId: row.orderId,
      })) } : {}),
    } } : {}),
  }
}

export function archiveCheckout(expected: CheckoutAttempt): SavedCheckout {
  const current = readCheckout()
  if (!current.attempt || JSON.stringify(current.attempt) !== JSON.stringify(expected)) {
    throw new Error('下單紀錄已經更新，請重新整理並確認目前的訂單。')
  }
  let entries: ArchivedCheckout[] = []
  try {
    const raw = localStorage.getItem(CHECKOUT_HISTORY_KEY)
    if (raw) {
      const history: unknown = JSON.parse(raw)
      if (!record(history) || history.version !== 1 || history.buyerId !== BUYER_ID || history.scope !== API_SCOPE
        || !Array.isArray(history.entries) || !history.entries.every((entry: unknown) => record(entry)
          && typeof entry.archivedAt === 'string' && entry.reason === 'user_ended_checkout'
          && entry.serverOrderChanged === false && isAttempt(entry.attempt))) {
        throw new Error('Invalid checkout history')
      }
      entries = (history.entries as ArchivedCheckout[]).map(entry => ({
        archivedAt: entry.archivedAt, reason: 'user_ended_checkout', serverOrderChanged: false,
        attempt: copyAttempt(entry.attempt),
      }))
    }
  } catch {
    throw new Error('先前的下單紀錄暫時無法讀取，這次購物車仍會保留。')
  }
  const attempt = copyAttempt(current.attempt)
  // 若上次已歸檔但清空失敗，重試時保留同一份紀錄即可。
  if (JSON.stringify(entries.at(-1)?.attempt) !== JSON.stringify(attempt)) {
    entries.push({ archivedAt: new Date().toISOString(), reason: 'user_ended_checkout', serverOrderChanged: false, attempt })
  }
  try {
    localStorage.setItem(CHECKOUT_HISTORY_KEY, JSON.stringify({ version: 1, buyerId: BUYER_ID, scope: API_SCOPE, entries }))
  } catch {
    throw new Error('下單紀錄無法歸檔，這次購物車仍會保留。請先確認瀏覽器儲存空間。')
  }
  // 先存好紀錄才清空。這個函式不呼叫任何 API，也不改變伺服器訂單。
  const cleared = emptyCheckout()
  writeCheckout(cleared)
  return cleared
}

function orderStamp(order: OrderIdentity) {
  return { buyer_id: order.buyerId, timestamp: order.timestamp }
}

function readOrderRows(value: unknown, attempt: CheckoutAttempt, order?: OrderIdentity): { identity: OrderIdentity; state: string } {
  if (!Array.isArray(value) || value.length !== attempt.meals.length || value.length === 0) {
    throw new Error('訂單明細與購物車不一致，請先查詢我的訂單。')
  }
  let timestamp: string | undefined
  let state: string | undefined
  const orderIds: number[] = []
  const mealIds: number[] = []
  for (const row of value as unknown[]) {
    if (!record(row) || id(row.buyer_id) !== attempt.buyerId || !positiveInteger(row.order_id)
      || !positiveInteger(row.meal_id) || typeof row.timestamp !== 'string' || !timestampPattern.test(row.timestamp)
      || !['pending', 'processing', 'completed', 'cancelled'].includes(String(row.order_state))) {
      throw new Error('訂單識別資料不完整，請先查詢我的訂單。')
    }
    const expected = attempt.meals.find(item => item.meal_id === row.meal_id)
    if (!expected || row.quantity !== expected.quantity || (row.note ?? '') !== (expected.note ?? '')
      || (timestamp !== undefined && timestamp !== row.timestamp)
      || (state !== undefined && state !== row.order_state)) {
      throw new Error('訂單明細或下單時間不一致，暫時無法建立送餐資料。')
    }
    timestamp = row.timestamp
    state = String(row.order_state)
    orderIds.push(row.order_id)
    mealIds.push(row.meal_id)
  }
  if (!timestamp || !state || new Set(orderIds).size !== orderIds.length || new Set(mealIds).size !== mealIds.length) {
    throw new Error('訂單明細有重複或缺漏，請先查詢我的訂單。')
  }
  if (order && (timestamp !== order.timestamp || order.buyerId !== attempt.buyerId
    || orderIds.length !== order.orderIds.length || orderIds.some(value => !order.orderIds.includes(value)))) {
    throw new Error('查回的訂單編號與剛建立的訂單不同，暫時無法建立送餐資料。')
  }
  return { identity: { buyerId: attempt.buyerId, timestamp, orderIds }, state }
}

async function hasMatchingDelivery(attempt: CheckoutAttempt, order: OrderIdentity): Promise<boolean> {
  let result: unknown
  try {
    result = await requestJson('/api/view_delivery', orderStamp(order))
  } catch (error) {
    if (error instanceof ApiError && error.status === 404 && error.hasJsonBody) return false
    throw error
  }
  if (!Array.isArray(result) || result.length > 1) throw new Error('送餐資料格式不完整，請先查詢我的訂單。')
  if (result.length === 0) return false
  const delivery: unknown = result[0]
  if (!record(delivery) || id(delivery.buyer_id) !== order.buyerId || delivery.timestamp !== order.timestamp
    || typeof delivery.pickup_address !== 'string') {
    throw new Error('查回的送餐資料與這筆訂單不同，請先查詢我的訂單。')
  }
  if (delivery.pickup_address.trim() !== attempt.address) {
    throw new Error('這筆訂單已經有不同的送餐地址，請到「我的訂單」確認，這次不會覆寫。')
  }
  return true
}

type SaveAttempt = (attempt: CheckoutAttempt | null) => void

export async function finishCheckout(
  previous: CheckoutAttempt | null,
  cart: CartItem[],
  address: string,
  saveAttempt: SaveAttempt,
): Promise<OrderIdentity> {
  if (!validBuyer) throw new Error('示範買家帳號設定無效，VITE_BUYER_ID 必須是正整數。')
  let attempt = previous
  if (!attempt) {
    if (!cart.length || !cart.every(isCartItem)) throw new Error('請確認購物車餐點、數量與備註。')
    if (!address.trim() || address.trim().length > 120) throw new Error('請填寫送餐地址，最多 120 個字。')
    attempt = {
      phase: 'creating', buyerId: BUYER_ID, address: address.trim(),
      startedAt: new Date().toISOString(),
      meals: cart.map(item => ({ meal_id: item.id, quantity: item.quantity, note: item.note.trim() || null })),
    }
    saveAttempt(attempt)
    let created: unknown
    try {
      created = await requestJson('/api/add_new_order', { buyer_id: BUYER_ID, meals: attempt.meals })
    } catch (error) {
      if (error instanceof ApiError && error.status >= 400 && error.status < 500 && error.explicitlyRejected) {
        saveAttempt(null)
        throw error
      }
      saveAttempt({ ...attempt, phase: 'create_unknown', unconfirmed: {
        reason: 'request_unconfirmed', ...(error instanceof ApiError ? { httpStatus: error.status } : {}),
      } })
      throw new Error('尚無法確認訂單是否建立，請先查看「我的訂單」。這筆下單不會自動重送。')
    }
    let order: OrderIdentity
    try {
      if (!record(created) || created.success !== true) throw new Error('建單回應不完整。')
      // 整單時間只採用每列 Orders 共同的 timestamp，不使用回應根層時間或自行加秒。
      const result = readOrderRows(created.orders, attempt)
      if (result.state !== 'pending') throw new Error('新訂單狀態不正確。')
      order = result.identity
    } catch {
      const candidateRows = record(created) && Array.isArray(created.orders)
        ? created.orders.filter(record).map(row => ({ buyerId: id(row.buyer_id), orderId: id(row.order_id),
          timestamp: typeof row.timestamp === 'string' && timestampPattern.test(row.timestamp) ? row.timestamp : null }))
        : []
      saveAttempt({ ...attempt, phase: 'create_unknown', unconfirmed: { reason: 'response_mismatch', candidateRows } })
      throw new Error('訂單已送出，但回應資料無法核對。請先查看「我的訂單」，這筆下單不會重送。')
    }
    attempt = { ...attempt, phase: 'verify_order', order }
    saveAttempt(attempt)
  }

  if (attempt.phase === 'creating' || attempt.phase === 'create_unknown' || !attempt.order) {
    throw new Error('這筆下單的結果還待確認。請先查看「我的訂單」，避免再次送出。')
  }
  const order = attempt.order
  const rows = await requestJson('/api/view_meals_of_order', orderStamp(order))
  const verified = readOrderRows(rows, attempt, order)
  if (await hasMatchingDelivery(attempt, order)) {
    saveAttempt({ ...attempt, phase: 'complete' })
    return order
  }
  if (attempt.phase === 'complete') throw new Error('原本確認的送餐資料目前查不到，請先查看「我的訂單」。')
  if (verified.state !== 'pending') throw new Error('這筆訂單已不在待接單狀態，請先查看「我的訂單」。')

  // 先保存識別再送出；後續重試只處理這筆 Delivery，絕不重建 Orders。
  attempt = { ...attempt, phase: 'delivery_unknown' }
  saveAttempt(attempt)
  let deliveryError: unknown
  try {
    await requestJson('/api/add_new_delivery', { ...orderStamp(order), pickup_address: attempt.address })
  } catch (error) {
    deliveryError = error
  }
  // 回應遺失或 409 時也先讀回。只有同一訂單、同一地址才視為完成。
  if (await hasMatchingDelivery(attempt, order)) {
    saveAttempt({ ...attempt, phase: 'complete' })
    return order
  }
  saveAttempt({ ...attempt, phase: 'delivery' })
  const reason = deliveryError instanceof ApiError ? `（${deliveryError.message}）` : ''
  throw new Error(`訂單已建立，但送餐資料還沒確認完成${reason}。請按「繼續完成下單」，不會重建訂單。`)
}
