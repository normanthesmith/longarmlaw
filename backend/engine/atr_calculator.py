"""
ATLAS - ATR-Based Trade Calculator
Uses Average True Range to place stops and targets scientifically.

The Patient Architect profile:
- Stop: 1.5× ATR beyond the structural level
- Target: 4× ATR from entry (produces ~2.7R given stop)
- Trail: activates at 1.5R profit, trails at 1× ATR

Why ATR and not fixed percentage?
Because a 1% stop on SPY ($5) is very different from 1% on NVDA ($8).
ATR normalises to each instrument's actual volatility — 
stops are placed where the market noise doesn't reach, not where
a round number lands.
"""

import numpy as np
import pandas as pd
import logging

logger = logging.getLogger(__name__)


class ATRCalculator:
    """
    Calculates ATR and derives all trade parameters from it.
    """

    def calculate_atr(self, bars_df: pd.DataFrame, period: int = 14) -> float:
        """
        True Range = max of:
        1. High - Low
        2. |High - Previous Close|
        3. |Low - Previous Close|

        ATR = Exponential Moving Average of True Range over `period` bars.
        """
        if bars_df is None or len(bars_df) < period + 1:
            return None

        df = bars_df.copy()
        df["prev_close"] = df["close"].shift(1)

        df["tr"] = np.maximum(
            df["high"] - df["low"],
            np.maximum(
                abs(df["high"] - df["prev_close"]),
                abs(df["low"] - df["prev_close"]),
            ),
        )

        # Wilder's smoothing (standard ATR)
        atr_values = []
        tr = df["tr"].dropna().values

        if len(tr) < period:
            return None

        # First ATR = simple average of first `period` TRs
        first_atr = np.mean(tr[:period])
        atr_values.append(first_atr)

        # Subsequent ATRs use Wilder's smoothing
        for i in range(period, len(tr)):
            atr = (atr_values[-1] * (period - 1) + tr[i]) / period
            atr_values.append(atr)

        return round(float(atr_values[-1]), 4) if atr_values else None

    def calculate_trade_levels(
        self,
        direction: str,
        entry_price: float,
        atr: float,
        stop_multiplier: float = 1.5,
        target_multiplier: float = 4.0,
        nearest_structure: float = None,
    ) -> dict:
        """
        Calculate entry, stop, and target using ATR.

        For LONG:
            Stop = entry - (atr × stop_multiplier)
            If nearest support exists and is closer, use that
            Target = entry + (atr × target_multiplier)

        For SHORT:
            Stop = entry + (atr × stop_multiplier)
            Target = entry - (atr × target_multiplier)

        Returns full trade level set.
        """
        if not atr or atr <= 0 or not entry_price or entry_price <= 0:
            return None

        atr_stop_distance = atr * stop_multiplier
        atr_target_distance = atr * target_multiplier

        if direction == "LONG":
            # Stop: below entry by ATR distance
            stop_atr = entry_price - atr_stop_distance

            # If there's a structural support level that's tighter, use it
            # (but no tighter than 0.5× ATR)
            if nearest_structure and nearest_structure < entry_price:
                structure_stop = nearest_structure * 0.999  # Just below
                min_stop = entry_price - (atr * 0.5)
                stop_price = max(structure_stop, min_stop)
                stop_method = "STRUCTURAL"
            else:
                stop_price = stop_atr
                stop_method = "ATR"

            target_price = entry_price + atr_target_distance

        else:  # SHORT
            stop_atr = entry_price + atr_stop_distance

            if nearest_structure and nearest_structure > entry_price:
                structure_stop = nearest_structure * 1.001
                max_stop = entry_price + (atr * 0.5)
                stop_price = min(structure_stop, max_stop)
                stop_method = "STRUCTURAL"
            else:
                stop_price = stop_atr
                stop_method = "ATR"

            target_price = entry_price - atr_target_distance

        # Calculate R:R
        risk = abs(entry_price - stop_price)
        reward = abs(target_price - entry_price)
        rr_ratio = reward / risk if risk > 0 else 0

        # Risk as percentage
        risk_pct = risk / entry_price * 100
        reward_pct = reward / entry_price * 100

        return {
            "entry_price": round(entry_price, 4),
            "stop_price": round(stop_price, 4),
            "target_price": round(target_price, 4),
            "stop_method": stop_method,
            "atr": round(atr, 4),
            "atr_stop_distance": round(atr_stop_distance, 4),
            "risk": round(risk, 4),
            "reward": round(reward, 4),
            "risk_pct": round(risk_pct, 3),
            "reward_pct": round(reward_pct, 3),
            "risk_reward": round(rr_ratio, 2),
        }

    def calculate_position_size(
        self,
        entry_price: float,
        stop_price: float,
        account_equity: float,
        risk_pct: float = 0.01,
        size_multiplier: float = 1.0,
    ) -> dict:
        """
        Position size = dollar_risk / stop_distance_per_share

        dollar_risk = account_equity × risk_pct × size_multiplier
        stop_distance = |entry - stop|
        shares = dollar_risk / stop_distance

        Also caps at 40% of account value per position.
        """
        if not all([entry_price, stop_price, account_equity]):
            return {"shares": 0, "dollar_risk": 0, "position_value": 0}

        stop_distance = abs(entry_price - stop_price)
        if stop_distance == 0:
            return {"shares": 0, "dollar_risk": 0, "position_value": 0}

        # Apply size multiplier (emotional circuit breaker)
        effective_risk_pct = risk_pct * size_multiplier
        dollar_risk = account_equity * effective_risk_pct
        
        shares = int(dollar_risk / stop_distance)

        # Cap at 40% of account
        max_position_value = account_equity * 0.40
        position_value = shares * entry_price
        if position_value > max_position_value:
            shares = int(max_position_value / entry_price)
            position_value = shares * entry_price

        actual_dollar_risk = shares * stop_distance

        return {
            "shares": shares,
            "dollar_risk": round(actual_dollar_risk, 2),
            "position_value": round(shares * entry_price, 2),
            "effective_risk_pct": round(effective_risk_pct, 4),
            "size_multiplier": size_multiplier,
        }

    def calculate_trailing_stop(
        self,
        direction: str,
        entry_price: float,
        current_price: float,
        current_stop: float,
        atr: float,
        activation_r: float = 1.5,
        trail_atr_multiplier: float = 1.0,
    ) -> dict:
        """
        Trail the stop once position is `activation_r` × R in profit.
        Stop trails at `trail_atr_multiplier` × ATR from current price.

        For LONG:
            If current_price >= entry + (activation_r × initial_risk):
                new_stop = current_price - (atr × trail_atr_multiplier)
                new_stop = max(new_stop, current_stop)  # never move stop back

        Returns new stop price and whether trailing is active.
        """
        initial_risk = abs(entry_price - current_stop)
        activation_threshold = activation_r * initial_risk

        if direction == "LONG":
            profit = current_price - entry_price
            trailing_active = profit >= activation_threshold

            if trailing_active:
                new_stop = current_price - (atr * trail_atr_multiplier)
                # Never move stop backwards (downward for long)
                new_stop = max(new_stop, current_stop)
            else:
                new_stop = current_stop

        else:  # SHORT
            profit = entry_price - current_price
            trailing_active = profit >= activation_threshold

            if trailing_active:
                new_stop = current_price + (atr * trail_atr_multiplier)
                # Never move stop backwards (upward for short)
                new_stop = min(new_stop, current_stop)
            else:
                new_stop = current_stop

        r_multiple = profit / initial_risk if initial_risk > 0 else 0

        return {
            "new_stop": round(new_stop, 4),
            "trailing_active": trailing_active,
            "r_multiple": round(r_multiple, 2),
            "profit_pct": round(profit / entry_price * 100, 3),
            "stop_moved": new_stop != current_stop,
        }

    def get_thesis_progress(
        self,
        direction: str,
        entry_price: float,
        stop_price: float,
        target_price: float,
        current_price: float,
    ) -> dict:
        """
        Calculate where current price sits between stop and target.
        Used for the dashboard progress bar — shows trade thesis health
        without emphasising dollar P&L.

        Returns 0-100 progress where:
        0 = at stop (thesis invalidated)
        50 = at entry (breakeven)
        100 = at target (thesis complete)
        """
        total_range = abs(target_price - stop_price)
        if total_range == 0:
            return {"progress": 50, "zone": "ENTRY", "r_multiple": 0}

        if direction == "LONG":
            progress = (current_price - stop_price) / total_range * 100
        else:
            progress = (stop_price - current_price) / total_range * 100

        progress = max(0, min(100, progress))

        # Zone classification
        entry_pct = abs(entry_price - stop_price) / total_range * 100
        if progress < 5:
            zone = "STOP_ZONE"      # Near stop — thesis failing
        elif progress < entry_pct - 5:
            zone = "RISK_ZONE"      # Between stop and entry — onside but negative
        elif progress < entry_pct + 10:
            zone = "ENTRY_ZONE"     # Near entry — breakeven area
        elif progress < 75:
            zone = "PROFIT_ZONE"    # Moving toward target
        else:
            zone = "TARGET_ZONE"    # Near target — consider taking profits

        initial_risk = abs(entry_price - stop_price)
        if direction == "LONG":
            r_multiple = (current_price - entry_price) / initial_risk if initial_risk > 0 else 0
        else:
            r_multiple = (entry_price - current_price) / initial_risk if initial_risk > 0 else 0

        return {
            "progress": round(progress, 1),
            "zone": zone,
            "r_multiple": round(r_multiple, 2),
            "entry_pct": round(entry_pct, 1),
        }
