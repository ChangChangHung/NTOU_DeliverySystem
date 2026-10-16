
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

interface Restaurant {
  id: number
  name: string
  description: string
}

interface MenuItem {
  id: number
  name: string
  price: number
}

interface CartItem extends MenuItem {
  quantity: number
}

const restaurants = ref<Restaurant[]>([])
const selectedRestaurant = ref<Restaurant | null>(null)
const menuItems = ref<MenuItem[]>([])
const cart = ref<CartItem[]>([])

const loading = ref(true)
const menuLoading = ref(false)
const submitting = ref(false)
const address = ref('')
const error = ref('')
const notice = ref('')

const cartCount = computed(() =>
  cart.value.reduce((sum, item) => sum + item.quantity, 0),
)

const total = computed(() =>
  cart.value.reduce(
    (sum, item) => sum + item.price * item.quantity,
    0,
  ),
)

async function loadRestaurants() {
  loading.value = true
  error.value = ''

  try {
    const response = await fetch('/api/restaurants')
    if (!response.ok) throw new Error('無法取得餐廳資料')

    restaurants.value = await response.json()
  } catch {
    error.value = '無法連接後端，請確認 Python 伺服器是否啟動。'
  } finally {
    loading.value = false
  }
}

async function openMenu(restaurant: Restaurant) {
  selectedRestaurant.value = restaurant
  menuLoading.value = true
  error.value = ''
  notice.value = ''

  try {
    const response = await fetch(
      `/api/restaurants/${restaurant.id}/menu`,
    )
    if (!response.ok) throw new Error('無法取得菜單')

    menuItems.value = await response.json()
  } catch {
    error.value = '菜單載入失敗，請稍後再試。'
  } finally {
    menuLoading.value = false
  }
}

function backToRestaurants() {
  selectedRestaurant.value = null
  menuItems.value = []
  error.value = ''
  notice.value = ''
}

function addToCart(item: MenuItem) {
  const existing = cart.value.find(
    cartItem => cartItem.id === item.id,
  )

  if (existing) {
    if (existing.quantity >= 99) {
      notice.value = '單一餐點最多購買 99 份。'
      return
    }
    existing.quantity++
  } else {
    cart.value.push({ ...item, quantity: 1 })
  }

  notice.value = `已將「${item.name}」加入購物車！`
}

function changeQuantity(itemId: number, change: number) {
  const item = cart.value.find(cartItem => cartItem.id === itemId)
  if (!item) return

  const nextQuantity = item.quantity + change

  if (nextQuantity <= 0) {
    cart.value = cart.value.filter(
      cartItem => cartItem.id !== itemId,
    )
    return
  }

  if (nextQuantity > 99) return

  item.quantity = nextQuantity
}

async function submitOrder() {
  error.value = ''
  notice.value = ''

  if (!selectedRestaurant.value) {
    error.value = '請先選擇餐廳。'
    return
  }

  if (cart.value.length === 0) {
    error.value = '購物車還沒有餐點。'
    return
  }

  if (!address.value.trim()) {
    error.value = '請輸入送餐地址。'
    return
  }

  submitting.value = true

  try {
    const response = await fetch('/api/orders', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        restaurant_id: selectedRestaurant.value.id,
        address: address.value.trim(),
        items: cart.value.map(item => ({
          item_id: item.id,
          quantity: item.quantity,
        })),
      }),
    })

    const result = await response.json()

    if (!response.ok) {
      throw new Error(
        result.detail
          ? String(result.detail)
          : '訂單送出失敗',
      )
    }

    notice.value =
      `下單成功！訂單編號：${result.order.id}，` +
      `總金額：NT$ ${result.order.total.toLocaleString()}`

    cart.value = []
    address.value = ''
  } catch (e) {
    error.value =
      e instanceof Error ? e.message : '下單失敗，請稍後再試。'
  } finally {
    submitting.value = false
  }
}

onMounted(loadRestaurants)
</script>

<template>
  <div class="page">
    <header class="navbar">
      <a class="brand" href="#" @click.prevent="backToRestaurants">
        <span class="brand-icon">🍱</span>
        <span>海大食堂</span>
      </a>
      <span class="nav-label">國立臺灣海洋大學・校園美食</span>
    </header>

    <main class="container">
      <section class="hero">
        <div class="hero-content">
          <span class="eyebrow">YOUR CAMPUS FOOD GUIDE</span>
          <h1>{{ selectedRestaurant ? selectedRestaurant.name : '今天想吃點什麼？' }}</h1>
          <p>
            {{
              selectedRestaurant
                ? selectedRestaurant.description
                : '探索校園美食，找到你的下一份美味。'
            }}
          </p>
          <button
            v-if="selectedRestaurant"
            class="hero-button"
            @click="backToRestaurants"
          >
            ← 返回餐廳列表
          </button>
          <a v-else class="hero-button" href="#restaurants">
            探索餐廳 ↓
          </a>
        </div>
        <div class="hero-emoji" aria-hidden="true">
          {{ selectedRestaurant ? '🍜' : '🍱' }}
        </div>
      </section>

      <section v-if="!selectedRestaurant" id="restaurants" class="restaurant-section">
        <div class="section-heading">
          <div>
            <span class="eyebrow">GOOD FOOD, GOOD MOOD</span>
            <h2>人氣餐廳</h2>
          </div>
          <span class="restaurant-count">
            {{ restaurants.length }} 間餐廳
          </span>
        </div>

        <div v-if="loading" class="message">
          正在為你尋找美食……
        </div>

        <div v-else-if="error" class="message error">
          {{ error }}
          <button class="retry-button" @click="loadRestaurants">
            重新載入
          </button>
        </div>

        <div v-else-if="restaurants.length === 0" class="message">
          目前還沒有餐廳資料。
        </div>

        <div v-else class="restaurant-grid">
          <article
            v-for="(restaurant, index) in restaurants"
            :key="restaurant.id"
            class="restaurant-card"
          >
            <div class="restaurant-cover" :class="'cover-' + (index % 3)">
              <span class="food-emoji">
                {{ ['🍛', '🍱', '🍜'][index % 3] }}
              </span>
              <span class="cover-tag">校園美食</span>
            </div>

            <div class="card-content">
              <span class="card-kicker">
                RESTAURANT {{ String(index + 1).padStart(2, '0') }}
              </span>
              <h3>{{ restaurant.name }}</h3>
              <p>{{ restaurant.description }}</p>
              <button class="menu-button" @click="openMenu(restaurant)">
                查看菜單 <span>→</span>
              </button>
            </div>
          </article>
        </div>
      </section>

      <section v-else class="ordering-section">
        <div class="section-heading">
          <div>
            <span class="eyebrow">FRESHLY MADE FOR YOU</span>
            <h2>餐點菜單</h2>
          </div>
          <span class="restaurant-count">
            {{ menuItems.length }} 種餐點
          </span>
        </div>

        <div v-if="menuLoading" class="message">
          正在載入菜單……
        </div>

        <div v-else-if="error" class="message error">
          {{ error }}
          <button class="retry-button" @click="openMenu(selectedRestaurant!)">
            重新載入
          </button>
        </div>

        <div v-else class="ordering-layout">
          <div class="menu-list">
            <article
              v-for="(item, index) in menuItems"
              :key="item.id"
              class="menu-card"
            >
              <div class="menu-food" :class="'cover-' + (index % 3)">
                {{ ['🍗', '🥩', '🍱'][index % 3] }}
              </div>
              <div class="menu-info">
                <h3>{{ item.name }}</h3>
                <p>現點現做・美味餐點</p>
                <strong>NT$ {{ item.price }}</strong>
              </div>
              <button
                class="add-button"
                :aria-label="`加入${item.name}`"
                @click="addToCart(item)"
              >
                +
              </button>
            </article>
          </div>

          <aside class="cart-panel">
            <div class="cart-title">
              <span>🛒</span>
              <h2>我的購物車</h2>
              <span class="cart-count">{{ cartCount }}</span>
            </div>

            <div v-if="cart.length === 0" class="empty-cart">
              <span>🥢</span>
              <p>購物車還是空的</p>
              <small>挑幾道喜歡的餐點吧！</small>
            </div>

            <div v-else class="cart-items">
              <div v-for="item in cart" :key="item.id" class="cart-item">
                <div class="cart-item-info">
                  <strong>{{ item.name }}</strong>
                  <span>NT$ {{ item.price }} / 份</span>
                </div>
                <div class="quantity-control">
                  <button
                    aria-label="減少數量"
                    @click="changeQuantity(item.id, -1)"
                  >
                    −
                  </button>
                  <span>{{ item.quantity }}</span>
                  <button
                    aria-label="增加數量"
                    @click="changeQuantity(item.id, 1)"
                  >
                    +
                  </button>
                </div>
              </div>
            </div>

            <div class="cart-total">
              <span>商品總額</span>
              <strong>NT$ {{ total.toLocaleString() }}</strong>
            </div>

            <form class="checkout-form" @submit.prevent="submitOrder">
              <label for="delivery-address">送餐地址</label>
              <textarea
                id="delivery-address"
                v-model="address"
                placeholder="請輸入送餐地址，例如：海大第一餐廳旁"
                rows="3"
                maxlength="300"
                required
              />

              <p v-if="error" class="feedback error">{{ error }}</p>
              <p v-if="notice" class="feedback success">{{ notice }}</p>

              <button
                type="submit"
                class="checkout-button"
                :disabled="submitting || cart.length === 0"
              >
                {{
                  submitting
                    ? '訂單送出中……'
                    : `確認下單・NT$ ${total.toLocaleString()}`
                }}
              </button>
            </form>

            <p class="cart-footnote">
              下單後訂單狀態將設為 Pending（待接單）。
            </p>
          </aside>
        </div>

        <p v-if="notice && cart.length === 0" class="message success">
          {{ notice }}
        </p>
      </section>

      <footer class="footer">
        <span>🍚 海大食堂</span>
        <span>為校園生活，多一點美味。</span>
      </footer>
    </main>
  </div>
</template>

<style>
:root {
  font-family: "Noto Sans TC", "Microsoft JhengHei", sans-serif;
  color: #292820;
  background: #faf8f3;
  font-synthesis: none;
  text-rendering: optimizeLegibility;
  -webkit-font-smoothing: antialiased;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  min-width: 320px;
  min-height: 100vh;
  background: #faf8f3;
  color: #292820;
}

button, a, textarea {
  font: inherit;
}

button {
  cursor: pointer;
}

a {
  color: inherit;
  text-decoration: none;
}

.page {
  min-height: 100vh;
  background: #faf8f3;
}

.navbar {
  min-height: 76px;
  padding: 12px max(6%, calc((100% - 1120px) / 2));
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  background: #fffefa;
  border-bottom: 1px solid #eee9df;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  color: #292820;
  font-size: 21px;
  font-weight: 900;
}

.brand-icon {
  font-size: 27px;
}

.nav-label {
  color: #777367;
  font-size: 13px;
}

.cart-indicator {
  padding: 9px 13px;
  border: 1px solid #e6ded1;
  border-radius: 9px;
  background: #fffefa;
  color: #8f4b2e;
  font-weight: 700;
}

.container {
  width: min(1120px, 88%);
  margin: 0 auto;
}

.hero {
  min-height: 290px;
  margin: 38px 0 50px;
  padding: 42px 7%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  overflow: hidden;
  border-radius: 24px;
  background: #f2e6d2;
}

.hero-content {
  position: relative;
  z-index: 1;
}

.eyebrow {
  color: #a45c35;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 2px;
}

.hero h1 {
  margin: 15px 0 12px;
  color: #292820;
  font-size: clamp(30px, 4vw, 45px);
  font-weight: 900;
}

.hero p {
  margin: 0 0 24px;
  color: #686052;
  font-size: 15px;
  line-height: 1.8;
}

.hero-button {
  display: inline-flex;
  padding: 12px 22px;
  border: 0;
  border-radius: 9px;
  background: #b95f38;
  color: white;
  font-size: 14px;
  font-weight: 700;
}

.hero-button:hover {
  background: #984a2b;
}

.hero-emoji {
  margin-right: 3%;
  font-size: clamp(80px, 13vw, 145px);
}

.restaurant-section,
.ordering-section {
  padding-bottom: 50px;
}

.section-heading {
  margin-bottom: 24px;
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 16px;
}

.section-heading h2 {
  margin: 7px 0 0;
  color: #292820;
  font-size: 27px;
  font-weight: 900;
}

.restaurant-count,
.cart-count {
  padding: 7px 12px;
  border-radius: 20px;
  background: #eee8dc;
  color: #766d5e;
  font-size: 12px;
}

.restaurant-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
  gap: 24px;
}

.restaurant-card {
  overflow: hidden;
  border: 1px solid #eee9df;
  border-radius: 17px;
  background: #fffefa;
  box-shadow: 0 5px 20px #493c1b08;
  transition: transform 0.2s, box-shadow 0.2s;
}

.restaurant-card:hover {
  transform: translateY(-4px);
  box-shadow: 0 12px 28px #493c1b12;
}

.restaurant-cover {
  height: 165px;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
}

.cover-0 { background: #f5e1c9; }
.cover-1 { background: #e7e9d8; }
.cover-2 { background: #f0deda; }

.food-emoji {
  font-size: 77px;
}

.cover-tag {
  position: absolute;
  top: 13px;
  left: 13px;
  padding: 6px 10px;
  border-radius: 6px;
  background: #fffefaeb;
  color: #8a563a;
  font-size: 11px;
  font-weight: 700;
}

.card-content {
  padding: 23px;
}

.card-kicker {
  color: #a45c35;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 1.5px;
}

.card-content h3 {
  margin: 10px 0 8px;
  color: #292820;
  font-size: 20px;
}

.card-content p {
  min-height: 24px;
  margin: 0 0 22px;
  color: #777367;
  font-size: 13px;
  line-height: 1.7;
}

.menu-button {
  width: 100%;
  padding: 12px 14px;
  display: flex;
  justify-content: space-between;
  border: 1px solid #e6ded1;
  border-radius: 8px;
  background: transparent;
  color: #8f4b2e;
  font-size: 13px;
  font-weight: 700;
}

.menu-button:hover {
  background: #f8eee3;
}

.ordering-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.5fr) minmax(300px, 0.9fr);
  align-items: start;
  gap: 26px;
}

.menu-list {
  display: grid;
  gap: 15px;
}

.menu-card {
  min-width: 0;
  padding: 16px;
  display: flex;
  align-items: center;
  gap: 16px;
  border: 1px solid #eee9df;
  border-radius: 14px;
  background: #fffefa;
}

.menu-food {
  width: 84px;
  height: 84px;
  flex: 0 0 84px;
  display: grid;
  place-items: center;
  border-radius: 12px;
  font-size: 42px;
}

.menu-info {
  flex: 1;
  min-width: 0;
}

.menu-info h3 {
  margin: 0 0 7px;
  font-size: 17px;
}

.menu-info p {
  margin: 0 0 9px;
  color: #888174;
  font-size: 12px;
}

.menu-info strong {
  color: #a34e2d;
  font-size: 15px;
}

.add-button {
  width: 38px;
  height: 38px;
  flex: 0 0 38px;
  border: 0;
  border-radius: 50%;
  background: #b95f38;
  color: white;
  font-size: 24px;
}

.add-button:hover,
.checkout-button:hover:not(:disabled) {
  background: #984a2b;
}

.cart-panel {
  position: sticky;
  top: 20px;
  padding: 23px;
  border: 1px solid #eee9df;
  border-radius: 17px;
  background: #fffefa;
  box-shadow: 0 5px 20px #493c1b08;
}

.cart-title {
  display: flex;
  align-items: center;
  gap: 10px;
  padding-bottom: 18px;
  border-bottom: 1px solid #eee9df;
}

.cart-title h2 {
  flex: 1;
  margin: 0;
  font-size: 19px;
}

.empty-cart {
  padding: 28px 5px;
  text-align: center;
  color: #777367;
}

.empty-cart span {
  font-size: 38px;
}

.empty-cart p {
  margin: 10px 0 6px;
  color: #514b40;
  font-weight: 700;
}

.empty-cart small {
  font-size: 12px;
}

.cart-items {
  padding: 7px 0;
}

.cart-item {
  padding: 14px 0;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  border-bottom: 1px solid #f0ece5;
}

.cart-item-info {
  min-width: 0;
  display: grid;
  gap: 6px;
}

.cart-item-info strong {
  font-size: 14px;
}

.cart-item-info span {
  color: #8a563a;
  font-size: 12px;
}

.quantity-control {
  display: flex;
  align-items: center;
  gap: 10px;
}

.quantity-control button {
  width: 28px;
  height: 28px;
  border: 1px solid #e6ded1;
  border-radius: 7px;
  background: #faf8f3;
  color: #8f4b2e;
  font-size: 17px;
}

.quantity-control span {
  min-width: 12px;
  text-align: center;
  font-size: 13px;
}

.cart-total {
  margin-top: 10px;
  padding: 17px 0;
  display: flex;
  justify-content: space-between;
  gap: 12px;
  border-top: 1px solid #eee9df;
}

.cart-total span {
  color: #777367;
  font-size: 13px;
}

.cart-total strong {
  color: #a34e2d;
  font-size: 20px;
}

.checkout-form {
  display: grid;
  gap: 10px;
}

.checkout-form label {
  font-size: 13px;
  font-weight: 800;
}

.checkout-form textarea {
  width: 100%;
  padding: 12px;
  resize: vertical;
  border: 1px solid #e6ded1;
  border-radius: 9px;
  outline: none;
  background: #fffefa;
  color: #292820;
  font-size: 13px;
  line-height: 1.6;
}

.checkout-form textarea:focus {
  border-color: #b95f38;
}

.checkout-button {
  width: 100%;
  padding: 14px 10px;
  border: 0;
  border-radius: 9px;
  background: #b95f38;
  color: white;
  font-size: 14px;
  font-weight: 800;
}

.checkout-button:disabled {
  cursor: not-allowed;
  background: #c8b9ab;
}

.cart-footnote {
  margin: 12px 0 0;
  color: #999184;
  font-size: 11px;
  line-height: 1.7;
}

.message {
  padding: 28px 20px;
  border: 1px dashed #ded6c8;
  border-radius: 14px;
  color: #777367;
  text-align: center;
  line-height: 1.8;
}

.error {
  color: #a43d30;
}

.success {
  color: #34704b;
}

.feedback {
  margin: 0;
  font-size: 12px;
  line-height: 1.7;
  overflow-wrap: anywhere;
}

.retry-button {
  margin-left: 10px;
  padding: 7px 12px;
  border: 1px solid #e6ded1;
  border-radius: 7px;
  background: #fffefa;
  color: #8f4b2e;
}

.footer {
  padding: 24px 0 32px;
  display: flex;
  justify-content: space-between;
  gap: 16px;
  border-top: 1px solid #e9e3d8;
  color: #888174;
  font-size: 12px;
}

.footer span:first-child {
  color: #635343;
  font-weight: 800;
}

@media (max-width: 800px) {
  .ordering-layout {
    grid-template-columns: 1fr;
  }

  .cart-panel {
    position: static;
  }
}

@media (max-width: 600px) {
  .navbar {
    padding: 12px 5%;
    flex-wrap: wrap;
  }

  .nav-label {
    display: none;
  }

  .container {
    width: 90%;
  }

  .hero {
    min-height: 250px;
    margin: 24px 0 38px;
    padding: 30px 7%;
  }

  .hero-emoji {
    margin-right: 0;
    font-size: 75px;
  }

  .restaurant-grid {
    grid-template-columns: 1fr;
  }

  .menu-card {
    padding: 12px;
    gap: 11px;
  }

  .menu-food {
    width: 64px;
    height: 64px;
    flex-basis: 64px;
    font-size: 32px;
  }

  .menu-info h3 {
    font-size: 15px;
  }

  .section-heading h2 {
    font-size: 23px;
  }

  .footer {
    flex-direction: column;
    gap: 8px;
  }
}
</style>
