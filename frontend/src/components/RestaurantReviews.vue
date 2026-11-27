<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

interface Restaurant {
  restaurant_id: number
  restaurant_name: string
  restaurant_address: string
}

interface Review {
  restaurants_id: number
  user_id: number
  timestamp: string
  stars: number
  comment: string | null
}

// 有傳 restaurantId 就只顯示那間餐廳（例如放在菜單頁），沒傳就顯示全部餐廳
const props = defineProps<{ restaurantId?: number }>()

const restaurants = ref<Restaurant[]>([])
const reviews = ref<Review[]>([])

const loading = ref(true)
const error = ref('')

const shownRestaurants = computed(() => {
  if (props.restaurantId === undefined) return restaurants.value
  return restaurants.value.filter(r => r.restaurant_id === props.restaurantId)
})

// 某間餐廳的評價，新的排前面
function reviewsOf(restaurantId: number) {
  return reviews.value
    .filter(review => review.restaurants_id === restaurantId)
    .sort((a, b) => b.timestamp.localeCompare(a.timestamp))
}

// 平均星等，取到小數點後一位
function averageStars(restaurantId: number) {
  const list = reviewsOf(restaurantId)
  const sum = list.reduce((total, review) => total + review.stars, 0)
  return Math.round((sum / list.length) * 10) / 10
}

function starText(stars: number) {
  const full = Math.round(stars)
  return '★'.repeat(full) + '☆'.repeat(5 - full)
}

async function loadReviews() {
  loading.value = true
  error.value = ''

  try {
    const restaurantResponse = await fetch('/api/view_all_restaurants')
    if (!restaurantResponse.ok) throw new Error('無法取得餐廳')
    restaurants.value = await restaurantResponse.json()

    const reviewResponse = await fetch('/api/view_all_restaurants_reviews')
    if (!reviewResponse.ok) throw new Error('無法取得評價')
    const result = await reviewResponse.json()
    reviews.value = result.reviews
  } catch {
    error.value = '無法連接後端，請確認 Python 伺服器是否啟動。'
  } finally {
    loading.value = false
  }
}

onMounted(loadReviews)
</script>

<template>
  <div class="reviews">
    <template v-if="restaurantId === undefined">
      <h1>餐廳評價</h1>
      <p><a href="/">← 回點餐頁</a></p>
    </template>

    <div v-if="loading" class="message">載入中……</div>

    <div v-else-if="error" class="message">{{ error }}</div>

    <div v-else-if="shownRestaurants.length === 0" class="message">
      目前沒有餐廳
    </div>

    <div v-else>
      <div
        v-for="restaurant in shownRestaurants"
        :key="restaurant.restaurant_id"
        class="restaurant"
        data-testid="restaurant-reviews"
      >
        <div class="restaurant-title">
          <strong>{{ restaurant.restaurant_name }}</strong>

          <span v-if="reviewsOf(restaurant.restaurant_id).length" data-testid="average-stars">
            {{ starText(averageStars(restaurant.restaurant_id)) }}
            {{ averageStars(restaurant.restaurant_id) }}（{{ reviewsOf(restaurant.restaurant_id).length }} 則評價）
          </span>
          <span v-else class="empty">還沒有評價</span>
        </div>

        <div
          v-for="(review, index) in reviewsOf(restaurant.restaurant_id)"
          :key="index"
          class="review"
        >
          <div class="review-title">
            <span>{{ starText(review.stars) }}</span>
            <span class="time">{{ review.timestamp.replace('T', ' ') }}</span>
          </div>
          <p v-if="review.comment" class="comment">{{ review.comment }}</p>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.reviews {
  max-width: 800px;
  margin: 0 auto;
  padding: 20px;
  color: #333;
  overflow-wrap: anywhere;
}

h1 {
  text-align: center;
}

a {
  color: #333;
}

.message {
  text-align: center;
  color: #888;
  padding: 30px;
}

.restaurant {
  background: white;
  padding: 20px;
  border: 1px solid #ddd;
  border-radius: 10px;
  margin-bottom: 15px;
}

.restaurant-title {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 10px;
  border-bottom: 1px solid #ddd;
  padding-bottom: 10px;
}

.empty,
.time {
  color: #888;
}

.review {
  padding: 10px 0;
  border-bottom: 1px solid #eee;
}

.review:last-child {
  border-bottom: none;
}

.review-title {
  display: flex;
  justify-content: space-between;
}

.comment {
  margin: 5px 0 0;
}
</style>
