"""
ATLAS - Market Structure Engine
Layer 6: Identifies trend, key levels, VWAP, volume profile,
and generates structural bias for each instrument.
"""

import numpy as np
import pandas as pd
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class MarketStructureEngine:
    """
    Analyses intraday price structure:
    - Trend direction (HH/HL vs LH/LL)
    - VWAP and deviation
    - Key support/resistance levels
    - Volume profile (high/low volume nodes)
    - Momentum and RSI
    - Break of structure detection
    """

    def __init__(self):
        self.structure_cache = {}

    def calculate_vwap(self, bars_df):
        """
        Calculate VWAP and standard deviation bands.
        VWAP = Σ(Typical Price × Volume) / Σ(Volume)
        """
        if bars_df.empty or len(bars_df) < 2:
            return None, None, None

        df = bars_df.copy()
        df["typical_price"] = (df["high"] + df["low"] + df["close"]) / 3
        df["tpv"] = df["typical_price"] * df["volume"]

        cumulative_tpv = df["tpv"].cumsum()
        cumulative_vol = df["volume"].cumsum()

        df["vwap"] = cumulative_tpv / cumulative_vol

        # VWAP standard deviation bands
        df["variance"] = (df["typical_price"] - df["vwap"]) ** 2
        df["cum_variance"] = (df["variance"] * df["volume"]).cumsum()
        df["vwap_std"] = np.sqrt(df["cum_variance"] / cumulative_vol)

        current_vwap = df["vwap"].iloc[-1]
        current_std = df["vwap_std"].iloc[-1]

        bands = {
            "vwap": round(current_vwap, 4),
            "upper_1": round(current_vwap + current_std, 4),
            "lower_1": round(current_vwap - current_std, 4),
            "upper_2": round(current_vwap + 2 * current_std, 4),
            "lower_2": round(current_vwap - 2 * current_std, 4),
        }

        return current_vwap, current_std, bands

    def detect_swing_points(self, bars_df, lookback=5):
        """
        Identify swing highs and lows.
        A swing high is a bar where the high is highest of surrounding bars.
        """
        if len(bars_df) < lookback * 2 + 1:
            return [], []

        highs = bars_df["high"].values
        lows = bars_df["low"].values

        swing_highs = []
        swing_lows = []

        for i in range(lookback, len(highs) - lookback):
            window_highs = highs[i - lookback : i + lookback + 1]
            window_lows = lows[i - lookback : i + lookback + 1]

            if highs[i] == max(window_highs):
                swing_highs.append(
                    {
                        "index": i,
                        "price": float(highs[i]),
                        "time": bars_df.index[i] if hasattr(bars_df.index, "__iter__") else i,
                    }
                )

            if lows[i] == min(window_lows):
                swing_lows.append(
                    {
                        "index": i,
                        "price": float(lows[i]),
                        "time": bars_df.index[i] if hasattr(bars_df.index, "__iter__") else i,
                    }
                )

        return swing_highs, swing_lows

    def detect_trend(self, swing_highs, swing_lows, current_price):
        """
        Determine trend from swing point sequence.
        Bullish: HH + HL sequence
        Bearish: LH + LL sequence
        """
        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return "UNDEFINED", 0

        recent_highs = sorted(swing_highs, key=lambda x: x["index"])[-3:]
        recent_lows = sorted(swing_lows, key=lambda x: x["index"])[-3:]

        # Check for higher highs and higher lows
        hh = all(
            recent_highs[i]["price"] > recent_highs[i - 1]["price"]
            for i in range(1, len(recent_highs))
        )
        hl = all(
            recent_lows[i]["price"] > recent_lows[i - 1]["price"]
            for i in range(1, len(recent_lows))
        )

        # Check for lower highs and lower lows
        lh = all(
            recent_highs[i]["price"] < recent_highs[i - 1]["price"]
            for i in range(1, len(recent_highs))
        )
        ll = all(
            recent_lows[i]["price"] < recent_lows[i - 1]["price"]
            for i in range(1, len(recent_lows))
        )

        if hh and hl:
            strength = 2 if (hh and hl) else 1
            return "BULLISH", strength
        elif lh and ll:
            strength = 2 if (lh and ll) else 1
            return "BEARISH", -strength
        elif hh or hl:
            return "BULLISH_WEAK", 1
        elif lh or ll:
            return "BEARISH_WEAK", -1
        else:
            return "RANGING", 0

    def calculate_key_levels(self, bars_df, swing_highs, swing_lows, current_price):
        """
        Identify nearest key support and resistance levels.
        """
        all_highs = [sh["price"] for sh in swing_highs]
        all_lows = [sl["price"] for sl in swing_lows]

        resistances = sorted([h for h in all_highs if h > current_price])[:3]
        supports = sorted([l for l in all_lows if l < current_price], reverse=True)[:3]

        # Also add session high/low
        session_high = float(bars_df["high"].max())
        session_low = float(bars_df["low"].min())

        if session_high > current_price and session_high not in resistances:
            resistances.append(session_high)
            resistances = sorted(resistances)[:3]

        if session_low < current_price and session_low not in supports:
            supports.append(session_low)
            supports = sorted(supports, reverse=True)[:3]

        return {
            "resistance": [round(r, 4) for r in resistances],
            "support": [round(s, 4) for s in supports],
            "session_high": round(session_high, 4),
            "session_low": round(session_low, 4),
        }

    def calculate_volume_profile(self, bars_df, bins=20):
        """
        Build volume profile (price histogram weighted by volume).
        Identifies POC, HVN, LVN.
        """
        if bars_df.empty or len(bars_df) < 5:
            return None

        price_min = bars_df["low"].min()
        price_max = bars_df["high"].max()
        price_range = price_max - price_min

        if price_range == 0:
            return None

        # Create price bins
        bin_size = price_range / bins
        volume_at_price = {}

        for _, row in bars_df.iterrows():
            bar_low = row["low"]
            bar_high = row["high"]
            bar_volume = row["volume"]
            bar_range = bar_high - bar_low

            # Distribute volume across price range of bar
            for b in range(bins):
                bin_low = price_min + b * bin_size
                bin_high = bin_low + bin_size

                # Calculate overlap
                overlap_low = max(bin_low, bar_low)
                overlap_high = min(bin_high, bar_high)

                if overlap_high > overlap_low and bar_range > 0:
                    overlap_pct = (overlap_high - overlap_low) / bar_range
                    bin_center = (bin_low + bin_high) / 2
                    volume_at_price[round(bin_center, 4)] = (
                        volume_at_price.get(round(bin_center, 4), 0)
                        + bar_volume * overlap_pct
                    )

        if not volume_at_price:
            return None

        # Find POC
        poc_price = max(volume_at_price, key=volume_at_price.get)
        total_volume = sum(volume_at_price.values())

        # Value area (70% of volume)
        sorted_levels = sorted(volume_at_price.items(), key=lambda x: x[1], reverse=True)
        value_area_vol = 0
        value_area_prices = []
        target = total_volume * 0.70

        for price, vol in sorted_levels:
            value_area_prices.append(price)
            value_area_vol += vol
            if value_area_vol >= target:
                break

        vah = max(value_area_prices) if value_area_prices else poc_price
        val = min(value_area_prices) if value_area_prices else poc_price

        return {
            "poc": round(poc_price, 4),
            "value_area_high": round(vah, 4),
            "value_area_low": round(val, 4),
            "profile": {
                str(k): round(v, 0) for k, v in sorted(volume_at_price.items())
            },
        }

    def calculate_rsi(self, closes, period=14):
        """Standard RSI calculation."""
        if len(closes) < period + 1:
            return 50.0

        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        avg_gain = np.mean(gains[:period])
        avg_loss = np.mean(losses[:period])

        if avg_loss == 0:
            return 100.0

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        rs = avg_gain / avg_loss if avg_loss != 0 else 100
        return round(100 - (100 / (1 + rs)), 2)

    def calculate_momentum_score(self, bars_df, current_price, vwap):
        """
        Composite momentum score combining multiple signals.
        Returns -2 to +2 score.
        """
        score = 0
        signals = []

        closes = bars_df["close"].values

        # RSI signal
        rsi = self.calculate_rsi(closes)
        if rsi > 70:
            score += 1
            signals.append(f"RSI overbought ({rsi:.0f})")
        elif rsi > 55:
            score += 0.5
            signals.append(f"RSI bullish ({rsi:.0f})")
        elif rsi < 30:
            score -= 1
            signals.append(f"RSI oversold ({rsi:.0f})")
        elif rsi < 45:
            score -= 0.5
            signals.append(f"RSI bearish ({rsi:.0f})")
        else:
            signals.append(f"RSI neutral ({rsi:.0f})")

        # Price vs VWAP
        if vwap:
            vwap_deviation = (current_price - vwap) / vwap * 100
            if vwap_deviation > 0.5:
                score += 0.5
                signals.append(f"Above VWAP +{vwap_deviation:.2f}%")
            elif vwap_deviation < -0.5:
                score -= 0.5
                signals.append(f"Below VWAP {vwap_deviation:.2f}%")
            else:
                signals.append(f"At VWAP ({vwap_deviation:.2f}%)")

        # Short-term momentum (last 5 bars)
        if len(closes) >= 5:
            recent_return = (closes[-1] - closes[-5]) / closes[-5] * 100
            if recent_return > 0.3:
                score += 0.5
                signals.append(f"5-bar momentum +{recent_return:.2f}%")
            elif recent_return < -0.3:
                score -= 0.5
                signals.append(f"5-bar momentum {recent_return:.2f}%")

        return {
            "score": round(min(2, max(-2, score)), 2),
            "rsi": rsi,
            "signals": signals,
        }

    def analyse(self, symbol, bars_df):
        """
        Full structure analysis for a symbol.
        Returns complete analysis dict.
        """
        if bars_df is None or bars_df.empty or len(bars_df) < 10:
            return {"error": f"Insufficient data for {symbol}", "symbol": symbol}

        current_price = float(bars_df["close"].iloc[-1])
        current_volume = float(bars_df["volume"].iloc[-1])
        avg_volume = float(bars_df["volume"].mean())

        # Core calculations
        vwap, vwap_std, vwap_bands = self.calculate_vwap(bars_df)
        swing_highs, swing_lows = self.detect_swing_points(bars_df)
        trend, trend_strength = self.detect_trend(swing_highs, swing_lows, current_price)
        key_levels = self.calculate_key_levels(bars_df, swing_highs, swing_lows, current_price)
        volume_profile = self.calculate_volume_profile(bars_df)
        momentum = self.calculate_momentum_score(bars_df, current_price, vwap)

        # Volume surge detection
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1
        volume_surge = volume_ratio > 1.5

        # Distance from VWAP in std devs
        vwap_deviation_pct = 0
        if vwap and vwap > 0:
            vwap_deviation_pct = (current_price - vwap) / vwap * 100

        # Overall structural bias
        bias_score = 0
        if "BULLISH" in trend:
            bias_score += 1 if "WEAK" in trend else 2
        elif "BEARISH" in trend:
            bias_score -= 1 if "WEAK" in trend else 2

        bias_score += momentum["score"] * 0.5

        if bias_score > 1:
            structural_bias = "BULLISH"
        elif bias_score > 0:
            structural_bias = "MILDLY_BULLISH"
        elif bias_score < -1:
            structural_bias = "BEARISH"
        elif bias_score < 0:
            structural_bias = "MILDLY_BEARISH"
        else:
            structural_bias = "NEUTRAL"

        return {
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "current_price": current_price,
            "trend": {
                "direction": trend,
                "strength": trend_strength,
                "description": self._trend_description(trend, trend_strength),
            },
            "vwap": vwap_bands,
            "vwap_deviation_pct": round(vwap_deviation_pct, 3),
            "key_levels": key_levels,
            "volume_profile": volume_profile,
            "momentum": momentum,
            "volume": {
                "current": int(current_volume),
                "average": int(avg_volume),
                "ratio": round(volume_ratio, 2),
                "surge": volume_surge,
            },
            "structural_bias": structural_bias,
            "bias_score": round(bias_score, 2),
        }

    def _trend_description(self, trend, strength):
        descriptions = {
            "BULLISH": "Strong uptrend — HH + HL sequence confirmed",
            "BULLISH_WEAK": "Weak bullish structure — partial confirmation",
            "BEARISH": "Strong downtrend — LH + LL sequence confirmed",
            "BEARISH_WEAK": "Weak bearish structure — partial confirmation",
            "RANGING": "Ranging market — no directional edge",
            "UNDEFINED": "Insufficient data for trend determination",
        }
        return descriptions.get(trend, "Unknown")
