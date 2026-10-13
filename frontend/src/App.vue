<script setup>
import { ref, onMounted } from 'vue'

const restaurants = ref([])
const meals = ref([])
const selectedRestaurant = ref(null)

const loadingRestaurants = ref(false)
const loadingMeals = ref(false)
const restaurantError = ref('')
const mealError = ref('')

async function loadRestaurants() {
  loadingRestaurants.value = true
  restaurantError.value = ''

  try {
    const response = await fetch('/api/restaurants')

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }

    restaurants.value = await response.json()
  } catch (err) {
    console.error(err)
    restaurantError.value = '無法取得餐廳列表，請稍後再試。'
  } finally {
    loadingRestaurants.value = false
  }
}

async function selectRestaurant(restaurant) {
  // 再次點擊目前選取的餐廳，就收起餐點
  if (selectedRestaurant.value?.id === restaurant.id) {
    selectedRestaurant.value = null
    meals.value = []
    mealError.value = ''
    return
  }

  selectedRestaurant.value = restaurant
  meals.value = []
  mealError.value = ''
  loadingMeals.value = true

  try {
    const url =
      `/api/restaurants/${encodeURIComponent(restaurant.name)}`

    const response = await fetch(url)

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }

    meals.value = await response.json()
  } catch (err) {
    console.error(err)
    mealError.value = '無法取得餐點資料，請稍後再試。'
  } finally {
    loadingMeals.value = false
  }
}

function addToCart(meal) {
  // 目前尚未實作購物車功能
}

onMounted(loadRestaurants)
</script>

<template>
  <main class="container">
    <h1>海大餐飲外送系統</h1>

    <p v-if="loadingRestaurants">餐廳載入中...</p>
    <p v-else-if="restaurantError" class="error">
      {{ restaurantError }}
    </p>

    <div v-else class="restaurant-list">
      <!-- 每間餐廳佔一整排 -->
      <section
        v-for="restaurant in restaurants"
        :key="restaurant.id"
        class="restaurant-section"
      >
        <button
          type="button"
          class="restaurant-row"
          :class="{
            selected: selectedRestaurant?.id === restaurant.id
          }"
          :aria-expanded="selectedRestaurant?.id === restaurant.id"
          @click="selectRestaurant(restaurant)"
        >
          <span class="restaurant-info">
            <strong>{{ restaurant.name }}</strong>
            <span class="address">
              {{ restaurant.address || '尚無地址資料' }}
            </span>
          </span>

          <span class="arrow">
            {{ selectedRestaurant?.id === restaurant.id ? '▲' : '▼' }}
          </span>
        </button>

        <!-- 只有被選取的餐廳才顯示餐點 -->
        <div
          v-if="selectedRestaurant?.id === restaurant.id"
          class="meals-panel"
        >
          <h2>{{ restaurant.name }} 的餐點</h2>

          <p v-if="loadingMeals">餐點載入中...</p>
          <p v-else-if="mealError" class="error">
            {{ mealError }}
          </p>
          <p v-else-if="meals.length === 0">
            目前沒有餐點資料。
          </p>

          <div v-else class="meal-list">
            <!-- 每個餐點佔一整排 -->
            <div
              v-for="meal in meals"
              :key="meal.id"
              class="meal-row"
            >
              <div class="meal-info">
                <strong>{{ meal.name }}</strong>
                <span class="price">
                  {{ meal.price ?? '未提供價格' }}
                  <template v-if="meal.price != null"> 元</template>
                </span>
              </div>

              <button
                type="button"
                class="cart-button"
                @click.stop="addToCart(meal)"
              >
                加入購物車
              </button>
            </div>
          </div>
        </div>
      </section>
    </div>
  </main>
</template>

<style scoped>
.container {
  max-width: 900px;
  margin: 40px auto;
  padding: 0 20px;
  font-family: sans-serif;
  color: #222;
}

h1 {
  margin-bottom: 24px;
}

.restaurant-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.restaurant-section {
  width: 100%;
  border: 1px solid #ddd;
  border-radius: 10px;
  overflow: hidden;
}

.restaurant-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  width: 100%;
  padding: 18px 20px;
  border: none;
  background: #fff;
  color: inherit;
  text-align: left;
  cursor: pointer;
}

.restaurant-row:hover,
.restaurant-row.selected {
  background: #f0faf5;
}

.restaurant-info {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.restaurant-info strong {
  font-size: 17px;
}

.address {
  color: #666;
  font-size: 14px;
  overflow-wrap: anywhere;
}

.arrow {
  flex-shrink: 0;
  color: #555;
}

.meals-panel {
  padding: 16px 20px 20px;
  border-top: 1px solid #ddd;
  background: #fafafa;
}

.meals-panel h2 {
  margin: 0 0 16px;
  font-size: 18px;
}

.meal-list {
  display: flex;
  flex-direction: column;
}

.meal-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 0;
  border-bottom: 1px solid #e5e5e5;
}

.meal-row:last-child {
  border-bottom: none;
}

.meal-info {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.price {
  color: #555;
  font-size: 14px;
}

.cart-button {
  flex-shrink: 0;
  padding: 9px 14px;
  border: none;
  border-radius: 6px;
  background: #258653;
  color: white;
  cursor: pointer;
}

.cart-button:hover {
  background: #1d6d42;
}

.error {
  color: #c62828;
}

@media (max-width: 480px) {
  .container {
    padding: 0 12px;
  }

  .restaurant-row {
    padding: 14px;
  }

  .meals-panel {
    padding: 14px;
  }

  .meal-row {
    gap: 8px;
  }

  .cart-button {
    padding: 8px 10px;
    font-size: 13px;
  }
}
</style>
