<script setup lang="ts">
import { demoAccount, stateText, useOrderTracking } from '../lib/orderTracking'
import { arrivalTime, deliveryFee } from '../lib/deliveryEstimate'

const DELIVERER_ID = demoAccount(import.meta.env.VITE_DELIVERER_ID ?? '1')
const { orders, loading, error, notice, pendingAction, lastUpdated, online, blocked, loadOrders, changeOrder, key } = useOrderTracking('deliverer', DELIVERER_ID)
</script>

<template>
  <div class="page">
    <h1>我的外送單</h1>
    <p class="account" data-testid="demo-account">示範帳號：外送員 {{ DELIVERER_ID === null ? "設定無效" : `#${DELIVERER_ID}` }}（尚未串接登入）</p>
    <p><a href="/driver.html">← 回接單大廳</a></p>

    <div class="toolbar">
      <span>共 {{ orders.length }} 筆</span>
      <button data-testid="refresh-orders" :disabled="loading || !!pendingAction || !online || DELIVERER_ID === null" @click="loadOrders()">{{ loading ? "更新中……" : "重新整理" }}</button>
    </div>

    <p class="refresh-status" data-testid="refresh-status">{{ lastUpdated ? `上次更新：${lastUpdated}；每 15 秒自動更新` : '每 15 秒自動更新' }}</p>
    <p v-if="notice" class="notice" role="status" data-testid="action-notice">{{ notice }}</p>
    <p v-if="pendingAction" class="notice" role="status">正在處理訂單，請稍候……</p>
    <div v-if="error" class="notice error" role="alert" data-testid="orders-error">
      <p>{{ error }}</p>
      <p v-if="orders.length">下方保留上次查詢結果，更新成功後才能繼續操作。</p>
      <button data-testid="retry-orders" :disabled="loading || !!pendingAction || !online || DELIVERER_ID === null" @click="loadOrders()">重試</button>
    </div>

    <div v-if="loading && orders.length === 0" class="message">載入中……</div>

    <div v-else-if="!error && orders.length === 0" class="message">
      目前沒有接到的訂單
    </div>

    <div v-else-if="orders.length">
      <div
        v-for="order in orders"
        :key="key(order)"
        class="order"
        data-testid="order-card"
        :data-order-key="key(order)"
        :data-order-state="order.state"
        :aria-busy="pendingAction === key(order)"
      >
        <div class="order-title">
          <strong>買家 #{{ order.buyer_id }}</strong>
          <span class="state" data-testid="order-state">{{ stateText[order.state] }}</span>
        </div>

        <p class="time">下單時間：{{ order.timestamp }}</p>
        <p data-testid="delivery-address">送餐地址：{{ order.address }}</p>
        <p v-if="order.state === 'processing'" data-testid="arrival-time">預計 {{ arrivalTime(order.timestamp, order.address) }} 送達</p>
        <p data-testid="delivery-fee">外送費（你的收入）：NT$ {{ deliveryFee(order.address) }}</p>

        <div v-for="(item, index) in order.items" :key="index">
          <div class="item">
            <span>{{ item.name }} × {{ item.quantity }}（{{ item.restaurant }}）</span>
            <span>NT$ {{ item.price * item.quantity }}</span>
          </div>
          <p v-if="item.note" class="note">備註：{{ item.note }}</p>
        </div>

        <div class="bottom">
          <span class="total">總金額：NT$ {{ order.total }}</span>

          <div v-if="order.state === 'processing'" class="actions">
            <button
              :disabled="blocked"
              data-testid="abandon-order"
              @click="changeOrder(order, 'abandon', '確定要放棄這筆訂單嗎？')"
            >
              棄單
            </button>
            <button
              :disabled="blocked"
              data-testid="complete-order"
              class="primary"
              @click="changeOrder(order, 'complete', '確定已經送達，要完成這筆訂單嗎？')"
            >
              完成訂單
            </button>
          </div>
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
  overflow-wrap: anywhere;
}

h1 {
  text-align: center;
}

.account {
  text-align: center;
  color: #888;
}

a {
  color: #333;
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

.actions {
  display: flex;
  gap: 10px;
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

button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.refresh-status {
  color: #666;
  font-size: 14px;
}

.error {
  background: #fff1e8;
}

@media (max-width: 520px) {
  .page { padding: 12px; }
  .order { padding: 14px; }
  .bottom { flex-wrap: wrap; gap: 12px; }
  .order-title, .item { gap: 12px; }
}
</style>
