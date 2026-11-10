import os

from dotenv import load_dotenv
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import URL, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text
from enum import Enum
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import traceback
import utils

load_dotenv()

app = Flask(__name__)

# MySQL 連線設定
app.config["SQLALCHEMY_DATABASE_URI"] = URL.create(
    drivername="mysql+pymysql",
    username=os.environ["DB_USER"],
    password=os.environ["DB_PASSWORD"],
    host=os.getenv("DB_HOST", "127.0.0.1"),
    port=int(os.getenv("DB_PORT", "3306")),
    database=os.environ["DB_NAME"],
    query={"charset": "utf8mb4"},
)

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# 餐廳
class Restaurants(db.Model):

    __tablename__ = "restaurants"
    restaurant_id = db.Column(db.Integer, primary_key=True)
    restaurant_name = db.Column(db.String(40), unique=True, nullable=False)
    restaurant_address = db.Column(db.String(80), nullable=False)

    def to_dict(self):
        return {
            "restaurant_id": self.restaurant_id,
            "restaurant_name": self.restaurant_name,
            "restaurant_address": self.restaurant_address,
        }

# 餐點
class Meals(db.Model):

    __tablename__ = "meals"
    meal_id = db.Column(db.Integer, primary_key=True)
    meal_name = db.Column(db.String(40), unique=False, nullable=False)

    # 外鍵：對應 restaurants 資料表的 id
    restaurant_id = db.Column(
        db.Integer,
        db.ForeignKey("restaurants.restaurant_id"),
        nullable=False
    )

    price = db.Column(db.Integer, nullable=False)

    def to_dict(self):
        return {
            "meal_id": self.meal_id,
            "meal_name": self.meal_name,
            "restaurant_id": self.restaurant_id,
            "price": self.price,
        }

#使用者狀態
class UserStatus(Enum):
    NORMAL = "normal"
    DELIVERER = "deliverer"

#使用者
class Users(db.Model):
    __tablename__ = "users"

    user_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_name = db.Column(db.String(20),unique=True,nullable=False)
    password = db.Column(db.String(255),nullable=False)
    user_state = db.Column(db.Enum(UserStatus),nullable=False,default=UserStatus.NORMAL)

    def set_password(self, password):
        print("password: ",password)
        self.password = password

    # 驗證密碼
    def check_password(self, password):
        return self.password == password

    # 切換為一般狀態
    def switch_to_normal(self):
        self.user_status = UserStatus.NORMAL

    # 切換為外送狀態
    def switch_to_deliverer(self):
        self.user_status = UserStatus.DELIVERER

#訂單狀態
class OrderState(Enum):
    IN_CART = "in_cart"       # 未下單
    PENDING = "pending"       # 等待處理
    PROCESSING = "processing" # 處理中
    COMPLETED = "completed"   # 已完成
    CANCELLED = "cancelled"   # 已取消

#訂單
class Orders(db.Model):
    __tablename__ = "orders"

    order_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    buyer_id = db.Column(db.Integer,db.ForeignKey("users.user_id"),nullable=False)
    meal_id = db.Column(db.Integer,db.ForeignKey("meals.meal_id"),nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    timestamp = db.Column(db.DateTime, nullable=False)
    note = db.Column(db.String(100), nullable=True)

    order_state = db.Column(
        db.Enum(OrderState),
        nullable=False,
        default=OrderState.PENDING
    )

    buyer = db.relationship("Users", backref="orders")
    meal = db.relationship("Meals", backref="orders")

#承接訂單
class Delivery(db.Model):
    __tablename__ = "delivery"

    buyer_id = db.Column(db.Integer,db.ForeignKey("users.user_id"),primary_key=True,nullable=False)
    timestamp = db.Column(db.DateTime,primary_key=True,nullable=False)
    deliverer_id = db.Column(db.Integer,db.ForeignKey("users.user_id"),nullable=True)
    pickup_address = db.Column(db.String(120),nullable=False)

    buyer = db.relationship(
        "Users",
        foreign_keys=[buyer_id],
        backref="delivery_requests"
    )

    deliverer = db.relationship(
        "Users",
        foreign_keys=[deliverer_id],
        backref="accepted_deliveries"
    )

# 測試後端是否正常運作
@app.get("/")
def index():
    return jsonify({"message": "Flask API is running"})


# 查詢所有餐廳
@app.get("/api/view_all_restaurants")
def get_restaurants():
    restaurants = db.session.execute(
        select(Restaurants).order_by(Restaurants.restaurant_id)
    ).scalars().all()

    return jsonify([
        restaurant.to_dict()
        for restaurant in restaurants
    ])


# 查詢所有餐點
@app.get("/api/view_all_meals")
def get_meals():
    meals = db.session.execute(
        select(Meals).order_by(Meals.meal_id)
    ).scalars().all()

    return jsonify([
        meal.to_dict()
        for meal in meals
    ])


# 新增餐廳
@app.post("/api/add_new_restaurant")
def create_restaurant():
    data, notJsonError = utils.get_json_object()

    if notJsonError:
        return notJsonError

    restaurant_name = data.get("restaurant_name")
    restaurant_address = data.get("restaurant_address")

    if not isinstance(restaurant_name, str) or not restaurant_name.strip():
        return jsonify({"error": "restaurant_name 不可為空"}), 400

    if not isinstance(restaurant_address, str) or not restaurant_address.strip():
        return jsonify({"error": "restaurant_address 不可為空"}), 400

    restaurant = Restaurants(
        restaurant_name=restaurant_name.strip(),
        restaurant_address=restaurant_address.strip()
    )

    db.session.add(restaurant)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "餐廳已存在"}), 409

    return jsonify(restaurant.to_dict()), 201


# 新增餐點
@app.post("/api/add_new_meal")
def create_meal():
    data, notJsonError = utils.get_json_object()

    if notJsonError:
        return notJsonError

    meal_name = data.get("meal_name")
    restaurant_id = data.get("restaurant_id")
    price = data.get("price")

    if not isinstance(meal_name, str) or not meal_name.strip():
        return jsonify({"error": "meal_name 不可為空"}), 400

    # 驗證restaurant_id
    restaurant_id, notPositiveNumErr = utils.validate_positive_int(restaurant_id,"restaurant_id")
    if notPositiveNumErr:
        return notPositiveNumErr

    # 驗證price
    price, notPositiveNumErr = utils.validate_positive_int(price,"price")
    if notPositiveNumErr:
        return notPositiveNumErr

    meal = Meals(
        meal_name=meal_name.strip(),
        restaurant_id=restaurant_id,
        price=price
    )

    db.session.add(meal)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "新增餐點失敗，請確認 restaurant_id 是否存在"}), 409

    return jsonify(meal.to_dict()), 201


# 刪除餐廳
@app.delete("/api/delete_restaurant")
def delete_restaurant():
    data, notJsonError = utils.get_json_object()

    if notJsonError:
        return notJsonError

    restaurant_id = data.get("restaurant_id")

    # 驗證restaurant_id
    restaurant_id, notPositiveNumErr = utils.validate_positive_int(restaurant_id,"restaurant_id")
    if notPositiveNumErr:
        return notPositiveNumErr

    restaurant = db.session.get(Restaurants, restaurant_id)

    if restaurant is None:
        return jsonify({"error": "無此餐廳id"}), 404

    try:
        db.session.delete(restaurant)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "餐廳仍有餐點，無法刪除"}), 409

    return jsonify({"message": "刪除成功"}), 200

# 刪除餐點
@app.delete("/api/delete_meal")
def delete_meal():
    data, notJsonError = utils.get_json_object()

    if notJsonError:
        return notJsonError

    meal_id = data.get("meal_id")

    # 驗證meal_id
    meal_id, notPositiveNumErr = utils.validate_positive_int(meal_id,"meal_id")
    if notPositiveNumErr:
        return notPositiveNumErr

    meal = db.session.get(Meals, meal_id)

    if meal is None:
        return jsonify({"error": "無此餐點id"}), 404

    try:
        db.session.delete(meal)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "此餐點已有訂單紀錄，無法刪除"}), 409

    return jsonify({"message": "刪除成功"}), 200

# 查詢餐廳所有餐點
@app.post("/api/view_meals_of_restaurant")
def get_restaurant_meals():
    data, notJsonError = utils.get_json_object()
    if notJsonError:
        return notJsonError

    restaurant_name = data.get("restaurant_name")

    if not isinstance(restaurant_name, str) or not restaurant_name.strip():
        return jsonify({"error": "restaurant_name 不可為空"}), 400

    restaurant = db.session.execute(
        select(Restaurants).where(
            Restaurants.restaurant_name == restaurant_name.strip()
        )
    ).scalar_one_or_none()

    if restaurant is None:
        return jsonify({"error": "無此餐廳"}), 404

    meals = db.session.execute(
        select(Meals)
        .where(Meals.restaurant_id == restaurant.restaurant_id)
        .order_by(Meals.meal_id)
    ).scalars().all()

    return jsonify([
        meal.to_dict()
        for meal in meals
    ]), 200

# 新增使用者
@app.post("/api/add_new_user")
def create_user():

    data, notJsonError = utils.get_json_object()

    if notJsonError:
        return notJsonError

    # 檢查必要欄位
    user_name = data.get("user_name")
    password = data.get("password")

    if not isinstance(user_name, str) or not user_name.strip():
        return jsonify({
            "success": False,
            "message": "user_name 不可為空"
        }), 400

    if not isinstance(password, str) or not password:
        return jsonify({
            "success": False,
            "message": "password 不可為空"
        }), 400

    user_name = user_name.strip()

    # 檢查使用者名稱是否已存在
    existing_user = Users.query.filter_by(
        user_name=user_name
    ).first()

    if existing_user:
        return jsonify({
            "success": False,
            "message": "使用者名稱已存在"
        }), 409

    # 建立使用者
    new_user = Users(
        user_name=user_name,
        user_state=UserStatus.NORMAL
    )

    # 設定密碼
    new_user.set_password(password)

    try:
        db.session.add(new_user)
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "使用者建立成功",
            "user_id": new_user.user_id
        }), 201

    except IntegrityError:
        db.session.rollback()

        return jsonify({
            "success": False,
            "message": "使用者名稱已存在"
        }), 409

    except Exception as e:
        db.session.rollback()
        return utils.general_exception_log(e, "建立使用者失敗")

# 刪除使用者
@app.delete("/api/delete_user")
def delete_user():

    data, notJsonError = utils.get_json_object()
    if notJsonError:
        return notJsonError

    user_id = data.get("user_id")

    # 驗證user_id
    user_id, notPositiveNumErr = utils.validate_positive_int(user_id,"user_id")
    if notPositiveNumErr:
        return notPositiveNumErr

    user = db.session.get(Users, user_id)

    if user is None:
        return jsonify({"error": "無此使用者id"}), 404

    try:
        db.session.delete(user)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "此使用者有下單或接單紀錄未完成，無法刪除"}), 409

    return jsonify({"message": "刪除成功"}), 200

# 查看所有使用者
@app.get("/api/view_all_users")
def view_all_users():

    try:
        # 查詢所有使用者
        users = db.session.execute(
            select(Users).order_by(Users.user_id)
        ).scalars().all()

        return jsonify([
            {
                "user_id": user.user_id,
                "user_name": user.user_name,
                "user_state": user.user_state.value
            }
            for user in users
        ]), 200

    except Exception as e:
        db.session.rollback()
        return utils.general_exception_log(e, "查詢使用者失敗")

# 核對使用者密碼（登入）
@app.post("/api/verify_user")
def verify_user():

    data, notJsonError = utils.get_json_object()

    if notJsonError:
        return notJsonError

    user_name = data.get("user_name")
    password = data.get("password")

    # 檢查必要欄位
    if not isinstance(user_name, str) or not user_name.strip():
        return jsonify({
            "success": False,
            "message": "user_name 不可為空"
        }), 400

    if not isinstance(password, str) or not password:
        return jsonify({
            "success": False,
            "message": "password 不可為空"
        }), 400

    user_name = user_name.strip()

    # 查詢使用者
    user = Users.query.filter_by(
        user_name=user_name
    ).first()

    # 使用者不存在
    if user is None:
        return jsonify({
            "success": False,
            "message": "使用者名稱或密碼錯誤"
        }), 401

    # 驗證密碼
    if not user.check_password(password):
        return jsonify({
            "success": False,
            "message": "使用者名稱或密碼錯誤"
        }), 401

    # 檢查使用者狀態
    if user.user_state not in (
        UserStatus.NORMAL,
        UserStatus.DELIVERER
    ):
        return jsonify({
            "success": False,
            "message": "帳號狀態異常"
        }), 403

    # 登入成功
    return jsonify({
        "success": True,
        "message": "登入成功",
        "user": {
            "user_id": user.user_id,
            "user_name": user.user_name,
            "user_state": user.user_state.value
        }
    }), 200

# 新增訂單(pending)
@app.post("/api/add_new_order")
def add_new_order():

    data, notJsonError = utils.get_json_object()

    if notJsonError:
        return notJsonError


    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "請提供 JSON 物件"
        }), 400

    buyer_id = data.get("buyer_id")
    meals = data.get("meals")

    # 驗證buyer_id
    buyer_id, notPositiveNumErr = utils.validate_positive_int(buyer_id,"buyer_id")
    if notPositiveNumErr:
        return notPositiveNumErr

    # 確認 meals 是非空陣列
    if not isinstance(meals, list) or len(meals) == 0:
        return jsonify({
            "success": False,
            "message": "meals 必須是非空陣列"
        }), 400

    # 確認買家是否存在
    buyer = db.session.get(Users, buyer_id)

    if buyer is None:
        return jsonify({
            "success": False,
            "message": "buyer_id 不存在"
        }), 404

    # 取得目前時間，同一次新增的訂單共用這個時間
    timestamp = datetime.now()

    new_orders = []

    # 逐一處理每個餐點
    for meal_data in meals:

        if not isinstance(meal_data, dict):
            return jsonify({
                "success": False,
                "message": "meals 中的資料格式錯誤"
            }), 400

        meal_id = meal_data.get("meal_id")
        quantity = meal_data.get("quantity")
        note = meal_data.get("note")

        # 驗證meal_id
        meal_id, notPositiveNumErr = utils.validate_positive_int(meal_id,"meal_id")
        if notPositiveNumErr:
            return notPositiveNumErr

        # 驗證quantity
        quantity, notPositiveNumErr = utils.validate_positive_int(quantity,"quantity")
        if notPositiveNumErr:
            return notPositiveNumErr

        # 驗證 note
        if note is not None and (
            not isinstance(note, str) or len(note) > 100
        ):
            return jsonify({
                "success": False,
                "message": "note 格式錯誤或超過 100 個字元"
            }), 400

        # 確認餐點是否存在
        meal = db.session.get(Meals, meal_id)

        if meal is None:
            return jsonify({
                "success": False,
                "message": f"meal_id {meal_id} 不存在"
            }), 404

        # 建立訂單
        new_order = Orders(
            buyer_id=buyer_id,
            meal_id=meal_id,
            quantity=quantity,
            timestamp=timestamp,
            note=note,
            order_state=OrderState.PENDING
        )

        new_orders.append(new_order)

    # 將所有訂單一次寫入資料庫
    try:
        db.session.add_all(new_orders)
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "訂單建立成功",
            "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "orders": [
                {
                    "order_id": order.order_id,
                    "buyer_id": order.buyer_id,
                    "meal_id": order.meal_id,
                    "quantity": order.quantity,
                    "timestamp": order.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    "note": order.note,
                    "order_state": order.order_state.value
                }
                for order in new_orders
            ]
        }), 201

    except IntegrityError:
        db.session.rollback()

        return jsonify({
            "success": False,
            "message": "資料庫資料寫入失敗"
        }), 400

    except Exception as e:
        db.session.rollback()
        utils.general_exception_log(e, "建立訂單失敗")

# 查看訂單(使用者id+時間)
@app.get("/api/view_order_by_stamp")
def view_order_by_stamp():

    try:
        order_stamps = db.session.execute(
            select(
                Orders.buyer_id,
                Orders.timestamp
            ).distinct().order_by(
                Orders.timestamp.desc(),
                Orders.buyer_id
            )
        ).all()

        return jsonify([
            {
                "buyer_id": str(buyer_id),
                "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S")
            }
            for buyer_id, timestamp in order_stamps
        ]), 200

    except Exception:
        return jsonify({
            "success": False,
            "message": "查詢訂單紀錄失敗"
        }), 500

# 查看訂單(同一單所有餐點)
@app.post("/api/view_meals_of_order")
def view_meals_of_order():

    data, notJsonError = utils.get_json_object()
    if notJsonError:
        return notJsonError

    buyer_id = data.get("buyer_id")
    timestamp = data.get("timestamp")

    # 驗證buyer_id
    buyer_id, notPositiveNumErr = utils.validate_positive_int(buyer_id,"buyer_id")
    if notPositiveNumErr:
        return notPositiveNumErr

    # 驗證timestamp
    timestamp, timestampTypeErr = utils.validate_timestamp(timestamp)
    if timestampTypeErr:
        return timestampTypeErr

    try:
        # 查詢符合 buyer_id 和 timestamp 的所有訂單
        orders = db.session.execute(
            select(Orders)
            .where(
                Orders.buyer_id == buyer_id,
                Orders.timestamp == timestamp
            )
            .order_by(Orders.order_id)
        ).scalars().all()

        if not orders:
            return jsonify({
                "success": False,
                "message": "找不到符合條件的訂單"
            }), 404

        return jsonify([
            {
                "order_id": order.order_id,
                "buyer_id": order.buyer_id,
                "meal_id": order.meal_id,
                "quantity": order.quantity,
                "timestamp": order.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "note": order.note,
                "order_state": order.order_state.value
            }
            for order in orders
        ]), 200

    except Exception as e:
        db.session.rollback()
        return utils.general_exception_log(e,"查詢訂單失敗")

# 新增Delivery資料
@app.post("/api/add_new_delivery")
def add_new_delivery():

    data, notJsonError = utils.get_json_object()

    if notJsonError:
        return notJsonError

    pickup_address = data.get("pickup_address")
    buyer_id = data.get("buyer_id")
    timestamp = data.get("timestamp")

    # 驗證 pickup_address
    if (
        not isinstance(pickup_address, str)
        or not pickup_address.strip()
        or len(pickup_address) > 120
    ):
        return jsonify({
            "success": False,
            "message": "pickup_address 格式錯誤或超過 120 個字元"
        }), 400

    pickup_address = pickup_address.strip()

    # 驗證buyer_id
    buyer_id, notPositiveNumErr = utils.validate_positive_int(buyer_id,"buyer_id")
    if notPositiveNumErr:
        return notPositiveNumErr
    
    # 驗證timestamp
    timestamp, timestampTypeErr = utils.validate_timestamp(timestamp)
    if timestampTypeErr:
        return timestampTypeErr

    try:
        # 確認買家是否存在
        buyer = db.session.get(Users, buyer_id)

        if buyer is None:
            return jsonify({
                "success": False,
                "message": "buyer_id 不存在"
            }), 404

        # 確認對應的訂單是否存在
        orders = db.session.execute(
            select(Orders).where(
                Orders.buyer_id == buyer_id,
                Orders.timestamp == timestamp
            )
        ).scalars().all()

        if not orders:
            return jsonify({
                "success": False,
                "message": "找不到對應的訂單"
            }), 404

        # 建立 Delivery
        new_delivery = Delivery(
            buyer_id=buyer_id,
            timestamp=timestamp,
            deliverer_id=None,
            pickup_address=pickup_address
        )

        db.session.add(new_delivery)
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "外送需求建立成功",
            "delivery": {
                "buyer_id": new_delivery.buyer_id,
                "timestamp": new_delivery.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "deliverer_id": new_delivery.deliverer_id,
                "pickup_address": new_delivery.pickup_address
            }
        }), 201

    except IntegrityError as e:
        db.session.rollback()

        print(f"IntegrityError: {str(e)}")
        traceback.print_exc()

        return jsonify({
            "success": False,
            "message": "資料庫資料寫入失敗",
            "error_type": type(e).__name__,
            "error_message": str(e)
        }), 409

    except Exception as e:
        db.session.rollback()
        return utils.general_exception_log(e,"建立外送需求失敗")

# 查看對應訂單Delivery資料
@app.post("/api/view_delivery")
def view_delivery():
    data, notJsonError = utils.get_json_object()
    if notJsonError:
        return notJsonError

    buyer_id = data.get("buyer_id")
    timestamp = data.get("timestamp")

    # 驗證buyer_id
    buyer_id, notPositiveNumErr = utils.validate_positive_int(buyer_id,"buyer_id")
    if notPositiveNumErr:
        return notPositiveNumErr

    # 驗證timestamp
    timestamp, timestampTypeErr = utils.validate_timestamp(timestamp)
    if timestampTypeErr:
        return timestampTypeErr

    try:
        # 查詢符合 buyer_id 和 timestamp 的所有訂單
        deliverys = db.session.execute(
            select(Delivery)
            .where(
                Delivery.buyer_id == buyer_id,
                Delivery.timestamp == timestamp
            )
            .order_by(Delivery.buyer_id)
        ).scalars().all()

        if not deliverys:
            return jsonify({
                "success": False,
                "message": "找不到符合條件的外送資訊"
            }), 404

        return jsonify([
            {
                "buyer_id": delivery.buyer_id,
                "deliverer_id": delivery.deliverer_id,
                "pickup_address": delivery.pickup_address,
                "timestamp": delivery.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            }
            for delivery in deliverys
        ]), 200

    except Exception as e:
        db.session.rollback()
        return utils.general_exception_log(e,"查詢外送資訊失敗")

# 取消訂單(cancelled)
@app.post("/api/cancel_order")
def cancel_order():

    data, notJsonError = utils.get_json_object()

    if notJsonError:
        return notJsonError

    buyer_id = data.get("buyer_id")
    timestamp = data.get("timestamp")

    # 驗證buyer_id
    buyer_id, notPositiveNumErr = utils.validate_positive_int(buyer_id,"buyer_id")
    if notPositiveNumErr:
        return notPositiveNumErr
    
    # 驗證timestamp
    timestamp, timestampTypeErr = utils.validate_timestamp(timestamp)
    if timestampTypeErr:
        return timestampTypeErr

    try:
        # 查詢符合 buyer_id 和 timestamp 的所有訂單
        orders = db.session.execute(
            select(Orders).where(
                Orders.buyer_id == buyer_id,
                Orders.timestamp == timestamp
            )
        ).scalars().all()

        if not orders:
            return jsonify({
                "success": False,
                "message": "找不到符合條件的訂單"
            }), 404

        # 確認所有訂單目前都可以取消
        if any(
            order.order_state in (
                OrderState.COMPLETED,
                OrderState.CANCELLED
            )
            for order in orders
        ):
            return jsonify({
                "success": False,
                "message": "訂單已完成或已取消，無法取消"
            }), 409

        # 將所有對應訂單狀態修改為 CANCELLED
        for order in orders:
            order.order_state = OrderState.CANCELLED

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "訂單取消成功",
            "buyer_id": buyer_id,
            "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "cancelled_orders": [
                {
                    "order_id": order.order_id,
                    "meal_id": order.meal_id,
                    "quantity": order.quantity,
                    "order_state": order.order_state.value
                }
                for order in orders
            ]
        }), 200

    except Exception as e:
        db.session.rollback()
        utils.general_exception_log(e,"取消訂單失敗")

# 刪除訂單
@app.delete("/api/delete_order")
def delete_order():

    # 確認收到的是 JSON 物件
    data, error = utils.get_json_object()
    if error:
        return error

    buyer_id = data.get("buyer_id")
    timestamp = data.get("timestamp")

    # 驗證buyer_id
    buyer_id, notPositiveNumErr = utils.validate_positive_int(buyer_id,"buyer_id")
    if notPositiveNumErr:
        return notPositiveNumErr
    
    # 驗證timestamp
    timestamp, timestampTypeErr = utils.validate_timestamp(timestamp)
    if timestampTypeErr:
        return timestampTypeErr

    try:
        # 刪除符合條件的所有訂單
        deleted_count = Orders.query.filter_by(
            buyer_id=buyer_id,
            timestamp=timestamp
        ).delete(synchronize_session=False)

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "訂單刪除成功",
            "deleted_count": deleted_count
        }), 200

    except Exception as e:
        db.session.rollback()
        return utils.general_exception_log(e,"刪除訂單時發生錯誤" )

# 接單(processing)
@app.post("/api/process_order")
def process_order():

    data = request.get_json(silent=True)
    # 確認收到的是 JSON 物件
    data, error = utils.get_json_object()
    if error:
        return error

    buyer_id = data.get("buyer_id")
    timestamp = data.get("timestamp")
    deliverer_id = data.get("deliverer_id")

    # 驗證buyer_id
    buyer_id, notPositiveNumErr = utils.validate_positive_int(buyer_id,"buyer_id")
    if notPositiveNumErr:
        return notPositiveNumErr
    
    # 驗證timestamp
    timestamp, timestampTypeErr = utils.validate_timestamp(timestamp)
    if timestampTypeErr:
        return timestampTypeErr

    # 驗證 deliverer_id
    deliverer_id, notPositiveNumErr = utils.validate_positive_int(deliverer_id,"deliverer_id")
    if notPositiveNumErr:
        return notPositiveNumErr

    try:
        # 確認外送員是否存在
        deliverer = db.session.get(Users, deliverer_id)

        if deliverer is None:
            return jsonify({
                "success": False,
                "message": "deliverer_id 不存在"
            }), 404

        # # 確認使用者是否具有外送員身分
        # if deliverer.user_state != UserStatus.DELIVERER:
        #     return jsonify({
        #         "success": False,
        #         "message": "該使用者不是外送員"
        #     }), 403

        # 查詢符合 buyer_id 和 timestamp 的所有訂單
        orders = db.session.execute(
            select(Orders).where(
                Orders.buyer_id == buyer_id,
                Orders.timestamp == timestamp
            )
        ).scalars().all()

        if not orders:
            return jsonify({
                "success": False,
                "message": "找不到符合條件的訂單"
            }), 404

        # 確認訂單狀態是否允許進入 PROCESSING
        invalid_orders = [
            order for order in orders if order.order_state is not OrderState.PENDING
        ]

        if invalid_orders:
            return jsonify({
                "success": False,
                "message": "訂單無法被承接或已經被承接",
                "invalid_orders": [
                    {
                        "order_id": order.order_id,
                        "order_state": order.order_state.value
                    }
                    for order in invalid_orders
                ]
            }), 409

        # 查詢對應的 Delivery
        delivery = db.session.execute(
            select(Delivery).where(
                Delivery.buyer_id == buyer_id,
                Delivery.timestamp == timestamp
            )
        ).scalar_one_or_none()

        if delivery is None:
            return jsonify({
                "success": False,
                "message": "找不到對應的外送紀錄"
            }), 404

        # 確認是否已經有其他外送員承接
        if (
            delivery.deliverer_id is not None
            and delivery.deliverer_id != deliverer_id
        ):
            return jsonify({
                "success": False,
                "message": "此訂單已由其他外送員承接"
            }), 409

        # 修改所有符合條件的訂單狀態
        for order in orders:
            order.order_state = OrderState.PROCESSING

        # 修改對應的外送員 ID
        delivery.deliverer_id = deliverer_id

        # 一次提交兩張資料表的修改
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "訂單承接成功",
            "buyer_id": buyer_id,
            "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "deliverer_id": delivery.deliverer_id,
            "processed_orders": [
                {
                    "order_id": order.order_id,
                    "meal_id": order.meal_id,
                    "quantity": order.quantity,
                    "order_state": order.order_state.value
                }
                for order in orders
            ]
        }), 200

    except Exception as e:
        db.session.rollback()

        print(f"錯誤類型：{type(e).__name__}")
        print(f"錯誤訊息：{str(e)}")
        traceback.print_exc()

        return jsonify({
            "success": False,
            "message": "承接訂單失敗",
            "error_type": type(e).__name__,
            "error_message": str(e)
        }), 500

# 完成訂單(completed)
@app.post("/api/complete_order")
def complete_order():

    data = request.get_json(silent=True)
    # 確認收到的是 JSON 物件
    data, error = utils.get_json_object()
    if error:
        return error

    buyer_id = data.get("buyer_id")
    timestamp = data.get("timestamp")

    # 驗證buyer_id
    buyer_id, notPositiveNumErr = utils.validate_positive_int(buyer_id,"buyer_id")
    if notPositiveNumErr:
        return notPositiveNumErr
    
    # 驗證timestamp
    timestamp, timestampTypeErr = utils.validate_timestamp(timestamp)
    if timestampTypeErr:
        return timestampTypeErr

    try:
        # 查詢符合 buyer_id 和 timestamp 的所有訂單
        orders = db.session.execute(
            select(Orders).where(
                Orders.buyer_id == buyer_id,
                Orders.timestamp == timestamp
            )
        ).scalars().all()

        if not orders:
            return jsonify({
                "success": False,
                "message": "找不到符合條件的訂單"
            }), 404

        # 確認訂單狀態是否允許進入 COMPLETED
        invalid_orders = [
            order for order in orders
            if order.order_state is not 
                OrderState.PROCESSING
        ]
        
        if invalid_orders:
            return jsonify({
                "success": False,
                "message": "訂單無法完成",
                "invalid_orders": [
                    {
                        "order_id": order.order_id,
                        "order_state": order.order_state.value
                    }
                    for order in invalid_orders
                ]
            }), 409

        # 確認是否有已取消的訂單
        if any(
            order.order_state == OrderState.COMPLETED
            for order in orders
        ):
            return jsonify({
                "success": False,
                "message": "訂單已完成"
            }), 409

        # 將所有符合條件的訂單狀態修改為 COMPLETED
        for order in orders:
            order.order_state = OrderState.COMPLETED

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "訂單完成",
            "buyer_id": buyer_id,
            "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "completed_orders": [
                {
                    "order_id": order.order_id,
                    "meal_id": order.meal_id,
                    "quantity": order.quantity,
                    "order_state": order.order_state.value
                }
                for order in orders
            ]
        }), 200

    except Exception as e:
        db.session.rollback()
        return utils.general_exception_log(e, "完成訂單失敗")

# 棄單
@app.post("/api/abandon_order")
def abandon_order():

    data = request.get_json(silent=True)
    # 確認收到的是 JSON 物件
    data, error = utils.get_json_object()
    if error:
        return error

    buyer_id = data.get("buyer_id")
    timestamp = data.get("timestamp")

    # 驗證buyer_id
    buyer_id, notPositiveNumErr = utils.validate_positive_int(buyer_id,"buyer_id")
    if notPositiveNumErr:
        return notPositiveNumErr
    
    # 驗證timestamp
    timestamp, timestampTypeErr = utils.validate_timestamp(timestamp)
    if timestampTypeErr:
        return timestampTypeErr

    try:
        # 查詢符合 buyer_id 和 timestamp 的所有訂單
        orders = db.session.execute(
            select(Orders).where(
                Orders.buyer_id == buyer_id,
                Orders.timestamp == timestamp
            )
        ).scalars().all()

        if not orders:
            return jsonify({
                "success": False,
                "message": "找不到符合條件的訂單"
            }), 404

        # 確認所有訂單狀態都是 PROCESSING
        if not all(
            order.order_state == OrderState.PROCESSING
            for order in orders
        ):
            return jsonify({
                "success": False,
                "message": "只有所有訂單狀態皆為 PROCESSING 時，才能放棄承接",
                "order_statees": [
                    {
                        "order_id": order.order_id,
                        "order_state": order.order_state.value
                    }
                    for order in orders
                ]
            }), 409

        # 查詢對應的 Delivery
        delivery = db.session.execute(
            select(Delivery).where(
                Delivery.buyer_id == buyer_id,
                Delivery.timestamp == timestamp
            )
        ).scalar_one_or_none()

        if delivery is None:
            return jsonify({
                "success": False,
                "message": "找不到對應的外送紀錄"
            }), 404

        # 將所有訂單狀態改回 PENDING
        for order in orders:
            order.order_state = OrderState.PENDING

        # 清除外送員 ID
        delivery.deliverer_id = None

        # 一次提交兩張資料表的修改
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "已放棄承接訂單",
            "buyer_id": buyer_id,
            "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "deliverer_id": delivery.deliverer_id,
            "updated_orders": [
                {
                    "order_id": order.order_id,
                    "meal_id": order.meal_id,
                    "quantity": order.quantity,
                    "order_state": order.order_state.value
                }
                for order in orders
            ]
        }), 200

    except Exception as e:
        db.session.rollback()
        return utils.general_exception_log(e, "放棄承接訂單失敗")


if __name__ == "__main__":
    with app.app_context():
        db.create_all()

    app.run(debug=True, port=5000)