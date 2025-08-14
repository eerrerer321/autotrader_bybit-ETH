"""
🏆 ETH 4小時自動交易策略 - 實時交易版本 (移動停損優化版)
🎯 參數來源: 2020-2025年優化結果 (穩定性評分48.38, 獲利5371.25 USDT)
📊 策略特點: 95.45%季度獲利率, 54.74%平均勝率, 14.06%最大回撤
🔧 停損機制: 固定停損(4小時檢查) + 移動停損(每分鐘檢查)
"""

import pandas as pd
from datetime import datetime, timedelta
import os
import time
import ccxt
from dotenv import load_dotenv
import json

# 載入 .env 檔案中的環境變數
load_dotenv()

# --- 🔧 策略配置參數 (集中管理) ---
# 交易所設定
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")
SYMBOL = "ETH/USDT"
TIMEFRAME = "4h"

# 資金管理設定
DEFAULT_QTY_PERCENT = 70  # 每次交易使用帳戶可用餘額的百分比
LEVER = 1 # 如果需要槓桿，從此修改，並在bybitAPP也修改為相同槓桿倍數

# 🏆 最佳策略參數 (2020-2025年優化結果，穩定性評分48.38)
STRATEGY_PARAMS = {
    "adx_threshold": 26,  # 更嚴格的趨勢判斷
    "long_fixed_stop_loss_percent": 0.019,  # 1.9%
    "long_trailing_activate_profit_percent": 0.011,  # 1.1%
    "long_trailing_pullback_percent": 0.054,  # 5.4%
    "long_trailing_min_profit_percent": 0.011,  # 1.1%
    "short_fixed_stop_loss_percent": 0.013,  # 1.3%
    "short_trailing_activate_profit_percent": 0.019,  # 1.9%
    "short_trailing_pullback_percent": 0.018,  # 1.8%
    "short_trailing_min_profit_percent": 0.016,  # 1.6%
}

# 系統設定
TRADE_SLEEP_SECONDS = 60  # 每隔多久檢查一次新K線 (60秒檢查一次)
FETCH_KLINE_LIMIT = 300  # 獲取多少根K線用於指標計算 (確保涵蓋EMA200所需的數據量)

# 定義保存狀態的檔案路徑
STATE_FILE = "strategy_state.json"


# --- 1. 數據載入 (從 Bybit API 獲取數據) ---
def fetch_bybit_klines(symbol, timeframe, limit=FETCH_KLINE_LIMIT):
    """
    從 Bybit 獲取指定交易對和時間週期的 K 線數據。
    """
    exchange = ccxt.bybit(
        {
            "apiKey": BYBIT_API_KEY,
            "secret": BYBIT_API_SECRET,
            "sandbox": False,  # 實際交易模式
            "options": {
                "defaultType": "future",  # 或者 'spot', 'margin' 等，根據您的交易類型設定
                "adjustForTimeDifference": True,  # 自動調整時間差
                "recvWindow": 120000,  # 增加接收窗口時間到2分鐘
            },
        }
    )

    # 同步時間
    try:
        exchange.load_time_difference()
    except:
        pass

    # 載入市場資訊，ccxt 需要知道市場資訊才能正確處理交易對
    exchange.load_markets()

    try:
        # 獲取 K 線數據
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(
            ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        df.columns = [col.lower() for col in df.columns]  # 統一列名為小寫
        return df
    except ccxt.NetworkError as e:
        print(f"網路錯誤: {e}")
        return pd.DataFrame()
    except ccxt.ExchangeError as e:
        print(f"交易所錯誤: {e}")
        return pd.DataFrame()
    except Exception as e:
        print(f"獲取 K 線數據時發生未知錯誤: {e}")
        return pd.DataFrame()


# --- 2. 指標計算 ---
def calculate_ema(series, period):
    """計算指數移動平均線"""
    return series.ewm(span=period, adjust=False).mean()


def calculate_adx(high, low, close, period=14):
    """計算ADX指標 - 使用標準的Wilder平滑法"""
    # 計算True Range
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # 計算方向移動
    plus_dm = high.diff()
    minus_dm = -low.diff()

    plus_dm[plus_dm < 0] = 0
    minus_dm[minus_dm < 0] = 0

    # 當+DM > -DM時，-DM = 0；當-DM > +DM時，+DM = 0
    plus_dm[(plus_dm <= minus_dm)] = 0
    minus_dm[(minus_dm <= plus_dm)] = 0

    # 使用Wilder平滑法 (alpha = 1/period)
    alpha = 1.0 / period

    # 計算平滑的TR和DM (使用指數移動平均)
    atr = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_dm_smooth = plus_dm.ewm(alpha=alpha, adjust=False).mean()
    minus_dm_smooth = minus_dm.ewm(alpha=alpha, adjust=False).mean()

    # 計算DI
    plus_di = 100 * (plus_dm_smooth / atr)
    minus_di = 100 * (minus_dm_smooth / atr)

    # 計算DX和ADX
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
    adx = dx.ewm(alpha=alpha, adjust=False).mean()

    return adx, plus_di, minus_di


def calculate_rsi(close, period=14):
    """計算RSI指標"""
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calculate_macd(close, fast=12, slow=26, signal=9):
    """計算MACD指標"""
    ema_fast = calculate_ema(close, fast)
    ema_slow = calculate_ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calculate_ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def calculate_indicators(df):
    """計算所有技術指標"""
    df = df.copy()

    # EMA指標
    df["ema90"] = calculate_ema(df["close"], 90)
    df["ema200"] = calculate_ema(df["close"], 200)

    # ADX指標
    adx, plus_di, minus_di = calculate_adx(df["high"], df["low"], df["close"], 14)
    df["adx"] = adx
    df["plus_di"] = plus_di
    df["minus_di"] = minus_di

    # RSI指標
    df["rsi"] = calculate_rsi(df["close"], 14)

    # MACD指標
    macd_line, signal_line, histogram = calculate_macd(df["close"])
    df["macd"] = macd_line
    df["macd_signal"] = signal_line
    df["macd_histogram"] = histogram

    return df.dropna()


# --- 3. 交易邏輯實現 ---
class TradingStrategy:
    def __init__(self, custom_params=None):
        """
        初始化交易策略
        custom_params: 可選的自定義參數字典，會覆蓋預設參數
        """
        # 使用預設參數，並允許自定義覆蓋
        params = STRATEGY_PARAMS.copy()
        if custom_params:
            params.update(custom_params)

        # 資金管理設定
        self.default_qty_percent = DEFAULT_QTY_PERCENT

        # ccxt 交易所實例 - 統一帳戶合約交易
        self.exchange = ccxt.bybit(
            {
                "apiKey": BYBIT_API_KEY,
                "secret": BYBIT_API_SECRET,
                "sandbox": False,  # 實際交易模式
                "options": {
                    "defaultType": "linear",  # 統一帳戶線性合約
                    "adjustForTimeDifference": True,
                    "recvWindow": 120000,  # 增加接收窗口時間到2分鐘
                    "unified": True,  # 啟用統一帳戶模式
                },
                "enableRateLimit": True,  # 啟用速率限制，避免被交易所 ban IP
            }
        )

        # 同步時間
        try:
            self.exchange.load_time_difference()
        except:
            pass
        self.exchange.load_markets()
        self.symbol = SYMBOL

        # 提醒用戶確認槓桿設置
        print("⚠️ 重要提醒: 請確認在Bybit平台手動設置ETH/USDT槓桿為1倍")
        print("   1. 登入Bybit網站 -> 合約交易")
        print("   2. 選擇ETH/USDT交易對")
        print("   3. 將槓桿設置為1x")
        print("   4. 確認設置後再開始交易")

        # 嘗試從檔案加載狀態
        if not self.load_state():
            print("未找到或無法加載狀態檔案，初始化策略狀態...")
            self.current_capital = self._get_free_balance()  # 從 Bybit 獲取當前可用資金
            self.position_size = (
                self._get_current_position_size()
            )  # 從 Bybit 獲取當前持倉

            self.entry_price = 0  # 添加缺失的屬性
            self.long_entry_price = None
            self.long_peak = None
            self.long_trail_stop_price = None
            self.is_long_trail_active = False

            self.short_entry_price = None
            self.short_trough = None
            self.short_trail_stop_price = None
            self.is_short_trail_active = False

            self.peak_capital = self.current_capital
            self.max_drawdown = 0.0
        else:
            print("策略狀態已從檔案加載。")
            # 🔧 重要修正：加載後必須重新同步實際持倉和進場價格
            self.current_capital = self._get_free_balance()
            actual_position = self._get_current_position_size()

            # � 重要修正：加載後必須重新同步實際持倉和進場價格
            if actual_position != 0:
                actual_avg_price = self._get_position_avg_price()

                if actual_avg_price and actual_avg_price > 0:
                    old_price = self.long_entry_price if actual_position > 0 else self.short_entry_price

                    # 只有在價格不同時才顯示更新訊息
                    if abs(actual_avg_price - (old_price or 0)) > 0.01:
                        print(f"🔧 進場價格同步: ${old_price} → ${actual_avg_price:.2f}")

                    self.entry_price = actual_avg_price
                    self.position_size = actual_position
                    if actual_position > 0:
                        self.long_entry_price = actual_avg_price
                        # 重置空單相關狀態
                        self.short_entry_price = None
                        self.short_trough = None
                        self.short_trail_stop_price = None
                        self.is_short_trail_active = False
                    else:
                        self.short_entry_price = actual_avg_price
                        # 重置多單相關狀態
                        self.long_entry_price = None
                        self.long_peak = None
                        self.long_trail_stop_price = None
                        self.is_long_trail_active = False

                    self.save_state()  # 保存更正後的狀態
            else:
                # 無持倉時重置所有進場相關狀態
                self.position_size = 0
                self.entry_price = 0
                self.long_entry_price = None
                self.short_entry_price = None
                self.long_peak = None
                self.long_trail_stop_price = None
                self.is_long_trail_active = False
                self.short_trough = None
                self.short_trail_stop_price = None
                self.is_short_trail_active = False
                self.save_state()

            print(
                f"📊 帳戶狀態：未使用資金: {self.current_capital:.2f} USDT, 持倉量: {self.position_size:.3f} {SYMBOL.split('/')[0]}"
            )

        # 策略參數設定
        self.adx_threshold = params["adx_threshold"]
        self.long_fixed_stop_loss_percent = params["long_fixed_stop_loss_percent"]
        self.long_trailing_activate_profit_percent = params[
            "long_trailing_activate_profit_percent"
        ]
        self.long_trailing_pullback_percent = params["long_trailing_pullback_percent"]
        self.long_trailing_min_profit_percent = params[
            "long_trailing_min_profit_percent"
        ]
        self.short_fixed_stop_loss_percent = params["short_fixed_stop_loss_percent"]
        self.short_trailing_activate_profit_percent = params[
            "short_trailing_activate_profit_percent"
        ]
        self.short_trailing_pullback_percent = params["short_trailing_pullback_percent"]
        self.short_trailing_min_profit_percent = params[
            "short_trailing_min_profit_percent"
        ]

        self.trade_log = []  # 實時交易日誌記錄

        print(
            f"✅ 策略初始化完成 | 未使用資金: {self.current_capital:.2f} USDT | 持倉: {self.position_size:.3f} {SYMBOL.split('/')[0]}"
        )

    def _get_free_balance(self, currency="USDT"):
        """獲取 Bybit 帳戶的可用資金"""
        try:
            balance = self.exchange.fetch_balance()
            return balance["free"][currency]
        except Exception as e:
            print(f"獲取帳戶餘額失敗: {e}")
            return 0

    def _get_current_position_size(self):
        """獲取 Bybit 統一帳戶當前指定交易對的持倉量，並同步進場價格"""
        try:
            # 使用原始API直接獲取持倉（這個方法有效）
            if hasattr(self.exchange, "private_get_v5_position_list"):
                params = {"category": "linear", "symbol": "ETHUSDT"}
                response = self.exchange.private_get_v5_position_list(params)

                if "result" in response and "list" in response["result"]:
                    positions = response["result"]["list"]
                    for pos in positions:
                        size = float(pos.get("size", 0))
                        if size > 0:
                            side = pos.get("side", "")
                            avg_price = (
                                float(pos.get("avgPrice", 0))
                                if pos.get("avgPrice") != "N/A"
                                else 0
                            )
                            mark_price = pos.get("markPrice", "N/A")
                            unrealized_pnl = pos.get("unrealisedPnl", "N/A")

                            # 持倉檢測（簡化日誌）
                            side_text = "多單" if side == "Buy" else "空單"

                            # 🔧 修正：同步進場價格到策略狀態
                            if side == "Buy" and avg_price > 0:
                                # 如果檢測到多單但策略狀態中沒有進場價格，則同步
                                if (
                                    self.long_entry_price is None
                                    or self.long_entry_price == 0
                                ):
                                    self.long_entry_price = avg_price
                                    self.entry_price = avg_price


                                    # 🔧 修正移動停損初始化：嘗試恢復合理的移動停損狀態
                                    current_price = (
                                        float(mark_price)
                                        if mark_price != "N/A"
                                        else avg_price
                                    )
                                    profit_percent = (
                                        current_price - avg_price
                                    ) / avg_price

                                    # 如果當前已有利潤且超過激活閾值，應該激活移動停損
                                    if (
                                        profit_percent
                                        > self.long_trailing_activate_profit_percent
                                    ):
                                        self.long_peak = (
                                            current_price  # 設定當前價格為峰值
                                        )
                                        self.long_trail_stop_price = avg_price * (
                                            1 + self.long_trailing_min_profit_percent
                                        )
                                        self.is_long_trail_active = True
                                        print(
                                            f"🔧 恢復移動停損狀態: 峰值${self.long_peak:.2f}, 止損價${self.long_trail_stop_price:.2f}"
                                        )
                                    else:
                                        # 如果沒有足夠利潤，重置移動停損狀態
                                        self.long_peak = None
                                        self.long_trail_stop_price = None
                                        self.is_long_trail_active = False


                                    self.save_state()
                                return size
                            elif side == "Sell" and avg_price > 0:
                                # 如果檢測到空單但策略狀態中沒有進場價格，則同步
                                if (
                                    self.short_entry_price is None
                                    or self.short_entry_price == 0
                                ):
                                    self.short_entry_price = avg_price
                                    self.entry_price = avg_price


                                    # 🔧 修正移動停損初始化：嘗試恢復合理的移動停損狀態
                                    current_price = (
                                        float(mark_price)
                                        if mark_price != "N/A"
                                        else avg_price
                                    )
                                    profit_percent = (
                                        avg_price - current_price
                                    ) / avg_price

                                    # 如果當前已有利潤且超過激活閾值，應該激活移動停損
                                    if (
                                        profit_percent
                                        > self.short_trailing_activate_profit_percent
                                    ):
                                        self.short_trough = (
                                            current_price  # 設定當前價格為谷值
                                        )
                                        self.short_trail_stop_price = avg_price * (
                                            1 - self.short_trailing_min_profit_percent
                                        )
                                        self.is_short_trail_active = True
                                        print(
                                            f"🔧 恢復移動停損狀態: 谷值${self.short_trough:.2f}, 止損價${self.short_trail_stop_price:.2f}"
                                        )
                                    else:
                                        # 如果沒有足夠利潤，重置移動停損狀態
                                        self.short_trough = None
                                        self.short_trail_stop_price = None
                                        self.is_short_trail_active = False


                                    self.save_state()
                                return -size

            print("📊 無持倉")
            return 0

        except Exception as e:
            print(f"獲取持倉失敗: {e}")
            return 0

    def _get_position_avg_price(self):
        """獲取當前持倉的平均進場價格"""
        try:
            # 使用原始API直接獲取持倉
            if hasattr(self.exchange, "private_get_v5_position_list"):
                params = {"category": "linear", "symbol": "ETHUSDT"}
                response = self.exchange.private_get_v5_position_list(params)

                if "result" in response and "list" in response["result"]:
                    positions = response["result"]["list"]

                    for pos in positions:
                        size = float(pos.get("size", 0))
                        avg_price = pos.get("avgPrice", "N/A")

                        if size > 0:
                            if avg_price != "N/A" and avg_price != "" and avg_price != "0":
                                try:
                                    return float(avg_price)
                                except:
                                    pass
            return None
        except Exception as e:
            print(f"❌ 獲取持倉平均價格失敗: {e}")
            return None

    def _place_order(self, side, trade_qty, price_type="market"):
        """下單到 Bybit 統一帳戶"""
        try:
            # 確保數量是非零的
            if trade_qty <= 0:
                print(f"嘗試下單數量為 {trade_qty}，訂單取消。")
                return None

            # 嘗試不同的下單參數組合
            print(f"🔄 嘗試下單: {side} {trade_qty} {self.symbol}")

            # 方法1: 強制使用線性合約參數
            try:
                params = {"category": "linear"}  # 強制使用合約交易
                order = self.exchange.create_order(
                    symbol=self.symbol,
                    type=price_type,
                    side=side,
                    amount=trade_qty,
                    price=None,
                    params=params,
                )
            except Exception as e1:
                print(f"方法1失敗: {e1}")

                # 方法2: 使用原始API確保合約交易
                try:
                    if hasattr(self.exchange, "private_post_v5_order_create"):
                        order_params = {
                            "category": "linear",  # 強制線性合約
                            "symbol": "ETHUSDT",
                            "side": side.capitalize(),
                            "orderType": "Market",
                            "qty": str(trade_qty),
                        }
                        response = self.exchange.private_post_v5_order_create(
                            order_params
                        )
                        if response.get("retCode") == 0:
                            # 🔧 修正：嘗試獲取成交價格
                            filled_price = None
                            if "result" in response and "avgPrice" in response["result"]:
                                try:
                                    filled_price = float(response["result"]["avgPrice"])
                                except:
                                    pass

                            order = {
                                "id": response["result"]["orderId"],
                                "side": side,
                                "amount": trade_qty,
                                "filled": trade_qty,  # 假設市價單完全成交
                                "price": filled_price,  # 可能為 None
                                "symbol": self.symbol,
                                "type": price_type,
                                "status": "closed",
                            }
                        else:
                            raise Exception(f"API錯誤: {response}")
                    else:
                        raise Exception("無可用的下單方法")
                except Exception as e2:
                    print(f"方法2失敗: {e2}")
                    raise e2
            print(
                f"下單成功: {order['side']} {order['amount']} {order['symbol']} @ {order.get('price', 'N/A')} (類型: {order['type']})"
            )
            self.trade_log.append(
                {
                    "time": datetime.now().isoformat(),
                    "type": f"ORDER_{side.upper()}",  # isoformat 讓 datetime 可 JSON 序列化
                    "price": order.get("price", "N/A"),
                    "qty": trade_qty,
                    "status": "PLACED",
                    "order_id": order["id"],
                }
            )
            return order
        except ccxt.InsufficientFunds as e:
            print(f"資金不足，無法下單 {side} {trade_qty} {self.symbol}")
            print(f"詳細錯誤: {e}")
            self.trade_log.append(
                {
                    "time": datetime.now().isoformat(),
                    "type": "ERROR_INSUFFICIENT_FUNDS",
                    "details": str(e),
                }
            )
        except ccxt.InvalidOrder as e:
            print(f"無效訂單: {e}")
            self.trade_log.append(
                {
                    "time": datetime.now().isoformat(),
                    "type": "ERROR_INVALID_ORDER",
                    "details": str(e),
                }
            )
        except Exception as e:
            print(f"下單失敗: {e}")
            self.trade_log.append(
                {
                    "time": datetime.now().isoformat(),
                    "type": "ERROR_ORDER_FAILED",
                    "details": str(e),
                }
            )
        except Exception as e:
            print(f"下單失敗: {e}")
            self.trade_log.append(
                {
                    "time": datetime.now().isoformat(),
                    "type": "ERROR_ORDER_FAILED",
                    "details": str(e),
                }
            )
        return None

    def _close_position(self, current_close):
        """平倉當前持有的所有倉位"""
        print(f"\n🔄 開始平倉程序...")

        # 添加重試機制確保狀態同步
        max_retries = 3
        actual_position = 0

        for attempt in range(max_retries):
            print(f"📊 嘗試 {attempt+1}/{max_retries}: 查詢當前持倉...")
            actual_position = self._get_current_position_size()

            if actual_position == 0:
                if attempt < max_retries - 1:
                    print(f"⏳ 查詢顯示無持倉，等待2秒後重試...")
                    time.sleep(2)
                    continue
                else:
                    print("📊 多次查詢確認無實際持倉")
                    # 重置內部狀態
                    print("🔧 重置所有內部交易狀態...")
                    self.position_size = 0
                    self.entry_price = 0
                    self.long_entry_price = None
                    self.long_peak = None
                    self.long_trail_stop_price = None
                    self.is_long_trail_active = False
                    self.short_entry_price = None
                    self.short_trough = None
                    self.short_trail_stop_price = None
                    self.is_short_trail_active = False
                    self.save_state()
                    return False
            else:
                print(f"✅ 確認持倉: {actual_position:.5f} ETH")
                break

        if actual_position == 0:
            print("❌ 多次查詢後仍顯示無持倉，可能存在同步問題")
            return False

        # 使用實際持倉數量進行平倉
        abs_pos_size = abs(actual_position)
        order = None

        print(f"🔄 準備平倉: 實際持倉 {actual_position:.5f} ETH")

        if actual_position > 0:  # 平多單
            print(
                f"📉 平多單: {actual_position:.5f} {self.symbol} @ ${current_close:.2f}"
            )
            order = self._place_order("sell", abs_pos_size, "market")
        elif actual_position < 0:  # 平空單
            print(f"📈 平空單: {abs_pos_size:.5f} {self.symbol} @ ${current_close:.2f}")
            order = self._place_order("buy", abs_pos_size, "market")

        if order:
            print(f"✅ 平倉訂單已提交: {order.get('id', 'N/A')}")

            # 等待並確認平倉結果
            print(f"⏳ 等待3秒後確認平倉結果...")
            time.sleep(3)

            # 確認平倉是否成功
            confirmation_retries = 3
            final_position = None

            for confirm_attempt in range(confirmation_retries):
                print(
                    f"🔍 確認嘗試 {confirm_attempt+1}/{confirmation_retries}: 查詢平倉後持倉..."
                )
                final_position = self._get_current_position_size()

                if final_position == 0:
                    print(f"✅ 平倉成功確認：持倉已清零")
                    break
                else:
                    print(f"⚠️ 平倉可能未完成，剩餘持倉: {final_position:.5f}")
                    if confirm_attempt < confirmation_retries - 1:
                        time.sleep(2)

            if final_position != 0:
                print(f"❌ 平倉確認失敗，剩餘持倉: {final_position:.5f}")
                self.trade_log.append(
                    {
                        "time": datetime.now().isoformat(),
                        "type": "ERROR_CLOSE_FAILED",
                        "remaining_position": final_position,
                        "order_id": order.get("id", "N/A"),
                    }
                )
                return False

            # 計算盈虧
            entry_price_for_calc = (
                self.long_entry_price if actual_position > 0 else self.short_entry_price
            )
            if entry_price_for_calc is not None and entry_price_for_calc > 0:
                if actual_position > 0:
                    profit_loss = (
                        current_close - entry_price_for_calc
                    ) * actual_position
                else:
                    profit_loss = (entry_price_for_calc - current_close) * abs(
                        actual_position
                    )
                print(f"💰 平倉盈虧: ${profit_loss:.2f} USDT")
            else:
                profit_loss = 0
                print("⚠️ 無進場價格記錄，無法計算精確盈虧")

            # 更新狀態
            self.current_capital = self._get_free_balance()  # 平倉後再次更新資金
            self.position_size = 0
            self.entry_price = 0
            self.long_entry_price = None
            self.long_peak = None
            self.long_trail_stop_price = None
            self.is_long_trail_active = False
            self.short_entry_price = None
            self.short_trough = None
            self.short_trail_stop_price = None
            self.is_short_trail_active = False

            self.trade_log.append(
                {
                    "time": datetime.now().isoformat(),
                    "type": "EXIT_REAL",
                    "price": current_close,
                    "profit_loss": profit_loss,
                    "current_position_size": self.position_size,
                    "current_capital": self.current_capital,
                    "order_id": order.get("id", "N/A"),
                }
            )
            self.save_state()  # 平倉後保存狀態
            print(f"✅ 平倉完成，狀態已重置")
            return True
        else:
            print(f"❌ 平倉失敗，訂單未成功")
            return False

    # --- 新增：保存策略狀態到 JSON 檔案 ---
    def save_state(self):
        state = {
            "position_size": self.position_size,
            "entry_price": self.entry_price,
            "long_entry_price": self.long_entry_price,
            "long_peak": self.long_peak,
            "long_trail_stop_price": self.long_trail_stop_price,
            "is_long_trail_active": self.is_long_trail_active,
            "short_entry_price": self.short_entry_price,
            "short_trough": self.short_trough,
            "short_trail_stop_price": self.short_trail_stop_price,
            "is_short_trail_active": self.is_short_trail_active,
            "peak_capital": self.peak_capital,
            "max_drawdown": self.max_drawdown,
            "current_capital": self.current_capital,  # 備份，雖然會實時查詢
            # trade_log 不建議保存所有歷史，只保存關鍵交易狀態
        }
        try:
            with open(STATE_FILE, "w") as f:
                json.dump(state, f, indent=4)
            print(f"策略狀態已保存到 {STATE_FILE}")
            return True
        except Exception as e:
            print(f"保存策略狀態失敗: {e}")
            return False

    # --- 新增：從 JSON 檔案加載策略狀態 ---
    def load_state(self):
        if not os.path.exists(STATE_FILE):
            return False
        try:
            with open(STATE_FILE, "r") as f:
                state = json.load(f)

            self.position_size = state.get("position_size", 0)
            self.entry_price = state.get("entry_price", 0)
            self.long_entry_price = state.get("long_entry_price")
            self.long_peak = state.get("long_peak")
            self.long_trail_stop_price = state.get("long_trail_stop_price")
            self.is_long_trail_active = state.get("is_long_trail_active", False)
            self.short_entry_price = state.get("short_entry_price")
            self.short_trough = state.get("short_trough")
            self.short_trail_stop_price = state.get("short_trail_stop_price")
            self.is_short_trail_active = state.get("is_short_trail_active", False)
            self.peak_capital = state.get(
                "peak_capital", self._get_free_balance()
            )  # 如果檔案中沒有，則初始化
            self.max_drawdown = state.get("max_drawdown", 0.0)
            self.current_capital = state.get(
                "current_capital", self._get_free_balance()
            )

            return True
        except Exception as e:
            print(f"加載策略狀態失敗: {e}")
            return False


        # --- 新增：與交易所同步校正 JSON 狀態（可定期呼叫） ---
    def sync_state_with_exchange(self, reason="scheduled hourly check"):
            """從交易所讀取實際持倉，並校正本地 JSON 狀態。
            - 會同步：持倉方向/數量、進場價位、資金餘額
            - 若已無持倉，會清空本地進場相關欄位
            - 僅在偵測到變更時才保存與打印，以減少噪音
            """
            try:
                changed = False

                # 同步資金
                try:
                    self.current_capital = self._get_free_balance()
                except Exception:
                    pass

                # 同步持倉與進場價
                actual_position = self._get_current_position_size()
                if actual_position == 0:
                    # 若實際無持倉，但本地仍有記錄，則重置
                    if (
                        self.position_size != 0
                        or self.long_entry_price is not None
                        or self.short_entry_price is not None
                    ):
                        self.position_size = 0
                        self.entry_price = 0
                        # 清空多單狀態
                        self.long_entry_price = None
                        self.long_peak = None
                        self.long_trail_stop_price = None
                        self.is_long_trail_active = False
                        # 清空空單狀態
                        self.short_entry_price = None
                        self.short_trough = None
                        self.short_trail_stop_price = None
                        self.is_short_trail_active = False
                        changed = True
                elif actual_position > 0:
                    # 多單持倉
                    avg = self._get_position_avg_price() or 0
                    if (
                        self.position_size != actual_position
                        or not self.long_entry_price
                        or abs((self.long_entry_price or 0) - avg) > 1e-9
                        or self.short_entry_price is not None
                    ):
                        self.position_size = actual_position
                        self.entry_price = avg
                        self.long_entry_price = avg
                        # 清空空單狀態避免殘留
                        self.short_entry_price = None
                        self.short_trough = None
                        self.short_trail_stop_price = None
                        self.is_short_trail_active = False
                        changed = True
                else:
                    # 空單持倉
                    avg = self._get_position_avg_price() or 0
                    if (
                        self.position_size != actual_position
                        or not self.short_entry_price
                        or abs((self.short_entry_price or 0) - avg) > 1e-9
                        or self.long_entry_price is not None
                    ):
                        self.position_size = actual_position
                        self.entry_price = avg
                        self.short_entry_price = avg
                        # 清空多單狀態避免殘留
                        self.long_entry_price = None
                        self.long_peak = None
                        self.long_trail_stop_price = None
                        self.is_long_trail_active = False
                        changed = True

                if changed:
                    self.save_state()
                    try:
                        # 僅在變更時輸出一行簡訊息，避免干擾
                        side = "LONG" if self.position_size > 0 else ("SHORT" if self.position_size < 0 else "FLAT")
                        entry = self.long_entry_price if self.position_size > 0 else (self.short_entry_price if self.position_size < 0 else 0)
                        print(f"🛠️ 已校正JSON狀態（{reason}）| 狀態: {side}, 持倉: {self.position_size:.5f}, 進場價: {entry}")
                    except Exception:
                        pass
                return True
            except Exception as e:
                print(f"⚠️ 校正JSON狀態失敗: {e}")
                return False

    def process_bar(self, current_bar):
        current_time = current_bar.name
        current_close = current_bar["close"]
        current_high = current_bar["high"]
        current_low = current_bar["low"]
        current_adx = current_bar["adx"]

        # 檢查關鍵數據是否為None
        if current_close is None or current_high is None or current_low is None:
            print(
                f"❌ 價格數據不完整: close={current_close}, high={current_high}, low={current_low}"
            )
            return

        if current_adx is None:
            print(f"❌ ADX數據不完整: {current_adx}")
            return

        if (
            pd.isna(current_bar["ema90"])
            or pd.isna(current_bar["ema200"])
            or pd.isna(current_adx)
        ):
            print(f"數據不足以計算指標在 {current_time}，跳過。")
            return

        # === 📊 關鍵指標報告 ===
        # 🔧 修正時區顯示：將UTC時間轉換為台北時間 (UTC+12)
        # 根據實際測試，需要加12小時才能得到正確的台北時間
        local_time = current_time + timedelta(hours=12)

        print(
            f"\n\n� 新K線: {local_time.strftime('%Y-%m-%d %H:%M:%S')} | 價格: ${current_close:.2f}"
        )

        # 技術指標（一行顯示）
        print(
            f"📈 技術指標 | EMA90: ${current_bar['ema90']:.2f} | EMA200: ${current_bar['ema200']:.2f} | ADX: {current_adx:.2f} | RSI: {current_bar['rsi']:.2f}"
        )

        # 進場條件檢查（簡化顯示）
        # 多單條件
        long_condition_price = current_close > current_bar["ema90"]
        long_condition_low = current_low > current_bar["ema90"]
        long_condition_trend = current_close > current_bar["ema200"]
        long_condition_strength = current_adx > self.adx_threshold
        long_condition_rsi = current_bar["rsi"] <= 70  # 🔧 新增：RSI高於70不進場
        long_entry_ready = (
            long_condition_price
            and long_condition_low
            and long_condition_trend
            and long_condition_strength
            and long_condition_rsi
        )

        # 空單條件
        short_condition_price = current_close < current_bar["ema90"]
        short_condition_high = current_high < current_bar["ema90"]
        short_condition_trend = current_close < current_bar["ema200"]
        short_condition_strength = current_adx > self.adx_threshold
        short_condition_rsi = current_bar["rsi"] >= 30  # 🔧 新增：RSI低於30不進場
        short_entry_ready = (
            short_condition_price
            and short_condition_high
            and short_condition_trend
            and short_condition_strength
            and short_condition_rsi
        )

        print(
            f"🎯 進場信號 | 多單: {'✅' if long_entry_ready else '❌'} | 空單: {'✅' if short_entry_ready else '❌'}"
        )

        strong_trend = current_adx > self.adx_threshold

        # --- 🔧 修正：先更新當前資金和持倉狀態，再顯示持倉資訊 ---
        self.current_capital = self._get_free_balance()
        self.position_size = self._get_current_position_size()

        # 簡化持倉和盈虧分析（使用更新後的持倉資訊）
        if self.position_size != 0:
            if self.position_size > 0:  # 多單
                entry_price = self.long_entry_price
                current_profit_usd = (
                    (current_close - entry_price) * self.position_size
                    if entry_price
                    else 0
                )
                current_profit_percent = (
                    ((current_close - entry_price) / entry_price * 100)
                    if entry_price
                    else 0
                )

                print(
                    f"\n📋 當前持倉: 多單 {self.position_size} ETH | 進場: ${entry_price:.2f} | 盈虧: {current_profit_percent:+.2f}%"
                )

                # 停損設置（簡化）
                if entry_price:
                    fixed_stop = entry_price * (1 - self.long_fixed_stop_loss_percent)
                    trail_stop = self.long_trail_stop_price
                    trail_status = "已激活" if self.is_long_trail_active else "未激活"

                    print(
                        f"🛡️ 停損設置: 固定 ${fixed_stop:.2f} | 移動停損: {trail_status}"
                    )

            else:  # 空單
                entry_price = self.short_entry_price
                abs_position = abs(self.position_size)
                current_profit_usd = (
                    (entry_price - current_close) * abs_position if entry_price else 0
                )
                current_profit_percent = (
                    ((entry_price - current_close) / entry_price * 100)
                    if entry_price
                    else 0
                )

                print(
                    f"\n📋 當前持倉: 空單 {abs_position} ETH | 進場: ${entry_price:.2f} | 盈虧: {current_profit_percent:+.2f}%"
                )

                # 停損設置（簡化）
                if entry_price:
                    fixed_stop = entry_price * (1 + self.short_fixed_stop_loss_percent)
                    trail_stop = self.short_trail_stop_price
                    trail_status = "已激活" if self.is_short_trail_active else "未激活"

                    print(
                        f"🛡️ 停損設置: 固定 ${fixed_stop:.2f} | 移動停損: {trail_status}"
                    )
        else:
            print(f"\n📋 當前持倉: 無持倉")

        # 帳戶狀態（簡化）
        print(
            f"💰 帳戶狀態: 未使用資金 {self.current_capital:.2f} USDT"
        )

        market = self.exchange.market(self.symbol)
        amount_precision = market["precision"]["amount"]
        min_amount = (
            market["limits"]["amount"]["min"] if "amount" in market["limits"] else 0.001
        )

        # 統一帳戶合約交易：資金和交易量計算（簡化日誌）
        # 使用完整的設定資金比例
        trade_qty_usd = self.current_capital * self.default_qty_percent / 100
        trade_qty_unrounded = trade_qty_usd / current_close

        # ETH只能下單到小數點後兩位，使用無條件捨去
        import math

        trade_qty = (math.floor(trade_qty_unrounded * 100) / 100) * LEVER

        # 確保trade_qty是數字類型
        try:
            trade_qty = float(trade_qty)
        except (ValueError, TypeError):
            print(f"❌ 交易數量轉換失敗: {trade_qty}，設為0")
            trade_qty = 0

        if trade_qty < min_amount:
            print(
                f"❌ 計算出的交易數量 {trade_qty:.6f} 小於最小交易量 {min_amount:.6f}，跳過交易。"
            )
            trade_qty = 0
        # 交易數量檢查通過（簡化日誌）

        # --- 處理多單邏輯 ---
        # 這裡需要重複 long_entry_condition 的判斷，因為它是根據當前K線數據來判斷的。
        long_entry_condition = (
            current_close > current_bar["ema90"]
            and current_low > current_bar["ema90"]
            and current_close > current_bar["ema200"]
            and strong_trend
            and current_bar["rsi"] <= 70  # 🔧 修正：新增 RSI 進場限制
        )
        if self.position_size == 0:
            if long_entry_condition and float(trade_qty) > 0:
                print(f"{current_time} - 觸發多單進場條件。")
                order = self._place_order("buy", trade_qty, "market")
                if order and order["status"] == "closed":
                    # 🔧 修正：下單後等待並查詢實際持倉來獲取真實進場價
                    time.sleep(2)

                    # 重新查詢持倉以獲取實際數量和平均價格
                    actual_position = self._get_current_position_size()
                    if actual_position > 0:
                        self.position_size = actual_position
                        # 從持倉資訊中獲取實際進場價格
                        actual_entry_price = self._get_position_avg_price()
                        if actual_entry_price and actual_entry_price > 0:
                            self.entry_price = actual_entry_price
                            self.long_entry_price = self.entry_price
                        else:
                            # 如果無法獲取實際價格，使用當前收盤價
                            self.entry_price = current_close
                            self.long_entry_price = self.entry_price
                    else:
                        # 如果查詢不到持倉，使用訂單資訊
                        self.position_size = order.get("filled", trade_qty)
                        self.entry_price = order.get("price", current_close)
                        self.long_entry_price = self.entry_price

                    self.long_peak = current_high
                    self.long_trail_stop_price = None
                    self.is_long_trail_active = False
                    print(
                        f"多單已進場，數量: {self.position_size:.3f} @ {self.entry_price:.2f}"
                    )
                    self.save_state()

        elif self.position_size > 0:
            # 🔧 修正：確保有進場價格才能執行停損邏輯
            if self.long_entry_price is None or self.long_entry_price <= 0:
                print(f"⚠️ 警告：檢測到多單持倉但無進場價格記錄，無法執行停損！")
                print(f"   建議手動檢查持倉或重啟程式以重新同步狀態")
                return

            # 確保long_peak不為None (移動停損需要)
            if self.long_peak is None:
                self.long_peak = current_high

            else:
                self.long_peak = max(self.long_peak, current_high)

            # 計算當前盈虧百分比
            current_profit_percent = (
                current_close - self.long_entry_price
            ) / self.long_entry_price

            # 計算固定停損價格
            long_fixed_stop_loss_price = self.long_entry_price * (
                1 - self.long_fixed_stop_loss_percent
            )

            # 固定停損觸發條件 - 使用收盤價判斷
            long_fixed_stop_loss_triggered = current_close <= long_fixed_stop_loss_price

            # 添加詳細的固定停損檢查日誌
            print(f"\n📊 多單固定停損檢查:")
            print(f"   固定停損價格: {long_fixed_stop_loss_price:.2f}")
            print(f"   當前收盤價: {current_close:.2f}")

            # 顯示移動停損詳細信息
            if self.is_long_trail_active:
                peak_str = f"${self.long_peak:.2f}" if self.long_peak else "N/A"
                trail_price_str = (
                    f"${self.long_trail_stop_price:.2f}"
                    if self.long_trail_stop_price
                    else "N/A"
                )
                print(f"   追蹤峰值: {peak_str}，保護停損價格: {trail_price_str}")
            else:
                print(f"   移動停損狀態: 未激活")

            if long_fixed_stop_loss_triggered:
                print(
                    f"🚨 固定止損觸發: 當前收盤價${current_close:.2f} <= 固定止損價${long_fixed_stop_loss_price:.2f}"
                )

                print(f"\n🚨 === 多單固定停損平倉觸發 ===")
                print(f"時間: {current_time}")
                print(f"觸發原因: FIXED_STOP")
                print(f"當前價格: ${current_close:.2f}")
                print(f"持倉量: {self.position_size}")
                print(f"進場價: ${self.long_entry_price:.2f}")

                close_success = self._close_position(current_close)
                if not close_success:
                    print(f"❌ 多單固定停損平倉失敗，請檢查")
                else:
                    print(f"✅ 多單固定停損平倉完成")

        # --- 處理空單邏輯 ---
        # 這裡需要重複 short_entry_condition 的判斷，因為它是根據當前K線數據來判斷的。
        short_entry_condition = (
            current_close < current_bar["ema90"]
            and current_high < current_bar["ema90"]
            and current_close < current_bar["ema200"]
            and strong_trend
            and current_bar["rsi"] >= 30  # 🔧 修正：新增 RSI 進場限制
        )
        if self.position_size == 0:
            if short_entry_condition and float(trade_qty) > 0:
                print(f"{current_time} - 觸發空單進場條件。")
                order = self._place_order("sell", trade_qty, "market")
                if order and order["status"] == "closed":
                    # 🔧 修正：下單後等待並查詢實際持倉來獲取真實進場價
                    print("⏳ 等待2秒後查詢實際持倉資訊...")
                    time.sleep(2)

                    # 重新查詢持倉以獲取實際數量和平均價格
                    actual_position = self._get_current_position_size()
                    if actual_position < 0:
                        self.position_size = actual_position
                        # 從持倉資訊中獲取實際進場價格
                        actual_entry_price = self._get_position_avg_price()
                        if actual_entry_price and actual_entry_price > 0:
                            self.entry_price = actual_entry_price
                            self.short_entry_price = self.entry_price
                            print(f"✅ 獲取實際進場價格: ${self.entry_price:.2f}")
                        else:
                            # 如果無法獲取實際價格，使用當前收盤價
                            self.entry_price = current_close
                            self.short_entry_price = self.entry_price
                            print(f"⚠️ 無法獲取實際進場價格，使用當前收盤價: ${current_close:.2f}")
                    else:
                        # 如果查詢不到持倉，使用訂單資訊
                        self.position_size = -order.get("filled", trade_qty)
                        self.entry_price = order.get("price", current_close)
                        self.short_entry_price = self.entry_price
                        print(f"⚠️ 查詢持倉失敗，使用訂單資訊: ${self.entry_price:.2f}")

                    self.short_trough = current_low
                    self.short_trail_stop_price = None
                    self.is_short_trail_active = False
                    print(
                        f"空單已進場，數量: {abs(self.position_size):.3f} @ {self.entry_price:.2f}"
                    )
                    self.save_state()

        elif self.position_size < 0:
            # 🔧 修正：確保有進場價格才能執行停損邏輯
            if self.short_entry_price is None or self.short_entry_price <= 0:
                print(f"⚠️ 警告：檢測到空單持倉但無進場價格記錄，無法執行停損！")
                print(f"   建議手動檢查持倉或重啟程式以重新同步狀態")
                return

            # 確保short_trough不為None (移動停損需要)
            if self.short_trough is None:
                self.short_trough = current_low

            else:
                self.short_trough = min(self.short_trough, current_low)

            # 計算當前盈虧百分比用於調試
            current_profit_percent = (
                self.short_entry_price - current_close
            ) / self.short_entry_price
            print(
                f"\n📊 空單狀態: 進場價${self.short_entry_price:.2f}, 當前價${current_close:.2f}, 盈虧{current_profit_percent*100:.2f}%"
            )

            # 計算固定停損價格
            short_fixed_stop_loss_price = self.short_entry_price * (
                1 + self.short_fixed_stop_loss_percent
            )

            # 固定停損觸發條件 - 使用收盤價判斷
            short_fixed_stop_loss_triggered = (
                current_close >= short_fixed_stop_loss_price
            )

            # 添加詳細的固定停損檢查日誌
            print(f"📊 空單固定停損檢查:")
            print(f"   固定停損價格: {short_fixed_stop_loss_price:.2f}")
            print(f"   當前收盤價: {current_close:.2f}")

            # 顯示移動停損詳細信息
            if self.is_short_trail_active:
                trough_str = f"${self.short_trough:.2f}" if self.short_trough else "N/A"
                trail_price_str = (
                    f"${self.short_trail_stop_price:.2f}"
                    if self.short_trail_stop_price
                    else "N/A"
                )
                print(f"   追蹤谷值: {trough_str}，保護停損價格: {trail_price_str}")
            else:
                print(f"   移動停損狀態: 未激活")

            if short_fixed_stop_loss_triggered:
                print(
                    f"🚨 固定止損觸發: 當前收盤價${current_close:.2f} >= 固定止損價${short_fixed_stop_loss_price:.2f}"
                )

                print(f"\n🚨 === 空單固定停損平倉觸發 ===")
                print(f"時間: {current_time}")
                print(f"觸發原因: FIXED_STOP")
                print(f"當前價格: ${current_close:.2f}")
                print(f"持倉量: {self.position_size}")
                print(f"進場價: ${self.short_entry_price:.2f}")

                close_success = self._close_position(current_close)
                if not close_success:
                    print(f"❌ 空單固定停損平倉失敗，請檢查")
                else:
                    print(f"✅ 空單固定停損平倉完成")

        # --- 更新資金和回撤計算 ---
        try:
            balance_data = self.exchange.fetch_balance()
            total_equity = balance_data["total"]["USDT"]

            self.current_capital = total_equity

            self.peak_capital = max(self.peak_capital, self.current_capital)

            if self.peak_capital > 0:
                current_drawdown = (
                    self.peak_capital - self.current_capital
                ) / self.peak_capital
                self.max_drawdown = max(self.max_drawdown, current_drawdown)
        except Exception as e:
            print(f"更新實時資金和回撤失敗: {e}")

        self.save_state()  # 每處理完一根K線都保存一次狀態，確保最新狀態被記錄

    def check_trailing_stop_only(self):
        """
        每分鐘檢查移動停損 - 只處理移動停損邏輯，不處理固定停損和進場邏輯
        靜默執行，只在重要事件時打印日誌
        """
        if self.position_size == 0:
            return  # 無持倉時不需要檢查

        try:
            # 獲取當前價格（使用1分鐘K線的最新數據）
            df_1m = fetch_bybit_klines(SYMBOL, "1m", limit=2)
            if df_1m.empty or len(df_1m) < 1:
                # 靜默跳過，不打印錯誤信息
                return

            current_bar_1m = df_1m.iloc[-1]  # 最新的1分鐘K線
            current_close = current_bar_1m["close"]
            current_high = current_bar_1m["high"]
            current_low = current_bar_1m["low"]
            current_time = current_bar_1m.name

            # 檢查關鍵數據是否為None
            if current_close is None or current_high is None or current_low is None:
                # 靜默跳過，不打印錯誤信息
                return

            # 靜默執行，不打印常規檢查信息

            # --- 處理多單移動停損 ---
            if self.position_size > 0:
                if self.long_entry_price is None or self.long_entry_price <= 0:
                    return  # 靜默跳過

                # 更新峰值
                if self.long_peak is None:
                    self.long_peak = current_high
                else:
                    old_peak = self.long_peak
                    self.long_peak = max(self.long_peak, current_high)
                    # 只在峰值有顯著更新時才打印（避免頻繁打印）
                    if (
                        self.long_peak > old_peak
                        and (self.long_peak - old_peak) / old_peak > 0.005
                    ):  # 0.5%以上的變化才打印
                        print(
                            f"\n\n📈 多單峰值更新: ${old_peak:.2f} → ${self.long_peak:.2f}"
                        )

                # 檢查是否需要激活移動停損
                if (
                    not self.is_long_trail_active
                    and current_close
                    > self.long_entry_price
                    * (1 + self.long_trailing_activate_profit_percent)
                ):
                    self.long_trail_stop_price = self.long_entry_price * (
                        1 + self.long_trailing_min_profit_percent
                    )
                    self.is_long_trail_active = True
                    print(
                        f"\n\n✅ 多單移動停損激活 | 初始止損價: ${self.long_trail_stop_price:.2f}"
                    )
                    self.save_state()

                # 更新移動停損價格
                if self.is_long_trail_active and self.long_peak is not None:
                    # 計算基於峰值回撤的停損價格
                    new_trail_stop = self.long_peak * (
                        1 - self.long_trailing_pullback_percent
                    )

                    # 🔧 重要修正：確保移動停損價格不低於最小獲利保護
                    min_profit_protection = self.long_entry_price * (
                        1 + self.long_trailing_min_profit_percent
                    )

                    # 移動停損價格取較高者（峰值回撤 vs 最小獲利保護）
                    new_trail_stop = max(new_trail_stop, min_profit_protection)

                    old_trail_stop = self.long_trail_stop_price
                    self.long_trail_stop_price = max(
                        (
                            self.long_trail_stop_price
                            if self.long_trail_stop_price is not None
                            else 0
                        ),
                        new_trail_stop,
                    )
                    # 只在停損價格有顯著更新時才打印
                    if (
                        old_trail_stop
                        and self.long_trail_stop_price > old_trail_stop
                        and (self.long_trail_stop_price - old_trail_stop)
                        / old_trail_stop
                        > 0.003
                    ):  # 0.3%以上的變化才打印
                        print(
                            f"\n\n📊 多單移動停損更新: ${old_trail_stop:.2f} → ${self.long_trail_stop_price:.2f}"
                        )
                        self.save_state()

                # 檢查移動停損觸發
                long_trail_stop_triggered = (
                    self.is_long_trail_active
                    and self.long_trail_stop_price is not None
                    and current_close <= self.long_trail_stop_price
                )

                if long_trail_stop_triggered:
                    print(f"\n\n🚨 === 多單移動停損觸發 ===")
                    print(f"時間: {current_time}")
                    print(f"當前價格: ${current_close:.2f}")
                    print(f"移動停損價: ${self.long_trail_stop_price:.2f}")
                    print(f"持倉量: {self.position_size}")

                    close_success = self._close_position(current_close)
                    if close_success:
                        print(f"✅ 多單移動停損平倉完成")
                    else:
                        print(f"❌ 多單移動停損平倉失敗")

            # --- 處理空單移動停損 ---
            elif self.position_size < 0:
                if self.short_entry_price is None or self.short_entry_price <= 0:
                    return  # 靜默跳過

                # 更新谷值
                if self.short_trough is None:
                    self.short_trough = current_low
                else:
                    old_trough = self.short_trough
                    self.short_trough = min(self.short_trough, current_low)
                    # 只在谷值有顯著更新時才打印（避免頻繁打印）
                    if (
                        self.short_trough < old_trough
                        and (old_trough - self.short_trough) / old_trough > 0.005
                    ):  # 0.5%以上的變化才打印
                        print(
                            f"\n\n📉 空單谷值更新: ${old_trough:.2f} → ${self.short_trough:.2f}"
                        )

                # 檢查是否需要激活移動停損
                if (
                    not self.is_short_trail_active
                    and current_close
                    < self.short_entry_price
                    * (1 - self.short_trailing_activate_profit_percent)
                ):
                    self.short_trail_stop_price = self.short_entry_price * (
                        1 - self.short_trailing_min_profit_percent
                    )
                    self.is_short_trail_active = True
                    print(
                        f"\n\n✅ 空單移動停損激活 | 初始止損價: ${self.short_trail_stop_price:.2f}"
                    )
                    self.save_state()

                # 更新移動停損價格
                if self.is_short_trail_active and self.short_trough is not None:
                    # 計算基於谷值回撤的停損價格
                    new_trail_stop = self.short_trough * (
                        1 + self.short_trailing_pullback_percent
                    )

                    # 🔧 重要修正：確保移動停損價格不高於最小獲利保護
                    min_profit_protection = self.short_entry_price * (
                        1 - self.short_trailing_min_profit_percent
                    )

                    # 移動停損價格取較低者（谷值回撤 vs 最小獲利保護）
                    new_trail_stop = min(new_trail_stop, min_profit_protection)

                    old_trail_stop = self.short_trail_stop_price
                    self.short_trail_stop_price = min(
                        (
                            self.short_trail_stop_price
                            if self.short_trail_stop_price is not None
                            else float("inf")
                        ),
                        new_trail_stop,
                    )
                    # 只在停損價格有顯著更新時才打印
                    if (
                        old_trail_stop
                        and self.short_trail_stop_price < old_trail_stop
                        and (old_trail_stop - self.short_trail_stop_price)
                        / old_trail_stop
                        > 0.003
                    ):  # 0.3%以上的變化才打印
                        print(
                            f"\n\n📊 空單移動停損更新: ${old_trail_stop:.2f} → ${self.short_trail_stop_price:.2f}"
                        )
                        self.save_state()

                # 檢查移動停損觸發
                short_trail_stop_triggered = (
                    self.is_short_trail_active
                    and self.short_trail_stop_price is not None
                    and current_close >= self.short_trail_stop_price
                )

                if short_trail_stop_triggered:
                    print(f"\n\n🚨 === 空單移動停損觸發 ===")
                    print(f"時間: {current_time}")
                    print(f"當前價格: ${current_close:.2f}")
                    print(f"移動停損價: ${self.short_trail_stop_price:.2f}")
                    print(f"持倉量: {self.position_size}")

                    close_success = self._close_position(current_close)
                    if close_success:
                        print(f"✅ 空單移動停損平倉完成")
                    else:
                        print(f"❌ 空單移動停損平倉失敗")

        except Exception as e:
            print(f"❌ 移動停損檢查發生錯誤: {e}")


# --- 輔助函數：動態狀態顯示 ---
def calculate_next_kline_time(last_kline_timestamp):
    """計算下次K線時間（4小時週期）"""
    if last_kline_timestamp is None:
        return "未知"

    # 確保 last_kline_timestamp 是 datetime 對象
    if isinstance(last_kline_timestamp, str):
        try:
            last_kline_timestamp = pd.to_datetime(last_kline_timestamp)
        except:
            return "時間格式錯誤"

    # 移除時區資訊，統一使用本地時間
    if hasattr(last_kline_timestamp, "tz") and last_kline_timestamp.tz is not None:
        last_kline_timestamp = last_kline_timestamp.tz_localize(None)

    # 找到當前時間對應的4小時週期
    now = datetime.now()

    # 4小時週期的開始時間點：00:00, 04:00, 08:00, 12:00, 16:00, 20:00
    current_hour = now.hour

    # 計算下一個4小時週期的開始時間
    next_cycle_hours = [0, 4, 8, 12, 16, 20]

    next_kline = None
    for cycle_hour in next_cycle_hours:
        if cycle_hour > current_hour:
            next_kline = now.replace(hour=cycle_hour, minute=0, second=0, microsecond=0)
            break

    # 如果沒有找到（當前時間超過20:00），下次週期是明天的00:00
    if next_kline is None:
        next_kline = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    return next_kline


def format_time_remaining(next_kline_time):
    """計算並格式化到下次K線的剩餘時間"""
    if next_kline_time == "未知" or next_kline_time == "時間格式錯誤":
        return next_kline_time

    now = datetime.now()

    # 如果 next_kline_time 有時區資訊，移除它進行比較
    if hasattr(next_kline_time, "tz") and next_kline_time.tz is not None:
        next_kline_time = next_kline_time.tz_localize(None)

    remaining = next_kline_time - now
    total_seconds = remaining.total_seconds()

    if total_seconds <= 0:
        return "應該有新K線了"

    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = int(total_seconds % 60)

    if hours > 0:
        return f"{hours}時{minutes}分{seconds}秒"
    elif minutes > 0:
        return f"{minutes}分{seconds}秒"
    else:
        return f"{seconds}秒"


def get_spinner_char(counter):
    """獲取旋轉動畫字符"""
    spinner_chars = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    return spinner_chars[counter % len(spinner_chars)]


# --- 主運行邏輯 (實時交易) ---
def run_live_trading():
    """實時交易主函數"""
    # 初始化策略實例 (使用預設最佳參數)
    strategy = TradingStrategy()

    last_kline_timestamp = None
    spinner_counter = 0

    print("\n--- 開始實時交易 ---")
    last_check_time = 0  # 記錄上次檢查K線的時間
    last_trailing_stop_check_time = 0  # 記錄上次檢查移動停損的時間
    TRAILING_STOP_CHECK_SECONDS = 60  # 每60秒檢查一次移動停損

    # 每小時校正一次 JSON 狀態（避免手動干預造成狀態偏移）
    last_state_sync_time = 0
    STATE_SYNC_INTERVAL_SECONDS = 3600


    while True:
        try:
            current_time = time.time()

            # 每分鐘檢查一次移動停損
            if (
                current_time - last_trailing_stop_check_time
                >= TRAILING_STOP_CHECK_SECONDS
            ):
                # 只有在有持倉時才檢查移動停損
                if strategy.position_size != 0:
                    # 靜默執行移動停損檢查，不打印額外日誌
                    strategy.check_trailing_stop_only()
                last_trailing_stop_check_time = current_time


                # 每小時與交易所同步一次狀態，校正JSON（進場價/方向/數量）
                if current_time - last_state_sync_time >= STATE_SYNC_INTERVAL_SECONDS:
                    try:
                        strategy.sync_state_with_exchange(reason="每小時校正")
                    finally:
                        last_state_sync_time = current_time

            # 每60秒檢查一次K線數據
            if current_time - last_check_time >= TRADE_SLEEP_SECONDS:
                # 獲取最新 K 線數據
                df_klines = fetch_bybit_klines(
                    SYMBOL, TIMEFRAME, limit=FETCH_KLINE_LIMIT
                )

                if df_klines.empty:
                    print("\n\n❌ 未獲取到 K 線數據，等待下一週期...")
                    last_check_time = current_time
                    continue

                df_processed = calculate_indicators(df_klines.copy())

                # 確保 df_processed 至少有兩行 (一根完成K線 + 一根當前K線)
                if len(df_processed) < 2:
                    print(
                        f"\n\n⚠️ 數據不足，至少需要2根完整K線。當前僅有 {len(df_processed)} 根。"
                    )
                    last_check_time = current_time
                    continue

                current_bar = df_processed.iloc[-2]  # 倒數第二根是最新完成的K線

                # 如果是第一次運行或有新的K線形成
                if (
                    last_kline_timestamp is None
                    or current_bar.name > last_kline_timestamp
                ):
                    # 先換行，避免覆蓋動態狀態行
                    # 🔧 修正：將UTC時間轉換為台北時間顯示
                    kline_taipei_time = current_bar.name + timedelta(hours=12)
                    print(f"\n\n🔔 檢測到新 4小時 K 線: {kline_taipei_time}")
                    print(f"⏰ 開始技術分析和交易判斷...")

                    # 將最新完成的 K 線傳入策略進行處理
                    strategy.process_bar(current_bar)
                    last_kline_timestamp = current_bar.name

                last_check_time = current_time

            # 靜默等待，不顯示任何狀態更新
            spinner_counter += 1

            time.sleep(1)  # 每秒更新一次顯示

        except Exception as e:
            print(f"\n\n❌ 主循環發生錯誤: {e}")
            time.sleep(TRADE_SLEEP_SECONDS * 2)  # 錯誤時等待更久，避免頻繁報錯


# --- 主程式入口 ---
if __name__ == "__main__":
    print("🏆 ETH 4小時自動交易策略啟動 (移動停損優化版)")
    print("📅 版本更新日期: 2025/8/3")
    print("🔧 新功能: 新增RSI進場限制")
    print("⚠️ 實時交易模式 | 請確認風險")

    # 處理非互動模式
    try:
        input("請確認您已理解風險並準備好，按 Enter 鍵繼續...")
    except EOFError:
        print("檢測到非互動模式，自動確認繼續...")
        time.sleep(2)

    run_live_trading()

