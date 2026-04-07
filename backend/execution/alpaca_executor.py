Content is user-generated and unverified.
"""
ATLAS - Alpaca Execution Layer
Handles all communication with Alpaca paper trading API.
Fetches market data, account info, and submits/manages orders.
"""

import os
import logging
import pandas as pd
from datetime import datetime, timedelta, timezone
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame

logger = logging.getLogger(__name__)


class AlpacaExecutor:
    """Wraps Alpaca SDK for ATLAS. Paper trading mode."""

    def __init__(self):
        self.api_key = os.getenv("ALPACA_API_KEY", "")
        self.secret_key = os.getenv("ALPACA_SECRET_KEY", "")
        self.base_url = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
        self.trading_client = None
        self.data_client = None
        self._connected = False
        self._connect()

    def _connect(self):
        try:
            if not self.api_key or not self.secret_key:
                logger.warning("Alpaca keys not configured - running in demo mode")
                return
            self.trading_client = TradingClient(
                api_key=self.api_key,
                secret_key=self.secret_key,
                paper=True,
            )
            self.data_client = StockHistoricalDataClient(
                api_key=self.api_key,
                secret_key=self.secret_key,
            )
            account = self.trading_client.get_account()
            account_id = str(account.id)
            logger.info(f"Alpaca connected - Account: {account_id[:8]}... Equity: ${float(account.equity):,.2f}")
            self._connected = True
        except Exception as e:
            logger.error(f"Alpaca connection failed: {e}")
            self._connected = False

    def is_connected(self):
        return self._connected

    def get_account(self):
        if not self._connected:
            return self._demo_account()
        try:
            acc = self.trading_client.get_account()
            return {
                "id": str(acc.id),
                "equity": float(acc.equity),
                "cash": float(acc.cash),
                "buying_power": float(acc.buying_power),
                "portfolio_value": float(acc.portfolio_value),
                "daytrade_count": acc.daytrade_count,
                "pattern_day_trader": acc.pattern_day_trader,
                "trading_blocked": acc.trading_blocked,
                "status": str(acc.status),
                "connected": True,
            }
        except Exception as e:
            logger.error(f"get_account error: {e}")
            return self._demo_account()

    def get_bars(self, symbol, timeframe_minutes=5, limit=100):
        if not self._connected:
            return self._generate_demo_bars(symbol, limit)
        try:
            end = datetime.now(timezone.utc)
            hours_back = max(2, (limit * timeframe_minutes) // 60 + 2)
            start = end - timedelta(hours=hours_back)

            request = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Minute,
                start=start,
                end=end,
                limit=limit * timeframe_minutes,
                adjustment="raw",
            )
            bars = self.data_client.get_stock_bars(request)

            if symbol not in bars.data or not bars.data[symbol]:
                logger.warning(f"No bars returned for {symbol}")
                return pd.DataFrame()

            df = pd.DataFrame([
                {
                    "open": float(b.open),
                    "high": float(b.high),
                    "low": float(b.low),
                    "close": float(b.close),
                    "volume": float(b.volume),
                    "timestamp": b.timestamp,
                }
                for b in bars.data[symbol]
            ])
            df.set_index("timestamp", inplace=True)

            if timeframe_minutes > 1:
                df = df.resample(f"{timeframe_minutes}min").agg({
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                }).dropna()

            return df.tail(limit)

        except Exception as e:
            logger.error(f"get_bars error for {symbol}: {e}")
            return pd.DataFrame()

    def get_latest_quote(self, symbol):
        if not self._connected:
            return None
        try:
            request = StockLatestQuoteRequest(symbol_or_symbols=symbol)
            quote = self.data_client.get_stock_latest_quote(request)
            if symbol in quote:
                q = quote[symbol]
                return {
                    "bid": float(q.bid_price) if q.bid_price else None,
                    "ask": float(q.ask_price) if q.ask_price else None,
                    "bid_size": int(q.bid_size) if q.bid_size else 0,
                    "ask_size": int(q.ask_size) if q.ask_size else 0,
                    "mid": (float(q.bid_price) + float(q.ask_price)) / 2
                           if q.bid_price and q.ask_price else None,
                }
        except Exception as e:
            logger.error(f"get_latest_quote error for {symbol}: {e}")
        return None

    def get_positions(self):
        if not self._connected:
            return []
        try:
            positions = self.trading_client.get_all_positions()
            return [
                {
                    "symbol": p.symbol,
                    "qty": float(p.qty),
                    "side": str(p.side),
                    "avg_entry_price": float(p.avg_entry_price),
                    "current_price": float(p.current_price) if p.current_price else None,
                    "market_value": float(p.market_value) if p.market_value else None,
                    "unrealized_pl": float(p.unrealized_pl) if p.unrealized_pl else None,
                    "unrealized_plpc": float(p.unrealized_plpc) if p.unrealized_plpc else None,
                }
                for p in positions
            ]
        except Exception as e:
            logger.error(f"get_positions error: {e}")
            return []

    def get_orders(self, status="open", limit=20):
        if not self._connected:
            return []
        try:
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus
            status_map = {
                "open": QueryOrderStatus.OPEN,
                "closed": QueryOrderStatus.CLOSED,
                "all": QueryOrderStatus.ALL,
            }
            request = GetOrdersRequest(
                status=status_map.get(status, QueryOrderStatus.ALL),
                limit=limit,
            )
            orders = self.trading_client.get_orders(request)
            return [
                {
                    "id": str(o.id),
                    "symbol": o.symbol,
                    "qty": float(o.qty) if o.qty else 0,
                    "filled_qty": float(o.filled_qty) if o.filled_qty else 0,
                    "side": str(o.side),
                    "type": str(o.order_type),
                    "status": str(o.status),
                    "submitted_at": str(o.submitted_at),
                    "filled_at": str(o.filled_at) if o.filled_at else None,
                    "filled_avg_price": float(o.filled_avg_price) if o.filled_avg_price else None,
                }
                for o in orders
            ]
        except Exception as e:
            logger.error(f"get_orders error: {e}")
            return []

    def submit_market_order(self, symbol, qty, side, risk_check_result):
        if not self._connected:
            logger.warning(f"Demo mode - would submit {side} {qty} shares of {symbol}")
            return {"id": "DEMO", "symbol": symbol, "qty": qty, "side": side, "status": "DEMO"}

        if not risk_check_result.get("approved"):
            logger.error(f"Order blocked by risk manager: {risk_check_result.get('rejection_reason')}")
            return None

        try:
            order_side = OrderSide.BUY if side == "LONG" else OrderSide.SELL
            request = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=order_side,
                time_in_force=TimeInForce.DAY,
            )
            order = self.trading_client.submit_order(request)
            logger.info(f"Order submitted: {side} {qty} {symbol} - ID: {order.id}")
            return {
                "id": str(order.id),
                "symbol": symbol,
                "qty": qty,
                "side": side,
                "status": str(order.status),
                "submitted_at": str(order.submitted_at),
            }
        except Exception as e:
            logger.error(f"Order submission error: {e}")
            return None

    def close_position(self, symbol):
        if not self._connected:
            logger.warning(f"Demo mode - would close position in {symbol}")
            return {"symbol": symbol, "status": "DEMO_CLOSED"}
        try:
            self.trading_client.close_position(symbol)
            logger.info(f"Position closed: {symbol}")
            return {"symbol": symbol, "status": "closed"}
        except Exception as e:
            logger.error(f"close_position error for {symbol}: {e}")
            return None

    def is_market_open(self):
        if not self._connected:
            return True
        try:
            clock = self.trading_client.get_clock()
            return clock.is_open
        except Exception as e:
            logger.error(f"is_market_open error: {e}")
            return False

    def _demo_account(self):
        account_size = float(os.getenv("ACCOUNT_SIZE", 25000))
        return {
            "id": "DEMO-ACCOUNT",
            "equity": account_size,
            "cash": account_size,
            "buying_power": account_size * 4,
            "portfolio_value": account_size,
            "daytrade_count": 0,
            "pattern_day_trader": False,
            "trading_blocked": False,
            "status": "ACTIVE",
            "connected": False,
        }

    def _generate_demo_bars(self, symbol, limit=100):
        import numpy as np
        prices = {"SPY": 500, "QQQ": 430, "AAPL": 185, "NVDA": 800,
                  "BTCUSD": 80000, "ETHUSD": 3000, "DEFAULT": 100}
        base = prices.get(symbol, prices["DEFAULT"])
        import random
        random.seed(hash(symbol) % 1000)
        closes = [base]
        for _ in range(limit):
            closes.append(closes[-1] * (1 + random.gauss(0, 0.001)))
        closes = closes[1:]
        dates = pd.date_range(end=datetime.now(timezone.utc), periods=limit, freq="5min")
        df = pd.DataFrame(index=dates)
        df["close"] = closes
        df["open"] = [c * (1 + random.gauss(0, 0.0005)) for c in closes]
        df["high"] = [max(o, c) * (1 + abs(random.gauss(0, 0.001)))
                      for o, c in zip(df["open"], df["close"])]
        df["low"] = [min(o, c) * (1 - abs(random.gauss(0, 0.001)))
                     for o, c in zip(df["open"], df["close"])]
        df["volume"] = [random.randint(100000, 5000000) for _ in range(limit)]
        return df