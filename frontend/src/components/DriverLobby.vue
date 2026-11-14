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

interface OrderMeal {
  meal_id: number
  quantity: number
  note: string | null
  order_state: string
}

interface Delivery {
  pickup_address: string
  deliverer_id: number | null
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

const orders = ref<LobbyOrder[]>([])
const loading = ref(true)
const error = ref('')
const notice = ref('')
const noticeKind = ref<'success' | 'error' | 'unknown'>('success')
const acceptingKey = ref<string | null>(null)
let loadSequence = 0
let loadController: AbortController | undefined
let acceptController: AbortController | undefined

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
    && typeof value.price === 'number' && Number.isFinite(value.price) && value.price >= 0
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
  return isRecord(value)
    && isPositiveInteger(value.meal_id)
    && isPositiveInteger(value.quantity)
    && (value.note === null || typeof value.note === 'string')
    && typeof value.order_state === 'string'
}

function isDelivery(value: unknown): value is Delivery {
  return isRecord(value)
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

async function loadOrders() {
  const sequence = ++loadSequence
  loadController?.abort()
  const controller = new AbortController()
  loadController = controller
  loading.value = true
  error.value = ''

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

    for (const stamp of stamps) {
      let orderMeals: OrderMeal[]
      let deliveries: Delivery[]
      try {
        orderMeals = listOf(
          await requestJson('/api/view_meals_of_order', postJson(stamp, controller.signal)),
          isOrderMeal, '訂單餐點資料',
        )
        // 訂單可能已被刪除或承接；整單都必須仍在等待接單。
        if (orderMeals.length === 0 || !orderMeals.every(item => item.order_state === 'pending')) continue
        deliveries = listOf(
          await requestJson('/api/view_delivery', postJson(stamp, controller.signal)),
          isDelivery, '送餐資料',
        )
      } catch (e) {
        // 缺少 Delivery 的訂單沿用既有規則：不列入待接單。
        if (e instanceof ApiError && e.status === 404 && e.hasJsonBody) continue
        throw e
      }

      const delivery = deliveries[0]
      if (!delivery || delivery.deliverer_id !== null) continue
      if (deliveries.length !== 1) throw new ResponseError('同一訂單有多筆送餐資料，請確認後端資料。')

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

      result.push({
        ...stamp,
        address: delivery.pickup_address,
        items,
        total: items.reduce((sum, item) => sum + item.price * item.quantity, 0),
      })
    }

    if (sequence === loadSequence) orders.value = result
  } catch (e) {
    if (sequence !== loadSequence || controller.signal.aborted) return
    error.value = e instanceof ApiError || e instanceof ResponseError
      ? e.message
      : '無法載入待接訂單，請確認後端是否啟動，再重新整理。'
  } finally {
    if (sequence === loadSequence) loading.value = false
  }
}

async function acceptOrder(order: LobbyOrder) {
  if (acceptingKey.value !== null || loading.value || !validDeliverer) return
  acceptingKey.value = orderKey(order)
  notice.value = ''
  // 接單後只採用重新讀取的結果，避免先前的載入覆蓋清單。
  ++loadSequence
  loadController?.abort()
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
    if (!controller.signal.aborted) await loadOrders()
    acceptingKey.value = null
    acceptController = undefined
  }
}

onMounted(loadOrders)
onBeforeUnmount(() => {
  ++loadSequence
  loadController?.abort()
  acceptController?.abort()
})
</script>

<template>
  <div class="page">
    <h1>外送員接單大廳</h1>
    <p class="demo-account" v-if="validDeliverer">示範帳號：外送員 #{{ DELIVERER_ID }}（尚未串接登入）</p>
    <p class="notice error-notice" v-else>示範外送員帳號設定無效，請確認 VITE_DELIVERER_ID 是正整數。</p>

    <div class="toolbar">
      <span>待接訂單 {{ orders.length }} 筆</span>
      <button :disabled="loading || acceptingKey !== null" @click="loadOrders">重新整理</button>
    </div>

    <p v-if="notice" class="notice" :class="noticeKind" role="status" aria-live="polite">{{ notice }}</p>

    <div v-if="loading" class="message">載入中……</div>

    <div v-else-if="error" class="message" role="alert">{{ error }}</div>

    <div v-else-if="orders.length === 0" class="message">
      目前沒有待接訂單
    </div>

    <div v-else>
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
            :disabled="acceptingKey !== null || loading || !validDeliverer"
            @click="acceptOrder(order)"
          >
            {{ acceptingKey === orderKey(order) ? '接單中……' : '接單' }}
          </button>
        </div>
      </div>
    </div>
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

.notice.success { color: #215c36; }
.notice.error, .error-notice { color: #992e25; }
.notice.unknown { color: #79500b; }

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

.order {
  background: white;
  padding: 20px;
  border: 1px solid #ddd;
  border-radius: 10px;
  margin-bottom: 15px;
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
  padding: 5px 0;
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
