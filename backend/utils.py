from flask import request, jsonify
from datetime import datetime
import traceback

def get_json_object():
    """
    確認請求內容是否為 JSON 物件。

    Returns:
        tuple:
            (data, None)       -> 成功，data 為 JSON dict
            (None, error_resp) -> 失敗，error_resp 為錯誤回應
    """

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return None, (jsonify({
            "success": False,
            "message": "請提供 JSON 物件"
        }), 400)

    return data, None

def validate_positive_int(value, field_name="id"):
    """
    驗證並轉換數值為正整數
    :param value: 欲驗證的值 (可以是 int 或 str)
    :param field_name: 欄位名稱，用於錯誤訊息提示
    :return: (parsed_value, None) 如果成功；(None, error_response) 如果失敗
    """
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        # 注意這裡：必須把 jsonify(...) 和 400 包成一個 Tuple 回傳，前面不能有多餘的逗號
        return None, (jsonify({
            "success": False,
            "message": f"{field_name} 格式錯誤"
        }), 400)

    try:
        parsed_value = int(value)
    except (ValueError, TypeError):
        # 這裡就是當傳入包含英文的字串（如 "abc"）時會進來的區塊
        return None, (jsonify({
            "success": False,
            "message": f"{field_name} 必須是正整數"
        }), 400)

    if parsed_value <= 0:
        return None, (jsonify({
            "success": False,
            "message": f"{field_name} 必須是正整數"
        }), 400)

    return parsed_value, None

def validate_timestamp(value, field_name="timestamp", date_format="%Y-%m-%d %H:%M:%S"):
    """
    驗證並轉換時間戳字串
    :param value: 欲驗證的值 (應為 str)
    :param field_name: 欄位名稱，用於錯誤訊息提示
    :param date_format: 預期的時間格式，預設為 "%Y-%m-%d %H:%M:%S"
    :return: (parsed_datetime, None) 如果成功；(None, error_response) 如果失敗
    """
    if not isinstance(value, str):
        return None, (jsonify({
            "success": False,
            "message": f"{field_name} 必須是字串"
        }), 400)

    try:
        # 成功解析後會回傳 datetime 物件，方便後續資料庫寫入或比較
        parsed_value = datetime.strptime(value, date_format)
    except ValueError:
        return None, (jsonify({
            "success": False,
            "message": f"{field_name} 格式錯誤，應為 YYYY-MM-DD HH:MM:SS"
        }), 400)

    return parsed_value, None


def general_exception_log(e, err_message="系統發生未預期的錯誤"):
    """
    統一處理例外錯誤：印出詳細追蹤資訊並回傳 500 錯誤回應
    :param e: 捕捉到的例外物件 (Exception)
    :param err_message: 給前端的自訂提示訊息
    :return: (jsonify_response, 500)
    """
    print(f"錯誤類型：{type(e).__name__}")
    print(f"錯誤訊息：{str(e)}")
    traceback.print_exc()
    print("=====================================")

    return jsonify({
        "success": False,
        "message": err_message,
        "error_type": type(e).__name__,
        "error_message": str(e)
    }), 500