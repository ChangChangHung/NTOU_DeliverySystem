---
title: 資料庫API操作說明
tags: [資料庫]

---

#  資料庫操作說明

### 說明
透過對網址發送HTTP請求、JSON物件操作資料庫。
臨時網址: `https://inspection-driving-jul-built.trycloudflare.com`
cloudflare 臨時tunnel，可能會變更。

- 所有API都是在此連結後附加對應的路徑: `https://臨時網域名稱/api`
例: `https://臨時網域名稱/api/add_user`是新增使用者API
- 有些HTTP請求類型不同(GET、POST、DELETE等)，請留意區別
- 伺服器回傳一律為JSON格式
- 若伺服器回傳的不是JSON，則代表程式有錯，請再另外告知

## API

### -- 查詢所有餐廳
- GET
- 路徑: `/api/view_all_restaurants`
- 回傳多筆`Restaurants`欄位資料
- 回傳範例:
```JSON=
[
    {
        "restaurant_address": "202基隆市中正區港通里信一路177號",
        "restaurant_id": 2,
        "restaurant_name": "麥當勞-基隆信一餐廳"
    },
    {
        "restaurant_address": "202基隆市中正區海濱里北寧路9之1號",
        "restaurant_id": 3,
        "restaurant_name": "北寧早餐店"
    }
]
```

### -- 查詢所有餐點
- GET
- 路徑: `/api/view_all_meals`
- 回傳多筆`Meals`欄位資料
- 回傳範例:
```JSON=
[
    {
        "meal_id": 3,
        "meal_name": "麥香雞堡",
        "price": 50,
        "restaurant_id": 3
    },
    {
        "meal_id": 4,
        "meal_name": "燻雞堡",
        "price": 50,
        "restaurant_id": 3
    },
    {
        "meal_id": 5,
        "meal_name": "里肌豬排堡",
        "price": 60,
        "restaurant_id": 3
    }
]
```

### -- 新增餐廳
- POST
- 路徑: `/api/add_new_restaurant`
- 傳送到伺服器的JSON必須包含:
```JSON=
{
    "restaurant_name":"餐廳名稱(不可重複)",
    "restaurant_address":"餐廳地址"
}
```
- 伺服器回傳JSON，顯示成功新增的餐點資料。

### -- 新增餐點
- POST
- 路徑: `/api/add_new_meal`
- 傳送到伺服器的JSON必須包含:
```JSON=
{
    "meal_name":"餐點名稱",
    "restaurant_id":"餐廳id(正整數)",
    "price":"價格(正整數)"
}
```
- 伺服器回傳JSON，顯示成功新增的餐點資料。

### -- 刪除餐廳
- DELETE
- 路徑: `/api/delete_restaurant`
- 傳送到伺服器的JSON必須包含: 
```JSON=
{
    "restaurant_id":"餐廳id(正整數)"
}
```
- 伺服器回傳JSON，顯示刪除成功。

### -- 刪除餐點
- DELETE
- 路徑: `/api/delete_meal`
- 傳送到伺服器的JSON必須包含: 
```JSON=
{
    "meal_id":"餐點id(正整數)"
}
```
- 伺服器回傳JSON，顯示刪除成功。

### -- 查詢某餐廳所有餐點
- POST
- 路徑: `/api/view_meals_of_restaurant`
- 傳送到伺服器的JSON必須包含: 
```JSON=
{
    "restaurant_name":"餐廳名稱"
}
```
- 伺服器回傳JSON，顯示刪除成功

### -- 新增使用者
- POST
- 路徑: `/api/add_new_user`
- 傳送到伺服器的JSON必須包含:
```JSON=
{
    "user_name":"使用者名稱(不可重複)",
    "password":"密碼(需要在前端加密)"
}
```
- 伺服器回傳JSON，顯示新增成功

### -- 刪除使用者
- DELETE
- 路徑: `/api/delete_user`
- 傳送到伺服器的JSON必須包含:
```JSON=
{
    "user_id":"使用者id(正整數)"
}
```
- 伺服器回傳JSON，顯示刪除成功

### -- 查詢所有使用者資訊
- GET
- 路徑: `/api/view_all_users`
- 伺服器回傳範例:
```JSON=
[
    {
        "user_id": 13,
        "user_name": "test_user",
        "user_state": "normal"
    },
    {
        "user_id": 14,
        "user_name": "派大星",
        "user_state": "normal"
    },
    {
        "user_id": 15,
        "user_name": "海綿寶寶",
        "user_state": "normal"
    }
]
```

### -- 核對使用者密碼
- POST
- 路徑: `/api/verify_user`
- 傳送到伺服器的JSON必須包含:
```JSON=
{
    "user_name":"使用者名稱",
    "password":"密碼(需要在前端加密)"
}
```
- 伺服器回傳範例:
```JSON=
{
    "message": "登入成功",
        "success": true,
        "user": {
            "user_id": 14,
            "user_name": "派大星",
            "user_state": "normal"
        }
}
```
- #### 說明
    - 這個功能目前可能有問題，因為還需要再跟前端協調匹配的加密以及驗證方式

### -- 新增訂單(pending)
- POST
- 路徑: `/api/add_new_order`
- 傳送到伺服器的JSON範例:
```JSON=
{
    "buyer_id":"15",
    "meals":
    [
        {
            "meal_id":"2",
            "quantity":9,
            "note":"不要酸黃瓜"
        },
        {
            "meal_id":"7",
            "quantity":2
        },
        {
            "meal_id":5,
            "quantity":1,
            "note":"多加番茄醬"
        }
    ]
}
```

- 伺服器回傳JSON，顯示成功與否

- #### 說明
    - 以數字10為例，JSON中可表示為"10"或10
    - 訂單狀態預設為`PENDING`

### -- 查看訂單(使用者id+時間)
- GET
- 路徑: `/api/view_order_by_stamp`
- 伺服器回傳範例:
```JSON=
{
    "buyer_id": "15",
    "timestamp": "2026-11-07 00:43:29"
},
{
    "buyer_id": "15",
    "timestamp": "2026-11-06 20:38:56"
},
{
    "buyer_id": "14",
    "timestamp": "2026-11-06 18:15:37"
}
```

### -- 查看訂單(同一單所有餐點)
- POST
- 路徑: `/api/view_meals_of_order`
- 傳送到伺服器的JSON範例:
```JSON=
{
    "buyer_id": "15",
    "timestamp": "2026-11-07 00:43:29"
}
```
- 伺服器回傳範例:
```JSON=
[
    {
        "buyer_id": 15,
        "meal_id": 2,
        "note": "不要酸黃瓜",
        "order_id": 6,
        "order_state": "pending",
        "quantity": 9,
        "timestamp": "2026-11-07 00:43:29"
    },
    {
        "buyer_id": 15,
        "meal_id": 7,
        "note": null,
        "order_id": 7,
        "order_state": "pending",
        "quantity": 2,
        "timestamp": "2026-11-07 00:43:29"
    },
    {
        "buyer_id": 15,
        "meal_id": 5,
        "note": "多加番茄醬",
        "order_id": 8,
        "order_state": "pending",
        "quantity": 1,
        "timestamp": "2026-11-07 00:43:29"
    }
]
```

### -- 新增Delivery資料
- POST
- 路徑: `/api/add_new_delivery`
- 傳送到伺服器的JSON範例:
```JSON=
{
    "buyer_id": "15",
    "timestamp": "2026-11-07 00:43:29",
    "pickup_address": "202基隆市中正區海濱里 電機資訊學院 一樓大門門口"
}
```
- 伺服器回傳JSON，顯示成功與否

### -- 查看對應訂單的Delivery資料
- POST
- 路徑: `/api/view_delivery`
- 傳送到伺服器的JSON範例:
```JSON=
{
    "buyer_id": "15",
    "timestamp": "2026-11-07 00:43:29"
}
```
- 伺服器回傳JSON範例: 
```JSON=
[
    {
        "buyer_id": 15,
        "deliverer_id": null,
        "pickup_address": "202基隆市中正區海濱里 電機資訊學院 一樓大門門口",
        "timestamp": "2026-11-06 20:38:56"
    }
]
```
- #### 說明
    - 預設`deliverer_id`為`null`

### -- 取消訂單(cancelled)
- POST
- 路徑: `/api/cancel_order`
- 傳送到伺服器的JSON範例:
```JSON=
{
    "buyer_id": "15",
    "timestamp": "2026-11-07 00:43:29"
}
```
- 伺服器回傳JSON，顯示成功與否
- #### 說明
    - 將對應訂單的狀態改為`CANCELLED`
    - 狀態為`CANCELLED`、`COMPLETED`的訂單不可取消

### -- 刪除訂單
- DELETE
- 路徑: `/api/delete_order`
- 傳送到伺服器的JSON範例:
```JSON=
{
    "buyer_id": "15",
    "timestamp": "2026-11-07 00:43:29"
}
```
- 伺服器回傳JSON，顯示成功與否

### -- 接單(processing)
- POST
- 路徑: `/api/process_order`
- 傳送到伺服器的JSON範例:
```JSON=
{
    "buyer_id": "15",
    "timestamp": "2026-11-07 00:43:29",
    "deliverer_id": "14"
}
```
- #### 說明
    - 將對應訂單狀態改為`PROCESSING`
    - 更改對應`Delivery`資料的`deliverer_id`
    - 只有狀態為`PENDING`的訂單可被承接

- 伺服器回傳JSON，顯示成功與否

### -- 完成訂單(complete)
- POST
- 路徑: `/api/complete_order`
- 傳送到伺服器的JSON範例:
```JSON=
{
    "buyer_id": "15",
    "timestamp": "2026-11-07 00:43:29"
}
```

- #### 說明
    - 將對應訂單狀態改為`COMPLETED`
    - 只有狀態為`PROCESSING`的訂單可被完成

- 伺服器回傳JSON，顯示成功與否

### -- 棄單
- POST
- 路徑: `/api/abandon_order`
- 傳送到伺服器的JSON範例:
```JSON=
{
    "buyer_id": "15",
    "timestamp": "2026-11-07 00:43:29"
}
```

- #### 說明
    - 將對應`Delivery`資料的`deliverer_id`改為`null`
    - 將對應訂單狀態改為`PENDING`
    - 只有狀態為`PROCESSING`的訂單可被放棄

- 伺服器回傳JSON，顯示成功與否



