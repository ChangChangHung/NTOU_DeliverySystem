<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'

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
  buyer_id: string
  timestamp: string
}

interface OrderMeal {
  meal_id: number
  quantity: number
  note: string | null
  order_state: string
}

interface OrderItem {
  name: string
  restaurant: string
  price: number
  quantity: number
  note: string | null
}

interface MyOrder {
  buyer_id: string
  timestamp: string
  address: string
  deliverer_id: number | null
  state: string
  items: OrderItem[]
  total: number
}

// 登入還沒做好，用 VITE_BUYER_ID 設定買家帳號，沒設定就用 2
const BUYER_ID = Number(import.meta.env.VITE_BUYER_ID ?? '2')

const stateText: Record<string, string> = {
  in_cart: '購物車中',
  pending: '待接單',
  processing: '外送中',
  completed: '已完成',
  cancelled: '已取消',
}

const orders = ref<MyOrder[]>([])

const loading = ref(true)
const error = ref('')
const notice = ref('')

async function loadOrders() {
  loading.value = true
  error.value = ''

  try {
    const mealResponse = await fetch('/api/view_all_meals')
    const meals: Meal[] = await mealResponse.json()

    const restaurantResponse = await fetch('/api/view_all_restaurants')
    const restaurants: Restaurant[] = await restaurantResponse.json()

    const stampResponse = await fetch('/api/view_order_by_stamp')
    if (!stampResponse.ok) throw new Error('無法取得訂單')

    const stamps: OrderStamp[] = await stampResponse.json()
    const result: MyOrder[] = []

    for (const stamp of stamps) {
      // 只留自己下的單
      if (Number(stamp.buyer_id) !== BUYER_ID) continue

      const mealsOfOrderResponse = await fetch('/api/view_meals_of_order', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(stamp),
      })
      const orderMeals: OrderMeal[] = await mealsOfOrderResponse.json()

      // 查送餐地址和外送員，還沒建立外送資料的訂單就先空著
      const deliveryResponse = await fetch('/api/view_delivery', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(stamp),
      })

      let address = '未填寫'
      let delivererId: number | null = null

      if (deliveryResponse.ok) {
        const delivery = await deliveryResponse.json()
        address = delivery[0].pickup_address
        delivererId = delivery[0].deliverer_id
      }

      const items = orderMeals.map(orderMeal => {
        const meal = meals.find(m => m.meal_id === orderMeal.meal_id)!
        const restaurant = restaurants.find(
          r => r.restaurant_id === meal.restaurant_id,
        )!

        return {
          name: meal.meal_name,
          restaurant: restaurant.restaurant_name,
          price: meal.price,
          quantity: orderMeal.quantity,
          note: orderMeal.note,
        }
      })

      result.push({
        buyer_id: stamp.buyer_id,
        timestamp: stamp.timestamp,
        address,
        deliverer_id: delivererId,
        state: orderMeals[0]!.order_state,
        items,
        total: items.reduce(
          (sum, item) => sum + item.price * item.quantity,
          0,
        ),
      })
    }

    orders.value = result
  } catch {
    error.value = '無法連接後端，請確認 Python 伺服器是否啟動。'
  } finally {
    loading.value = false
  }
}

// 取消訂單、完成訂單都用這個，url 傳要呼叫的 API
async function changeOrder(order: MyOrder, url: string, question: string) {
  if (!confirm(question)) return

  notice.value = ''

  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        buyer_id: order.buyer_id,
        timestamp: order.timestamp,
      }),
    })

    const result = await response.json()

    if (!response.ok) {
      throw new Error(result.message ? String(result.message) : '操作失敗')
    }

    notice.value = String(result.message)
  } catch (e) {
    notice.value = e instanceof Error ? e.message : '操作失敗，請稍後再試。'
  }

  await loadOrders()
}

// 每 15 秒自動重新整理一次，才看得到對方改過的狀態
let timer = 0

onMounted(() => {
  loadOrders()
  timer = window.setInterval(() => {
    if (!loading.value) loadOrders()
  }, 15000)
})

onUnmounted(() => window.clearInterval(timer))
</script>

<template>
  <div class="page">
    <h1>我的訂單</h1>
    <p class="account">示範帳號：買家 #{{ BUYER_ID }}（尚未串接登入）</p>

    <div class="toolbar">
      <span>共 {{ orders.length }} 筆</span>
      <button :disabled="loading" @click="loadOrders">重新整理</button>
    </div>

    <p v-if="notice" class="notice">{{ notice }}</p>

    <div v-if="loading && orders.length === 0" class="message">載入中……</div>

    <div v-else-if="error" class="message">{{ error }}</div>

    <div v-else-if="orders.length === 0" class="message">
      目前沒有訂單
    </div>

    <div v-else>
      <div
        v-for="order in orders"
        :key="order.timestamp"
        class="order"
      >
        <div class="order-title">
          <strong>{{ order.timestamp }}</strong>
          <span class="state">{{ stateText[order.state] }}</span>
        </div>

        <p>送餐地址：{{ order.address }}</p>
        <p v-if="order.deliverer_id">外送員：#{{ order.deliverer_id }}</p>

        <div v-for="(item, index) in order.items" :key="index">
          <div class="item">
            <span>{{ item.name }} × {{ item.quantity }}（{{ item.restaurant }}）</span>
            <span>NT$ {{ item.price * item.quantity }}</span>
          </div>
          <p v-if="item.note" class="note">備註：{{ item.note }}</p>
        </div>

        <div class="bottom">
          <span class="total">總金額：NT$ {{ order.total }}</span>

          <button
            v-if="order.state === 'pending'"
            @click="changeOrder(order, '/api/cancel_order', '確定要取消這筆訂單嗎？')"
          >
            取消訂單
          </button>

          <button
            v-if="order.state === 'processing'"
            class="primary"
            @click="changeOrder(order, '/api/complete_order', '確定已經收到餐點，要完成這筆訂單嗎？')"
          >
            已收到餐點
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

.account {
  text-align: center;
  color: #888;
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
  border-bottom: 1px solid #ddd;
  padding-bottom: 10px;
}

.state {
  font-weight: bold;
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

.primary {
  background: #333;
  color: white;
  padding: 10px 20px;
}
</style>
