<script setup lang="ts">
import { onMounted, ref } from 'vue'

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

interface LobbyItem {
  name: string
  restaurant: string
  price: number
  quantity: number
  note: string | null
}

interface LobbyOrder {
  buyer_id: string
  timestamp: string
  address: string
  items: LobbyItem[]
  total: number
}

// 登入還沒做好，先固定用這個外送員帳號接單
const DELIVERER_ID = 1

const orders = ref<LobbyOrder[]>([])

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
    const result: LobbyOrder[] = []

    for (const stamp of stamps) {
      // 查這筆訂單的餐點，只留還沒被接的
      const mealsOfOrderResponse = await fetch('/api/view_meals_of_order', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(stamp),
      })
      const orderMeals: OrderMeal[] = await mealsOfOrderResponse.json()

      if (orderMeals[0]!.order_state !== 'pending') continue

      // 查送餐地址，沒有外送資料的訂單不能接，先不顯示
      const deliveryResponse = await fetch('/api/view_delivery', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(stamp),
      })
      if (!deliveryResponse.ok) continue

      const delivery = await deliveryResponse.json()

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
        address: delivery[0].pickup_address,
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

async function acceptOrder(order: LobbyOrder) {
  notice.value = ''

  try {
    const response = await fetch('/api/process_order', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        buyer_id: order.buyer_id,
        timestamp: order.timestamp,
        deliverer_id: DELIVERER_ID,
      }),
    })

    const result = await response.json()

    if (!response.ok) {
      throw new Error(result.message ? String(result.message) : '接單失敗')
    }

    notice.value = '接單成功！'
  } catch (e) {
    notice.value = e instanceof Error ? e.message : '接單失敗，請稍後再試。'
  }

  await loadOrders()
}

onMounted(loadOrders)
</script>

<template>
  <div class="page">
    <h1>外送員接單大廳</h1>

    <div class="toolbar">
      <span>待接訂單 {{ orders.length }} 筆</span>
      <button @click="loadOrders">重新整理</button>
    </div>

    <p v-if="notice" class="notice">{{ notice }}</p>

    <div v-if="loading" class="message">載入中……</div>

    <div v-else-if="error" class="message">{{ error }}</div>

    <div v-else-if="orders.length === 0" class="message">
      目前沒有待接訂單
    </div>

    <div v-else>
      <div
        v-for="order in orders"
        :key="order.buyer_id + order.timestamp"
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
          <span class="total">總金額：NT$ {{ order.total }}</span>
          <button class="accept" @click="acceptOrder(order)">接單</button>
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

.accept {
  background: #333;
  color: white;
  padding: 10px 20px;
}
</style>
