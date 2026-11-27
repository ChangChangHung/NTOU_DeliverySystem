// 預估送達時間與外送費（IT3-02、IT3-03）
//
// 送餐地址是買家自己輸入的文字，所以看地址裡有沒有海大的地點名稱，
// 找出從校門口走過去大約幾公尺，再換算成送達時間和外送費。
// 距離都是大約值，之後要調整直接改下面這張表就好。

interface Place {
  keyword: string
  meters: number
}

const PLACES: Place[] = [
  { keyword: '行政大樓', meters: 200 },
  { keyword: '夢泉', meters: 300 },
  { keyword: '第一餐廳', meters: 350 },
  { keyword: '圖書館', meters: 400 },
  { keyword: '電機', meters: 500 },
  { keyword: '體育館', meters: 600 },
  { keyword: '宿舍', meters: 800 },
]

// 地址裡找不到上面的地點時，當作 500 公尺
const DEFAULT_METERS = 500

// 外送員去餐廳買餐、等餐大約 15 分鐘
const PREPARE_MINUTES = 15

// 走路速度：每分鐘大約 80 公尺
const METERS_PER_MINUTE = 80

export function deliveryMeters(address: string) {
  const place = PLACES.find(p => address.includes(p.keyword))
  return place ? place.meters : DEFAULT_METERS
}

// 預估要幾分鐘送達
export function deliveryMinutes(address: string) {
  return PREPARE_MINUTES + Math.ceil(deliveryMeters(address) / METERS_PER_MINUTE)
}

// 階梯收費：300 公尺內 15 元，600 公尺內 20 元，再遠 30 元
export function deliveryFee(address: string) {
  const meters = deliveryMeters(address)

  if (meters <= 300) return 15
  if (meters <= 600) return 20
  return 30
}

// 下單時間加上預估分鐘數，算出幾點幾分送達，例如 "12:45"
export function arrivalTime(timestamp: string, address: string) {
  const time = new Date(timestamp.replace(' ', 'T'))
  time.setMinutes(time.getMinutes() + deliveryMinutes(address))
  return time.toTimeString().slice(0, 5)
}
