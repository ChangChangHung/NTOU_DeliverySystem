
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  API_SCOPE, BUYER_ID, CHECKOUT_STORAGE_KEY, archiveCheckout, emptyCheckout, finishCheckout,
  loadCatalog, readCheckout, validBuyer, withCheckoutLock, writeCheckout,
  type CartItem, type CheckoutAttempt, type MenuItem, type Restaurant, type SavedCheckout,
} from './lib/checkout'

let saved = emptyCheckout()
let initialStorageError = ''
try { saved = readCheckout() } catch (e) {
  initialStorageError = e instanceof Error ? e.message : '無法讀取購物車紀錄。'
}
const restaurants = ref<Restaurant[]>([])
const allMeals = ref<MenuItem[]>([])
const restaurantId = ref(saved.restaurantId)
const selectedRestaurant = computed(() => restaurants.value.find(item => item.id === restaurantId.value) ?? null)
const menuItems = computed(() => allMeals.value.filter(item => item.restaurantId === restaurantId.value))
const cart = ref<CartItem[]>(saved.cart)
const address = ref(saved.address)
const pendingCheckout = ref<CheckoutAttempt | null>(saved.attempt)
const storageError = ref(initialStorageError)
const loading = ref(true)
const submitting = ref(false)
const catalogError = ref('')
const checkoutError = ref('')
const notice = ref('')
const reviewedOrders = ref(false)
let restoring = false
const cartLocked = computed(() => submitting.value || pendingCheckout.value !== null || !!storageError.value)
const unknownCheckout = computed(() => pendingCheckout.value?.phase === 'create_unknown' || pendingCheckout.value?.phase === 'creating')
const unavailableItems = computed(() => cart.value.filter(item => !allMeals.value.some(meal => meal.id === item.id)))
const canSubmit = computed(() => validBuyer && !loading.value && !catalogError.value && !cartLocked.value
  && cart.value.length > 0 && unavailableItems.value.length === 0 && Number.isSafeInteger(total.value))

const cartCount = computed(() =>
  cart.value.reduce((sum, item) => sum + item.quantity, 0),
)

const total = computed(() =>
  cart.value.reduce(
    (sum, item) => sum + item.price * item.quantity,
    0,
  ),
)

function snapshot(attempt = pendingCheckout.value): SavedCheckout {
  return { version: 1, buyerId: BUYER_ID, scope: API_SCOPE, cart: cart.value,
    address: address.value, restaurantId: restaurantId.value, attempt }
}

function persistDraft() {
  if (restoring || initialStorageError) return
  try {
    writeCheckout(snapshot())
    storageError.value = ''
  } catch (e) {
    storageError.value = e instanceof Error ? e.message : '購物車紀錄無法保存。'
  }
}

function saveAttempt(attempt: CheckoutAttempt | null) {
  // 寫入成功才更新畫面；無法保存時不再送出下一個 API。
  try { writeCheckout(snapshot(attempt)) } catch (e) {
    storageError.value = e instanceof Error ? e.message : '下單紀錄無法保存。'
    throw e
  }
  pendingCheckout.value = attempt
  storageError.value = ''
}

function restore(value: SavedCheckout) {
  restoring = true
  if (JSON.stringify(pendingCheckout.value) !== JSON.stringify(value.attempt)) reviewedOrders.value = false
  cart.value = value.cart
  address.value = value.address
  restaurantId.value = value.restaurantId
  pendingCheckout.value = value.attempt
  restoring = false
}

watch([cart, address, restaurantId], persistDraft, { deep: true, flush: 'sync' })

function refreshSavedCheckout(event: StorageEvent) {
  if (event.key !== CHECKOUT_STORAGE_KEY || submitting.value) return
  try { restore(readCheckout()) } catch (e) {
    storageError.value = e instanceof Error ? e.message : '無法讀取其他頁面的下單紀錄。'
  }
}

async function loadRestaurants() {
  if (submitting.value) return
  loading.value = true
  catalogError.value = ''
  try {
    const catalog = await loadCatalog()
    restaurants.value = catalog.restaurants
    allMeals.value = catalog.meals
    // 顯示目前菜單的價格與名稱；保留原本數量和備註。
    if (!pendingCheckout.value) {
      cart.value = cart.value.map(item => ({ ...item, ...catalog.meals.find(meal => meal.id === item.id) }))
    }
    if (cart.value.length && !selectedRestaurant.value) {
      restaurantId.value = catalog.restaurants.find(item => item.id === cart.value[0]?.restaurantId)?.id
        ?? catalog.restaurants[0]?.id ?? null
    }
  } catch (e) {
    catalogError.value = e instanceof Error ? e.message : '餐廳與菜單暫時載入失敗，請稍後再試。'
  } finally {
    loading.value = false
  }
}

function openMenu(restaurant: Restaurant) {
  restaurantId.value = restaurant.id
  notice.value = ''
}

function backToRestaurants() {
  restaurantId.value = null
}

function showCart() {
  restaurantId.value = cart.value[0]?.restaurantId ?? restaurants.value[0]?.id ?? null
}

function addToCart(item: MenuItem) {
  if (cartLocked.value) return
  checkoutError.value = ''
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
    cart.value.push({ ...item, quantity: 1, note: '' })
  }

  notice.value = `已將「${item.name}」加入購物車！`
}

function changeQuantity(itemId: number, change: number) {
  if (cartLocked.value) return
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

function removeFromCart(itemId: number) {
  if (cartLocked.value) return
  cart.value = cart.value.filter(item => item.id !== itemId)
}

async function submitOrder() {
  if (!canSubmit.value || submitting.value) return
  if (!address.value.trim() || address.value.trim().length > 120) {
    checkoutError.value = '請填寫送餐地址，最多 120 個字。'
    return
  }
  await runCheckout(false)
}

async function runCheckout(recover: boolean) {
  if (submitting.value || !validBuyer || storageError.value || (recover && unknownCheckout.value)) return
  submitting.value = true
  checkoutError.value = ''
  notice.value = ''
  try {
    await withCheckoutLock(async () => {
      // 取得同環境、同買家鎖之後才讀紀錄，不排隊重送另一頁剛完成的訂單。
      const latest = readCheckout()
      restore(latest)
      if (!recover && latest.attempt) {
        throw new Error('還有一筆下單尚未完成，請先處理下方的訂單紀錄。')
      }
      if (!recover && latest.cart.length === 0) {
        throw new Error('購物車已由其他頁面更新，請重新選餐。')
      }
      if (recover && !latest.attempt) {
        throw new Error('這筆下單已由其他頁面處理，請重新整理後查看訂單。')
      }
      await finishCheckout(pendingCheckout.value, cart.value, address.value, saveAttempt)
      const cleared = emptyCheckout()
      writeCheckout(cleared)
      restore(cleared)
      window.location.assign('/buyer-orders.html')
    })
  } catch (e) {
    checkoutError.value = e instanceof Error ? e.message : '下單暫時無法完成，請先查看訂單紀錄。'
  } finally {
    submitting.value = false
  }
}

async function endPendingCheckout() {
  if (submitting.value || !pendingCheckout.value || !reviewedOrders.value || storageError.value) return
  if (!window.confirm('這只會歸檔本機下單紀錄並清空這次購物車，不會刪除、取消或重送伺服器上的訂單。原訂單若已建立，仍會留在「我的訂單」。\n\n確定已查看原訂單狀態，要清空這次購物車並重新選餐嗎？')) return
  const attempt = pendingCheckout.value
  submitting.value = true
  try {
    await withCheckoutLock(async () => {
      const cleared = archiveCheckout(attempt)
      restore(cleared)
      checkoutError.value = ''
      notice.value = '這次購物車已清空，下單紀錄已保留在這個瀏覽器。原訂單狀態沒有改變，可以重新選餐。'
    })
  } catch (e) {
    checkoutError.value = e instanceof Error ? e.message : '這次下單紀錄無法歸檔，購物車仍會保留。'
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  window.addEventListener('storage', refreshSavedCheckout)
  void loadRestaurants()
})
onUnmounted(() => window.removeEventListener('storage', refreshSavedCheckout))
</script>

<template>
  <div class="page">
    <header class="navbar">
      <a class="brand" href="#" @click.prevent="backToRestaurants">
        <span class="brand-icon">🍱</span>
        <span>海大食堂</span>
      </a>
      <span class="nav-label">國立臺灣海洋大學・校園美食</span>
      <nav class="page-links" aria-label="頁面導覽">
        <a href="/buyer-orders.html">我的訂單</a>
        <a href="/driver.html">接單大廳</a>
        <button v-if="cart.length" class="cart-indicator" @click="showCart">購物車 {{ cartCount }}</button>
      </nav>
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

      <p class="buyer-account" data-testid="demo-account">
        示範帳號：買家 {{ validBuyer ? `#${BUYER_ID}` : '設定無效' }}（尚未串接登入）
      </p>
      <p v-if="!validBuyer" class="message error" role="alert">示範買家帳號設定無效，VITE_BUYER_ID 必須是正整數。</p>
      <p v-if="storageError" class="message error" role="alert" data-testid="checkout-storage-error">
        {{ storageError }} <a href="/buyer-orders.html">查看我的訂單</a>
      </p>
      <p v-if="checkoutError" class="message error checkout-feedback" role="alert" data-testid="checkout-error">{{ checkoutError }}</p>
      <section v-if="pendingCheckout" class="checkout-recovery" data-testid="checkout-pending" aria-live="polite">
        <template v-if="unknownCheckout">
          <h2>這筆下單還待確認</h2>
          <p data-testid="checkout-unknown">訂單可能已經建立，請先到「我的訂單」確認。這裡會保留餐點與地址，不會再次送出建單。</p>
        </template>
        <template v-else>
          <h2>繼續完成這筆下單</h2>
          <p>下單時間：{{ pendingCheckout.order?.timestamp }}。餐點已送出，接著確認這筆訂單與送餐資料。</p>
          <p>送餐地址：{{ pendingCheckout.address }}</p>
          <button data-testid="checkout-recover" class="retry-button" :disabled="submitting || !!storageError" @click="runCheckout(true)">
            {{ submitting ? '正在確認……' : '繼續完成下單' }}
          </button>
        </template>
        <a href="/buyer-orders.html" target="_blank" rel="noopener" data-testid="checkout-view-orders" @click="reviewedOrders = true">查看我的訂單（開新分頁）</a>
        <div class="checkout-end-attempt">
          <p>想結束這次操作並重新選餐，請先查看原訂單狀態。清空這裡的購物車不會取消訂單；若已下單，原訂單仍會留在「我的訂單」。</p>
          <button class="retry-button" data-testid="checkout-end-attempt" :disabled="submitting || !reviewedOrders || !!storageError" @click="endPendingCheckout">我已查看訂單紀錄，清空這次購物車</button>
        </div>
      </section>
      <p v-if="notice && !selectedRestaurant" class="message success checkout-feedback" role="status" data-testid="checkout-notice">{{ notice }}</p>

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

        <div v-else-if="catalogError" class="message error">
          {{ catalogError }}
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
            data-testid="restaurant-card"
            :data-restaurant-id="restaurant.id"
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

        <div v-if="loading" class="message">
          正在載入菜單……
        </div>

        <div v-else-if="catalogError" class="message error">
          {{ catalogError }}
          <button class="retry-button" @click="loadRestaurants">
            重新載入
          </button>
        </div>

        <div v-else class="ordering-layout">
          <div class="menu-list">
            <p v-if="menuItems.length === 0" class="message">這間餐廳目前沒有餐點。</p>
            <article
              v-for="(item, index) in menuItems"
              :key="item.id"
              class="menu-card"
              data-testid="menu-item"
              :data-meal-id="item.id"
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
                :disabled="cartLocked"
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
              <span class="cart-count" data-testid="cart-count">{{ cartCount }}</span>
            </div>

            <div v-if="cart.length === 0" class="empty-cart">
              <span>🥢</span>
              <p>購物車還是空的</p>
              <small>挑幾道喜歡的餐點吧！</small>
            </div>

            <div v-else class="cart-items">
              <div v-for="item in cart" :key="item.id" class="cart-item" data-testid="cart-item" :data-meal-id="item.id">
                <div class="cart-item-info">
                  <strong>{{ item.name }}</strong>
                  <span>NT$ {{ item.price }} / 份</span>
                  <small>{{ restaurants.find(restaurant => restaurant.id === item.restaurantId)?.name }}</small>
                  <small v-if="unavailableItems.some(unavailable => unavailable.id === item.id)" class="error">這道餐點目前無法購買，請先移除。</small>
                </div>
                <div class="quantity-control">
                  <button
                    aria-label="減少數量"
                    :disabled="cartLocked"
                    @click="changeQuantity(item.id, -1)"
                  >
                    −
                  </button>
                  <span data-testid="cart-quantity">{{ item.quantity }}</span>
                  <button
                    aria-label="增加數量"
                    :disabled="cartLocked || item.quantity >= 99"
                    @click="changeQuantity(item.id, 1)"
                  >
                    +
                  </button>
                  <button class="remove-item" :aria-label="`移除${item.name}`" :disabled="cartLocked" @click="removeFromCart(item.id)">移除</button>
                </div>
                <label class="cart-note" :for="`cart-note-${item.id}`">
                  餐點備註（最多 100 字）
                  <textarea :id="`cart-note-${item.id}`" v-model="item.note" data-testid="cart-note" :disabled="cartLocked" maxlength="100" rows="2" placeholder="例如：不要香菜" />
                </label>
              </div>
            </div>

            <div class="cart-total">
              <span>商品總額</span>
              <strong data-testid="cart-total">NT$ {{ total.toLocaleString() }}</strong>
            </div>

            <form class="checkout-form" @submit.prevent="submitOrder">
              <label for="delivery-address">送餐地址</label>
              <textarea
                id="delivery-address"
                v-model="address"
                data-testid="checkout-address"
                :disabled="cartLocked"
                placeholder="請輸入送餐地址，例如：海大第一餐廳旁"
                rows="3"
                maxlength="120"
                required
              />

              <p v-if="notice" class="feedback success">{{ notice }}</p>

              <button
                type="submit"
                class="checkout-button"
                data-testid="checkout-submit"
                :disabled="!canSubmit"
              >
                {{
                  submitting
                    ? '訂單送出中……'
                    : `確認下單・NT$ ${total.toLocaleString()}`
                }}
              </button>
            </form>

            <p class="cart-footnote">
              送餐地址最多 120 字。下單完成後，可到「我的訂單」查看進度。
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

button:disabled {
  cursor: not-allowed;
  opacity: 0.6;
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

.page-links {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 14px;
  font-size: 14px;
}

.page-links a,
.checkout-recovery a,
.message a {
  color: #8f4b2e;
  text-decoration: underline;
  text-underline-offset: 3px;
}

.buyer-account {
  margin: 0 0 20px;
  color: #777367;
  font-size: 13px;
}

.checkout-recovery {
  margin-bottom: 24px;
  padding: 20px;
  border: 1px solid #d9bb87;
  border-radius: 14px;
  background: #fff6e8;
  line-height: 1.8;
  overflow-wrap: anywhere;
}

.checkout-recovery h2 {
  margin-bottom: 8px;
  font-size: 20px;
}

.checkout-recovery .retry-button {
  margin: 12px 14px 0 0;
}

.checkout-end-attempt {
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px solid #e6d6bc;
}

.checkout-feedback {
  margin-bottom: 20px;
  overflow-wrap: anywhere;
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
  flex-wrap: wrap;
  border-bottom: 1px solid #f0ece5;
}

.cart-item-info {
  min-width: 0;
  flex: 1 1 100px;
  display: grid;
  gap: 6px;
  overflow-wrap: anywhere;
}

.cart-item-info small {
  font-size: 11px;
  color: #777367;
}

.cart-note {
  flex: 0 0 100%;
  display: grid;
  gap: 6px;
  color: #777367;
  font-size: 12px;
}

.cart-note textarea {
  width: 100%;
  padding: 8px;
  border: 1px solid #e6ded1;
  border-radius: 7px;
  resize: vertical;
  background: #fffefa;
  color: #292820;
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

.quantity-control .remove-item {
  width: auto;
  padding: 0 7px;
  font-size: 12px;
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
