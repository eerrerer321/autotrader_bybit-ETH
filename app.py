from flask import Flask, request, jsonify
from pybit.unified_trading import HTTP
import os

app = Flask(__name__)

api_key = os.getenv("BYBIT_API_KEY")
api_secret = os.getenv("BYBIT_API_SECRET")

session = HTTP(
    api_key=api_key,
    api_secret=api_secret,
    testnet=False  # 若用測試網這邊改成 True
)

@app.route("/")
def index():
    return "Bybit Webhook is running"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    print("收到訊號：", data)

    action = data.get("data", {}).get("action")
    symbol = data.get("symbol", "ETHUSDT")
    qty = 0.05
    side = "Buy" if action in ["buy", "buy_add"] else "Sell"

    if action in ["buy", "short"]:
        session.place_order(
            category="linear",
            symbol=symbol,
            side=side,
            order_type="Market",
            qty=qty,
            time_in_force="GoodTillCancel"
        )

    elif action in ["sell", "cover", "sell_add_exit", "cover_add_exit"]:
        session.place_order(
            category="linear",
            symbol=symbol,
            side="Sell" if action.startswith("sell") else "Buy",
            order_type="Market",
            qty=qty,
            reduce_only=True,
            time_in_force="GoodTillCancel"
        )

    return jsonify({"status": "success"})
