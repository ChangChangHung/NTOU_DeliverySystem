
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="海大餐飲外送系統 API")

# 第一階段使用假資料，之後可改成從資料庫讀取
restaurants = [
    {
        "id": 1,
        "name": "海大美食餐廳",
        "description": "校園人氣餐廳",
    },
    {
        "id": 2,
        "name": "海洋便當店",
        "description": "快速便當與套餐",
    },
]

menus = {
    1: [
        {"id": 101, "name": "雞腿飯", "price": 100},
        {"id": 102, "name": "豬排飯", "price": 90},
    ],
    2: [
        {"id": 201, "name": "招牌便當", "price": 85},
        {"id": 202, "name": "雞肉便當", "price": 80},
    ],
}

# 暫存訂單：伺服器重啟後會清空
orders = []
next_order_id = 1


class OrderItem(BaseModel):
    item_id: int
    quantity: int = Field(gt=0, le=99)


class OrderRequest(BaseModel):
    restaurant_id: int
    items: list[OrderItem] = Field(min_length=1)
    address: str = Field(min_length=1, max_length=300)


@app.get("/")
def root():
    return {"message": "海大餐飲外送系統 API 運作中"}


@app.get("/api/restaurants")
def get_restaurants():
    return restaurants


@app.get("/api/restaurants/{restaurant_id}/menu")
def get_menu(restaurant_id: int):
    if restaurant_id not in menus:
        raise HTTPException(status_code=404, detail="找不到餐廳")

    return menus[restaurant_id]


@app.post("/api/orders", status_code=201)
def create_order(order: OrderRequest):
    global next_order_id

    if order.restaurant_id not in menus:
        raise HTTPException(status_code=404, detail="找不到餐廳")

    # 後端根據自己的菜單查價格，不採用前端傳來的價格
    menu_by_id = {
        item["id"]: item
        for item in menus[order.restaurant_id]
    }

    order_items = []
    total = 0

    for requested_item in order.items:
        menu_item = menu_by_id.get(requested_item.item_id)

        if menu_item is None:
            raise HTTPException(
                status_code=400,
                detail=f"餐點 ID {requested_item.item_id} 不屬於這家餐廳",
            )

        subtotal = menu_item["price"] * requested_item.quantity
        total += subtotal

        order_items.append({
            "item_id": menu_item["id"],
            "name": menu_item["name"],
            "price": menu_item["price"],
            "quantity": requested_item.quantity,
            "subtotal": subtotal,
        })

    new_order = {
        "id": next_order_id,
        "restaurant_id": order.restaurant_id,
        "restaurant_name": next(
            r["name"] for r in restaurants
            if r["id"] == order.restaurant_id
        ),
        "items": order_items,
        "address": order.address.strip(),
        "total": total,
        "status": "Pending",
    }

    if not new_order["address"]:
        raise HTTPException(status_code=422, detail="請輸入送餐地址")

    orders.append(new_order)
    next_order_id += 1

    return {
        "message": "訂單建立成功",
        "order": new_order,
    }


@app.get("/api/orders/pending")
def get_pending_orders():
    return [
        order for order in orders
        if order["status"] == "Pending"
    ]
