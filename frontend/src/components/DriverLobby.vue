<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

interface Meal {
  meal_id: number
  meal_name: string
  restaurant_id: number
  price: number
}

interface Restaurant {
  restaurant_id: number
  restaurant_name: string
  restaurant_address: string
}

interface OrderStamp {
  buyer_id: string | number
  timestamp: string
}

type OrderState = 'in_cart' | 'pending' | 'processing' | 'completed' | 'cancelled'

interface OrderMeal extends OrderStamp {
  meal_id: number
  quantity: number
  note: string | null
  order_state: OrderState
}

interface Delivery extends OrderStamp {
  pickup_address: string
  deliverer_id: number | null
}

interface OrderDetails {
  meals: OrderMeal[]
  delivery: Delivery
}

interface LobbyItem {
  name: string
  restaurant: string
  price: number
  quantity: number
  note: string | null
}

interface LobbyOrder extends OrderStamp {
  address: string
  items: LobbyItem[]
  total: number
}

// 延續目前的示範帳號；登入串接完成後改由登入狀態提供。
const DELIVERER_ID = Number(import.meta.env.VITE_DELIVERER_ID ?? '1')
const validDeliverer = isPositiveInteger(DELIVERER_ID)
const REQUEST_TIMEOUT_MS = 15000
const REFRESH_INTERVAL_MS = 15000

const orders = ref<LobbyOrder[]>([])
const acceptedOrders = ref<LobbyOrder[]>([])
const loading = ref(true)
const refreshing = ref(false)
const hasLoaded = ref(false)
const lastUpdated = ref('')
const online = ref(navigator.onLine)
const error = ref('')
const notice = ref('')
const noticeKind = ref<'success' | 'error' | 'unknown'>('success')
const acceptingKey = ref<string | null>(null)
let loadSequence = 0
let loadController: AbortController | undefined
let acceptController: AbortController | undefined
let refreshTimer: number | undefined
let disposed = false

function clearRefreshTimer() {
  window.clearTimeout(refreshTimer)
  refreshTimer = undefined
}

function scheduleRefresh() {
  clearRefreshTimer()
  if (disposed || document.visibilityState !== 'visible' || !navigator.onLine
    || refreshing.value || acceptingKey.value !== null) return
  // 等這輪查完再計時，慢連線時不會一直重送查詢。
  refreshTimer = window.setTimeout(() => { void loadOrders() }, REFRESH_INTERVAL_MS)
}

function resumeRefresh() {
  online.value = navigator.onLine
  clearRefreshTimer()
  if (!disposed && document.visibilityState === 'visible' && online.value
    && !refreshing.value && acceptingKey.value === null) void loadOrders()
}

class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly hasJsonBody = false) {
    super(message)
  }
}

class ResponseError extends Error {}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isPositiveInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0
}

function isMeal(value: unknown): value is Meal {
  return isRecord(value)
    && isPositiveInteger(value.meal_id)
    && typeof value.meal_name === 'string'
    && isPositiveInteger(value.restaurant_id)
    && typeof value.price === 'number' && Number.isSafeInteger(value.price) && value.price >= 0
}

function isRestaurant(value: unknown): value is Restaurant {
  return isRecord(value)
    && isPositiveInteger(value.restaurant_id)
    && typeof value.restaurant_name === 'string'
    && typeof value.restaurant_address === 'string'
}

function isOrderStamp(value: unknown): value is OrderStamp {
  return isRecord(value)
    && (isPositiveInteger(value.buyer_id)
      || (typeof value.buyer_id === 'string' && /^[1-9]\d*$/.test(value.buyer_id)))
    && typeof value.timestamp === 'string'
    && /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(value.timestamp)
}

function isOrderMeal(value: unknown): value is OrderMeal {
  return isRecord(value) && isOrderStamp(value)
    && isPositiveInteger(value.meal_id)
    && isPositiveInteger(value.quantity)
    && (value.note === null || typeof value.note === 'string')
    && typeof value.order_state === 'string'
    && ['in_cart', 'pending', 'processing', 'completed', 'cancelled'].includes(value.order_state)
}

function isDelivery(value: unknown): value is Delivery {
  return isRecord(value) && isOrderStamp(value)
    && typeof value.pickup_address === 'string' && value.pickup_address.trim().length > 0
    && (value.deliverer_id === null || isPositiveInteger(value.deliverer_id))
}

function listOf<T>(value: unknown, guard: (item: unknown) => item is T, label: string): T[] {
  if (!Array.isArray(value) || !value.every(guard)) {
    throw new ResponseError(`${label}格式不正確，請確認後端資料。`)
  }
  return value
}

function responseMessage(value: unknown, fallback: string): string {
  if (isRecord(value)) {
    if (typeof value.message === 'string' && value.message.trim()) return value.message
    if (typeof value.error === 'string' && value.error.trim()) return value.error
  }
  return fallback
}

async function requestJson(path: string, options: RequestInit = {}): Promise<unknown> {
  const controller = new AbortController()
  const abort = () => controller.abort()
  if (options.signal?.aborted) controller.abort()
  options.signal?.addEventListener('abort', abort, { once: true })
  const timeout = window.setTimeout(abort, REQUEST_TIMEOUT_MS)

  try {
    const response = await fetch(path, { ...options, signal: controller.signal })
    let result: unknown
    try {
      result = await response.json()
    } catch {
      if (!response.ok) throw new ApiError(`伺服器回應錯誤（${response.status}）。`, response.status)
      throw new ResponseError('伺服器未回傳有效的 JSON 資料。')
    }
    if (!response.ok) {
      throw new ApiError(responseMessage(result, `請求失敗（${response.status}）。`), response.status, isRecord(result))
    }
    return result
  } finally {
    window.clearTimeout(timeout)
    options.signal?.removeEventListener('abort', abort)
  }
}

function postJson(stamp: OrderStamp, signal: AbortSignal): RequestInit {
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(stamp),
    signal,
  }
}

function orderKey(order: OrderStamp): string {
  return `${order.buyer_id}:${order.timestamp}`
}

function matchesOrder(value: OrderStamp, stamp: OrderStamp): boolean {
  return String(value.buyer_id) === String(stamp.buyer_id) && value.timestamp === stamp.timestamp
}

async function readOrderDetails(stamp: OrderStamp, signal: AbortSignal): Promise<OrderDetails | null> {
  try {
    const meals = listOf(
      await requestJson('/api/view_meals_of_order', postJson(stamp, signal)),
      isOrderMeal, '訂單餐點資料',
    )
    if (!meals.every(item => matchesOrder(item, stamp))) {
      throw new ResponseError('餐點資料與查詢的訂單不一致，請重新整理。')
    }
    if (meals.length === 0
      || (!meals.every(item => item.order_state === 'pending')
        && !meals.every(item => item.order_state === 'processing'))) return null

    const deliveries = listOf(
      await requestJson('/api/view_delivery', postJson(stamp, signal)),
      isDelivery, '送餐資料',
    )
    if (!deliveries.every(item => matchesOrder(item, stamp))) {
      throw new ResponseError('送餐資料與查詢的訂單不一致，請重新整理。')
    }
    const delivery = deliveries[0]
    if (!delivery) return null
    if (deliveries.length !== 1) throw new ResponseError('同一訂單有多筆送餐資料，請確認後端資料。')
    return { meals, delivery }
  } catch (e) {
    // 缺少 Delivery 的訂單沿用既有規則；其他錯誤不能當成空清單。
    if (e instanceof ApiError && e.status === 404 && e.hasJsonBody) return null
    throw e
  }
}

function hasConsistentAssignment(details: OrderDetails): boolean {
  return details.meals.every(item => item.order_state === 'pending')
    ? details.delivery.deliverer_id === null
    : details.delivery.deliverer_id !== null
}

async function loadOrders() {
  if (disposed || refreshing.value) return
  clearRefreshTimer()
  const sequence = ++loadSequence
  loadController?.abort()
  const controller = new AbortController()
  loadController = controller
  refreshing.value = true
  loading.value = !hasLoaded.value

  try {
    const meals = listOf(
      await requestJson('/api/view_all_meals', { signal: controller.signal }), isMeal, '餐點資料',
    )
    const restaurants = listOf(
      await requestJson('/api/view_all_restaurants', { signal: controller.signal }),
      isRestaurant, '餐廳資料',
    )
    const stamps = listOf(
      await requestJson('/api/view_order_by_stamp', { signal: controller.signal }),
      isOrderStamp, '訂單資料',
    )
    const result: LobbyOrder[] = []
    const acceptedResult: LobbyOrder[] = []

    for (const stamp of stamps) {
      let details = await readOrderDetails(stamp, controller.signal)
      if (!details) continue
      // 兩次查詢之間可能剛好有人接單或棄單，只重查一次確認。
      if (!hasConsistentAssignment(details)) {
        details = await readOrderDetails(stamp, controller.signal)
        if (!details || !hasConsistentAssignment(details)) {
          throw new ResponseError('訂單狀態與外送員資料尚未一致，請重新整理後再操作。')
        }
      }
      const { meals: orderMeals, delivery } = details
      const isPending = orderMeals.every(item => item.order_state === 'pending')
        && delivery.deliverer_id === null
      const isAcceptedByMe = validDeliverer
        && orderMeals.every(item => item.order_state === 'processing')
        && delivery.deliverer_id === DELIVERER_ID
      if (!isPending && !isAcceptedByMe) continue

      const items = orderMeals.map(orderMeal => {
        const meal = meals.find(item => item.meal_id === orderMeal.meal_id)
        const restaurant = meal && restaurants.find(item => item.restaurant_id === meal.restaurant_id)
        if (!meal || !restaurant) throw new ResponseError('訂單中的餐點或餐廳資料不完整，請重新整理。')
        return {
          name: meal.meal_name,
          restaurant: restaurant.restaurant_name,
          price: meal.price,
          quantity: orderMeal.quantity,
          note: orderMeal.note,
        }
      })

      const total = items.reduce((sum, item) => {
        const subtotal = item.price * item.quantity
        if (!Number.isSafeInteger(subtotal) || !Number.isSafeInteger(sum + subtotal)) {
          throw new ResponseError('訂單金額超出可正確計算的範圍，請確認餐點價格與數量。')
        }
        return sum + subtotal
      }, 0)
      const target = isPending ? result : acceptedResult
      target.push({
        ...stamp,
        address: delivery.pickup_address,
        items,
        total,
      })
    }

    if (sequence === loadSequence) {
      orders.value = result
      acceptedOrders.value = acceptedResult
      hasLoaded.value = true
      error.value = ''
      lastUpdated.value = new Date().toLocaleTimeString('zh-TW', { hour12: false })
    }
  } catch (e) {
    if (sequence !== loadSequence || controller.signal.aborted) return
    error.value = e instanceof ApiError || e instanceof ResponseError
      ? e.message
      : '暫時連不上伺服器，稍後會再試一次，也可以按重新整理。'
  } finally {
    if (sequence === loadSequence) {
      loading.value = false
      refreshing.value = false
      scheduleRefresh()
    }
  }
}

async function acceptOrder(order: LobbyOrder) {
  if (disposed || acceptingKey.value !== null || loading.value || error.value
    || !online.value || !validDeliverer) return
  clearRefreshTimer()
  acceptingKey.value = orderKey(order)
  notice.value = ''
  // 接單後只採用重新讀取的結果，避免先前的載入覆蓋清單。
  ++loadSequence
  loadController?.abort()
  refreshing.value = false
  const controller = new AbortController()
  acceptController = controller

  try {
    const result = await requestJson('/api/process_order', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        buyer_id: order.buyer_id,
        timestamp: order.timestamp,
        deliverer_id: DELIVERER_ID,
      }),
      signal: controller.signal,
    })

    if (!isRecord(result) || result.success !== true
      || String(result.buyer_id) !== String(order.buyer_id)
      || result.timestamp !== order.timestamp || result.deliverer_id !== DELIVERER_ID) {
      throw new ResponseError('接單回應資料不完整。')
    }
    noticeKind.value = 'success'
    notice.value = '接單成功！'
  } catch (e) {
    if (controller.signal.aborted) return
    if (e instanceof ApiError && e.status >= 400 && e.status < 500 && e.hasJsonBody) {
      noticeKind.value = 'error'
      notice.value = e.message
    } else {
      // 中斷或無法解析回應時，伺服器仍可能已成功；不自動重送 POST。
      noticeKind.value = 'unknown'
      notice.value = '尚無法確認接單結果，請先查看更新後的清單。若清單未能更新，請重新整理後再操作。'
    }
  } finally {
    if (!disposed && !controller.signal.aborted) await loadOrders()
    acceptingKey.value = null
    acceptController = undefined
    scheduleRefresh()
  }
}

onMounted(() => {
  document.addEventListener('visibilitychange', resumeRefresh)
  window.addEventListener('online', resumeRefresh)
  window.addEventListener('offline', resumeRefresh)
  void loadOrders()
})
onBeforeUnmount(() => {
  disposed = true
  clearRefreshTimer()
  document.removeEventListener('visibilitychange', resumeRefresh)
  window.removeEventListener('online', resumeRefresh)
  window.removeEventListener('offline', resumeRefresh)
  ++loadSequence
  loadController?.abort()
  acceptController?.abort()
})
</script>

<template>
  <div class="page">
    <h1>外送員接單大廳</h1>
    <nav class="page-links" aria-label="頁面導覽">
      <a href="/deliverer-orders.html">我的外送單</a>
      <a href="/">返回點餐</a>
    </nav>
    <p class="demo-account" v-if="validDeliverer">示範帳號：外送員 #{{ DELIVERER_ID }}（尚未串接登入）</p>
    <p class="notice error-notice" v-else>示範外送員帳號設定無效，請確認 VITE_DELIVERER_ID 是正整數。</p>

    <div class="toolbar">
      <span>待接訂單 {{ orders.length }} 筆</span>
      <button :disabled="refreshing || acceptingKey !== null" @click="loadOrders">重新整理</button>
    </div>
    <p class="refresh-status" data-testid="refresh-status">
      <span v-if="!online">目前離線，恢復連線後會自動更新。</span>
      <span v-else-if="refreshing && hasLoaded">正在更新訂單……</span>
      <span v-else>每 15 秒自動更新，也可以手動重新整理。</span>
      <span v-if="lastUpdated">上次更新：{{ lastUpdated }}</span>
    </p>

    <p v-if="notice" class="notice" :class="noticeKind" role="status" aria-live="polite">{{ notice }}</p>

    <div v-if="loading" class="message">載入中……</div>

    <div v-if="error" class="notice error-notice" role="alert">
      {{ error }}
      <span v-if="hasLoaded">目前顯示上次的清單，更新成功後就能繼續接單。</span>
    </div>

    <div v-if="!loading && !error && orders.length === 0" class="message">
      目前沒有待接訂單
    </div>

    <div v-if="!loading && orders.length > 0">
      <div
        v-for="order in orders"
        :key="orderKey(order)"
        class="order"
      >
        <div class="order-title">
          <strong>買家 #{{ order.buyer_id }}</strong>
          <span class="time">{{ order.timestamp }}</span>
        </div>

        <p>送餐地址：{{ order.address }}</p>

        <div v-for="(item, index) in order.items" :key="index">
          <div class="item">
            <span>{{ item.name }} × {{ item.quantity }}（{{ item.restaurant }}）</span>
            <span>NT$ {{ item.price * item.quantity }}</span>
          </div>
          <p v-if="item.note" class="note">備註：{{ item.note }}</p>
        </div>

        <div class="bottom">
          <span class="total">餐點小計：NT$ {{ order.total }}</span>
          <button
            class="accept"
            :disabled="acceptingKey !== null || loading || !!error || !online || !validDeliverer"
            @click="acceptOrder(order)"
          >
            {{ acceptingKey === orderKey(order) ? '接單中……' : '接單' }}
          </button>
        </div>
      </div>
    </div>

    <section
      v-if="hasLoaded && validDeliverer"
      class="accepted-section"
      data-testid="accepted-orders"
      aria-labelledby="accepted-orders-title"
    >
      <div class="section-heading">
        <h2 id="accepted-orders-title">我的已接訂單</h2>
        <span>共 {{ acceptedOrders.length }} 筆</span>
      </div>

      <div v-if="acceptedOrders.length === 0" class="message">目前沒有已接訂單</div>

      <div
        v-for="order in acceptedOrders"
        :key="orderKey(order)"
        class="accepted-order"
        data-testid="accepted-order"
        :data-order-key="orderKey(order)"
      >
        <div class="order-title">
          <strong>買家 #{{ order.buyer_id }}</strong>
          <span class="time">{{ order.timestamp }}</span>
        </div>

        <p>送餐地址：{{ order.address }}</p>

        <div v-for="(item, index) in order.items" :key="index">
          <div class="item">
            <span>{{ item.name }} × {{ item.quantity }}（{{ item.restaurant }}）</span>
            <span>NT$ {{ item.price * item.quantity }}</span>
          </div>
          <p v-if="item.note" class="note">備註：{{ item.note }}</p>
        </div>

        <div class="bottom">
          <span class="total">餐點小計：NT$ {{ order.total }}</span>
          <span class="accepted-status">已接單</span>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.page {
  max-width: 800px;
  margin: 0 auto;
  padding: 20px;
  color: #333;
}

h1 {
  text-align: center;
}

.demo-account {
  color: #666;
  font-size: 14px;
  text-align: center;
}

.page-links { display: flex; justify-content: center; gap: 20px; margin: 12px 0; }
.page-links a { color: #37506a; }

.notice.success { color: #215c36; }
.notice.error, .error-notice { color: #992e25; }
.notice.unknown { color: #79500b; }

.refresh-status {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 16px;
  color: #666;
  font-size: 14px;
  margin: -5px 0 15px;
}

.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 15px;
}

.notice {
  padding: 10px;
  background: #eee;
  border-radius: 5px;
}

.message {
  text-align: center;
  color: #888;
  padding: 30px;
}

.order, .accepted-order {
  background: white;
  padding: 20px;
  border: 1px solid #ddd;
  border-radius: 10px;
  margin-bottom: 15px;
  overflow-wrap: anywhere;
}

.accepted-section {
  border-top: 1px solid #ddd;
  margin-top: 30px;
  padding-top: 20px;
}

.section-heading {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 15px;
}

.section-heading h2 {
  margin: 0;
  font-size: 22px;
}

.accepted-status {
  color: #215c36;
  font-weight: bold;
}

.order-title {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 8px;
  border-bottom: 1px solid #ddd;
  padding-bottom: 10px;
}

.time {
  color: #888;
}

.item {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 5px 0;
}

.item > span:first-child {
  min-width: 0;
}

.item > span:last-child {
  flex-shrink: 0;
  white-space: nowrap;
}

.note {
  margin: 0 0 5px;
  color: #888;
  font-size: 14px;
}

.bottom {
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-top: 1px solid #ddd;
  margin-top: 10px;
  padding-top: 15px;
}

.total {
  font-size: 18px;
  font-weight: bold;
}

button {
  border: none;
  border-radius: 5px;
  padding: 8px 12px;
  cursor: pointer;
  background: #ddd;
}

button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.accept {
  background: #333;
  color: white;
  padding: 10px 20px;
}
</style>
