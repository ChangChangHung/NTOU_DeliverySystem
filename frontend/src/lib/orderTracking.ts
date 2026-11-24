import { computed, onMounted, onUnmounted, ref } from 'vue'

type JsonObject = Record<string, unknown>
type Role = 'buyer' | 'deliverer'
export type OrderState = 'in_cart' | 'pending' | 'processing' | 'completed' | 'cancelled'
export type OrderAction = 'cancel' | 'complete' | 'abandon'
interface Stamp { buyer_id: number; timestamp: string }
interface OrderRow extends Stamp {
  order_id: number; meal_id: number; quantity: number; note: string | null; order_state: OrderState
}
interface Delivery extends Stamp { deliverer_id: number | null; pickup_address: string }
interface Meal { meal_id: number; meal_name: string; restaurant_id: number; price: number }
interface Restaurant { restaurant_id: number; restaurant_name: string }
export interface TrackedOrder extends Stamp {
  address: string
  deliverer_id: number | null
  deliveryMissing: boolean
  state: OrderState
  items: { name: string; restaurant: string; price: number; quantity: number; note: string | null }[]
  total: number
  orderIds: number[]
}

export const stateText: Record<OrderState, string> = {
  in_cart: '購物車中', pending: '待接單', processing: '外送中', completed: '已完成', cancelled: '已取消',
}

const object = (value: unknown): value is JsonObject => typeof value === 'object' && value !== null && !Array.isArray(value)
const positive = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value > 0
const text = (value: unknown): value is string => typeof value === 'string' && value.trim().length > 0
const state = (value: unknown): value is OrderState => typeof value === 'string' && Object.hasOwn(stateText, value)
const invalid = () => new Error('訂單資料不完整或前後不一致，請重新整理。')

export function demoAccount(value: unknown): number | null {
  if (typeof value !== 'string' || !/^[1-9]\d*$/.test(value)) return null
  const id = Number(value)
  return positive(id) ? id : null
}

function timestamp(value: unknown): value is string {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(value)) return false
  const parsed = new Date(value.replace(' ', 'T') + 'Z')
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 19).replace('T', ' ') === value
}

function sameStamp(value: JsonObject, stamp: Stamp) {
  return positive(value.buyer_id) && value.buyer_id === stamp.buyer_id && value.timestamp === stamp.timestamp
}

function array(value: unknown): unknown[] {
  if (!Array.isArray(value)) throw invalid()
  return value
}

function unique<T>(values: T[], key: (value: T) => string | number): T[] {
  if (new Set(values.map(key)).size !== values.length) throw invalid()
  return values
}

export function parseStamps(value: unknown): Stamp[] {
  return unique(array(value).map(item => {
    if (!object(item) || !timestamp(item.timestamp)) throw invalid()
    const buyerId = typeof item.buyer_id === 'string' ? demoAccount(item.buyer_id) : item.buyer_id
    if (!positive(buyerId)) throw invalid()
    return { buyer_id: buyerId, timestamp: item.timestamp }
  }), item => `${item.buyer_id}/${item.timestamp}`)
}

function parseRows(value: unknown, stamp: Stamp): OrderRow[] {
  const rows = unique(array(value).map(item => {
    if (!object(item) || !sameStamp(item, stamp) || !positive(item.order_id) || !positive(item.meal_id)
      || !positive(item.quantity) || !state(item.order_state)
      || (item.note !== null && typeof item.note !== 'string')) throw invalid()
    return { ...stamp, order_id: item.order_id, meal_id: item.meal_id, quantity: item.quantity,
      note: item.note, order_state: item.order_state }
  }), row => row.order_id).sort((a, b) => a.order_id - b.order_id)
  if (!rows.length || new Set(rows.map(row => row.order_state)).size !== 1) throw invalid()
  return rows
}

function parseDelivery(value: unknown, stamp: Stamp): Delivery {
  const rows = array(value)
  const item = rows[0]
  if (rows.length !== 1 || !object(item) || !sameStamp(item, stamp) || !text(item.pickup_address)
    || (item.deliverer_id !== null && !positive(item.deliverer_id))) throw invalid()
  return { ...stamp, deliverer_id: item.deliverer_id, pickup_address: item.pickup_address }
}

class HttpError extends Error {
  constructor(readonly status: number, message: string, readonly validErrorBody: boolean) { super(message) }
}

async function request(path: string, signal: AbortSignal, body?: unknown): Promise<unknown> {
  const controller = new AbortController()
  const abort = () => controller.abort()
  signal.addEventListener('abort', abort, { once: true })
  if (signal.aborted) abort()
  const timer = window.setTimeout(abort, 15000)
  try {
    const response = await fetch(`/api/${path}`, {
      method: body === undefined ? 'GET' : 'POST', cache: 'no-store', signal: controller.signal,
      headers: { Accept: 'application/json', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    })
    let value: unknown
    try { value = await response.json() } catch { value = undefined }
    if (!response.ok) {
      const message = object(value) && text(value.message) ? value.message
        : object(value) && text(value.error) ? value.error : `讀取或操作失敗（HTTP ${response.status}），請重試。`
      throw new HttpError(response.status, message, object(value) && value.success === false && text(value.message))
    }
    if (value === undefined) throw new Error('伺服器回傳的資料格式不正確，請重新整理。')
    return value
  } catch (error) {
    if (controller.signal.aborted && !signal.aborted) throw new Error('連線逾時，請重新整理後再試。')
    throw error
  } finally {
    window.clearTimeout(timer)
    signal.removeEventListener('abort', abort)
  }
}

async function readDelivery(stamp: Stamp, signal: AbortSignal): Promise<Delivery | null> {
  try { return parseDelivery(await request('view_delivery', signal, stamp), stamp) }
  catch (error) {
    if (error instanceof HttpError && error.status === 404 && error.validErrorBody) return null
    throw error
  }
}

async function readOrder(stamp: Stamp, signal: AbortSignal) {
  const before = parseRows(await request('view_meals_of_order', signal, stamp), stamp)
  const delivery = await readDelivery(stamp, signal)
  const after = parseRows(await request('view_meals_of_order', signal, stamp), stamp)
  const deliveryAfter = await readDelivery(stamp, signal)
  if (JSON.stringify(before) !== JSON.stringify(after) || JSON.stringify(delivery) !== JSON.stringify(deliveryAfter)) throw invalid()
  const orderState = after[0]!.order_state
  if (orderState === 'processing' || orderState === 'completed') {
    if (!delivery || !positive(delivery.deliverer_id)) throw invalid()
  } else if (orderState !== 'cancelled' && delivery?.deliverer_id !== null && delivery?.deliverer_id !== undefined) throw invalid()
  return { rows: after, delivery, state: orderState }
}

async function loadSnapshot(role: Role, accountId: number, signal: AbortSignal): Promise<TrackedOrder[]> {
  const meals = unique(array(await request('view_all_meals', signal)).map(item => {
    if (!object(item) || !positive(item.meal_id) || !positive(item.restaurant_id) || !positive(item.price)
      || !text(item.meal_name)) throw invalid()
    return item as unknown as Meal
  }), meal => meal.meal_id)
  const restaurants = unique(array(await request('view_all_restaurants', signal)).map(item => {
    if (!object(item) || !positive(item.restaurant_id) || !text(item.restaurant_name)) throw invalid()
    return item as unknown as Restaurant
  }), restaurant => restaurant.restaurant_id)
  const stamps = parseStamps(await request('view_order_by_stamp', signal))
  const result: TrackedOrder[] = []
  for (const stamp of stamps) {
    if (role === 'buyer' && stamp.buyer_id !== accountId) continue
    if (role === 'deliverer') {
      const delivery = await readDelivery(stamp, signal)
      if (!delivery || delivery.deliverer_id !== accountId) continue
    }
    const snapshot = await readOrder(stamp, signal)
    if (role === 'deliverer' && snapshot.delivery?.deliverer_id !== accountId) continue
    const items = snapshot.rows.map(row => {
      const meal = meals.find(item => item.meal_id === row.meal_id)
      const restaurant = restaurants.find(item => item.restaurant_id === meal?.restaurant_id)
      if (!meal || !restaurant || !Number.isSafeInteger(meal.price * row.quantity)) throw invalid()
      return { name: meal.meal_name, restaurant: restaurant.restaurant_name, price: meal.price,
        quantity: row.quantity, note: row.note }
    })
    const total = items.reduce((sum, item) => sum + item.price * item.quantity, 0)
    if (!Number.isSafeInteger(total)) throw invalid()
    result.push({ ...stamp, address: snapshot.delivery?.pickup_address ?? '外送資訊尚未建立',
      deliverer_id: snapshot.delivery?.deliverer_id ?? null, deliveryMissing: !snapshot.delivery,
      state: snapshot.state, orderIds: snapshot.rows.map(row => row.order_id), items, total })
  }
  return result
}

export function useOrderTracking(role: Role, accountId: number | null) {
  const orders = ref<TrackedOrder[]>([])
  const loading = ref(false)
  const error = ref(accountId === null ? '示範帳號設定無效，請使用正整數帳號 ID。' : '')
  const notice = ref('')
  const pendingAction = ref('')
  const lastUpdated = ref('')
  const online = ref(navigator.onLine)
  const visible = ref(!document.hidden)
  const blocked = computed(() => accountId === null || loading.value || !!pendingAction.value || !!error.value || !online.value)
  let disposed = false
  let timer: number | undefined
  let readController: AbortController | undefined
  let actionController: AbortController | undefined
  const key = (order: Stamp) => `${order.buyer_id}/${order.timestamp}`
  const stopTimer = () => { if (timer !== undefined) window.clearTimeout(timer); timer = undefined }
  const active = () => !disposed && accountId !== null && online.value && visible.value

  function schedule() {
    stopTimer()
    if (active() && !loading.value && !pendingAction.value) timer = window.setTimeout(() => { void loadOrders() }, 15000)
  }

  async function loadOrders(afterAction = false): Promise<boolean> {
    if (disposed || accountId === null || loading.value || (pendingAction.value && !afterAction)) return false
    if (!online.value) { error.value = '目前離線，連線恢復後會更新訂單。'; return false }
    if (!visible.value && !afterAction) return false
    stopTimer()
    loading.value = true
    readController = new AbortController()
    try {
      const snapshot = await loadSnapshot(role, accountId, readController.signal)
      if (disposed || readController.signal.aborted) return false
      orders.value = snapshot
      error.value = ''
      lastUpdated.value = new Date().toLocaleTimeString('zh-TW', { hour12: false })
      return true
    } catch (cause) {
      if (!disposed && !readController.signal.aborted) {
        error.value = cause instanceof Error && !(cause instanceof TypeError) ? cause.message : '暫時無法取得訂單，請重新整理。'
      }
      return false
    } finally {
      readController = undefined
      loading.value = false
      schedule()
    }
  }

  async function changeOrder(order: TrackedOrder, action: OrderAction, question: string) {
    if (blocked.value || !orders.value.some(item => key(item) === key(order))) return
    const allowed = role === 'buyer'
      ? order.buyer_id === accountId && ((action === 'cancel' && order.state === 'pending') || (action === 'complete' && order.state === 'processing'))
      : order.deliverer_id === accountId && order.state === 'processing' && (action === 'complete' || action === 'abandon')
    if (!allowed || !window.confirm(question)) return
    stopTimer()
    pendingAction.value = key(order)
    notice.value = ''
    actionController = new AbortController()
    let uncertain = false
    try {
      const value = await request(`${action}_order`, actionController.signal, { buyer_id: order.buyer_id, timestamp: order.timestamp })
      if (!object(value) || value.success !== true || !sameStamp(value, order)) throw invalid()
      const expected = action === 'cancel' ? 'cancelled' : action === 'complete' ? 'completed' : 'pending'
      const responseField = action === 'cancel' ? 'cancelled_orders' : action === 'complete' ? 'completed_orders' : 'updated_orders'
      const changed = array(value[responseField])
      if (changed.some(row => !object(row) || !positive(row.order_id) || !positive(row.meal_id)
        || !positive(row.quantity) || row.order_state !== expected)
        || JSON.stringify(changed.map(row => (row as JsonObject).order_id).sort((a, b) => Number(a) - Number(b))) !== JSON.stringify(order.orderIds)
        || (action === 'abandon' && value.deliverer_id !== null)) throw invalid()
      notice.value = action === 'cancel' ? '訂單已取消。' : action === 'complete' ? '訂單已完成。' : '已放棄承接這筆訂單。'
    } catch (cause) {
      if (disposed) return
      uncertain = !(cause instanceof HttpError && cause.status >= 400 && cause.status < 500)
      notice.value = uncertain ? '還無法確認操作結果，正在重新查詢；請勿重複送出。'
        : cause instanceof Error ? cause.message : '這次操作未完成，請查看最新訂單狀態。'
    } finally {
      actionController = undefined
      if (!disposed) {
        if (uncertain) {
          const checkController = new AbortController()
          readController = checkController
          try {
            const snapshot = await readOrder(order, checkController.signal)
            const expected = action === 'cancel' ? 'cancelled' : action === 'complete' ? 'completed' : 'pending'
            const confirmed = snapshot.state === expected && (action !== 'abandon' || snapshot.delivery?.deliverer_id === null)
              && JSON.stringify(snapshot.rows.map(row => row.order_id)) === JSON.stringify(order.orderIds)
            notice.value = confirmed ? '已重新查詢，確認操作完成。' : '已重新查詢，尚未確認這次操作完成；請先查看最新狀態。'
          } catch {
            notice.value = '操作結果仍未確認，請重新整理成功後再查看，避免重複送出。'
          } finally { readController = undefined }
        }
        await loadOrders(true)
        pendingAction.value = ''
        schedule()
      }
    }
  }

  function resume() {
    online.value = navigator.onLine
    visible.value = !document.hidden
    stopTimer()
    if (!online.value) error.value = '目前離線，連線恢復後會更新訂單。'
    if (!active()) {
      if (!pendingAction.value) readController?.abort()
    } else if (!loading.value && !pendingAction.value) void loadOrders()
  }

  onMounted(() => {
    document.addEventListener('visibilitychange', resume)
    window.addEventListener('online', resume)
    window.addEventListener('offline', resume)
    resume()
  })
  onUnmounted(() => {
    disposed = true
    stopTimer()
    readController?.abort()
    actionController?.abort()
    document.removeEventListener('visibilitychange', resume)
    window.removeEventListener('online', resume)
    window.removeEventListener('offline', resume)
  })
  return { orders, loading, error, notice, pendingAction, lastUpdated, online, blocked, loadOrders, changeOrder, key }
}
