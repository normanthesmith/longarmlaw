"""
ATLAS - Macro Regime Detector
Layer 5: Identifies the current macro environment (risk-on/risk-off,
rate regime, credit stress) to bias all trading decisions.
"""

import os
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class MacroRegimeDetector:
    """
    Detects the current macro regime using:
    - Yield curve (2s10s spread) → recession signal
    - VIX level → fear/complacency
    - Dollar (DXY proxy) → risk-on/off
    - Credit spreads (HYG/LQD proxy) → credit stress
    """

    def __init__(self):
        self.alpha_vantage_key = os.getenv("ALPHA_VANTAGE_KEY", "")
        self.fred_key = os.getenv("FRED_API_KEY", "")
        self.regime_cache = {}
        self.cache_expiry = None
        self.cache_duration = 3600  # 1 hour cache

        # Regime definitions
        self.regimes = {
            "RISK_ON_BULL": "Risk-On Bullish",
            "RISK_ON_CAUTION": "Risk-On with Caution",
            "NEUTRAL": "Neutral/Transitional",
            "RISK_OFF_MILD": "Risk-Off Mild",
            "RISK_OFF_STRESS": "Risk-Off Stress",
        }

    def get_fred_series(self, series_id, limit=30):
        """Fetch economic data from FRED."""
        try:
            if not self.fred_key:
                return None
            url = "https://api.stlouisfed.org/fred/series/observations"
            params = {
                "series_id": series_id,
                "api_key": self.fred_key,
                "file_type": "json",
                "limit": limit,
                "sort_order": "desc",
            }
            r = requests.get(url, params=params, timeout=10)
            if r.status_code == 200:
                data = r.json()
                obs = data.get("observations", [])
                if obs:
                    values = []
                    for o in obs:
                        try:
                            values.append(float(o["value"]))
                        except (ValueError, KeyError):
                            continue
                    return values if values else None
        except Exception as e:
            logger.error(f"FRED fetch error for {series_id}: {e}")
        return None

    def get_alpha_vantage_quote(self, symbol):
        """Get current quote from Alpha Vantage."""
        try:
            url = "https://www.alphavantage.co/query"
            params = {
                "function": "GLOBAL_QUOTE",
                "symbol": symbol,
                "apikey": self.alpha_vantage_key,
            }
            r = requests.get(url, params=params, timeout=10)
            if r.status_code == 200:
                data = r.json()
                quote = data.get("Global Quote", {})
                price = quote.get("05. price")
                change_pct = quote.get("10. change percent", "0%").replace("%", "")
                if price:
                    return {
                        "price": float(price),
                        "change_pct": float(change_pct),
                    }
        except Exception as e:
            logger.error(f"Alpha Vantage quote error for {symbol}: {e}")
        return None

    def calculate_yield_curve_signal(self):
        """
        2s10s spread: negative = inverted = bearish signal.
        Returns score from -2 (very bearish) to +2 (very bullish).
        """
        spread_data = self.get_fred_series("T10Y2Y", limit=10)
        if not spread_data:
            # Fallback: neutral
            return 0, "Yield curve data unavailable", 0.0

        current_spread = spread_data[0]
        avg_spread = np.mean(spread_data)

        if current_spread < -0.5:
            score = -2
            label = f"Deeply inverted ({current_spread:.2f}%) — recession risk elevated"
        elif current_spread < 0:
            score = -1
            label = f"Inverted ({current_spread:.2f}%) — caution warranted"
        elif current_spread < 0.5:
            score = 0
            label = f"Flat/neutral ({current_spread:.2f}%) — transitional"
        elif current_spread < 1.5:
            score = 1
            label = f"Positive ({current_spread:.2f}%) — healthy"
        else:
            score = 2
            label = f"Steep ({current_spread:.2f}%) — expansionary"

        return score, label, current_spread

    def calculate_vix_signal(self):
        """
        VIX: >30 = stress, 20-30 = elevated, 15-20 = normal, <15 = complacent.
        Returns score from -2 to +2.
        """
        vix_data = self.get_alpha_vantage_quote("VIX")
        if not vix_data:
            # Try FRED VIX
            vix_series = self.get_fred_series("VIXCLS", limit=5)
            if vix_series:
                vix = vix_series[0]
            else:
                return 0, "VIX data unavailable", 20.0
        else:
            vix = vix_data["price"]

        if vix > 35:
            score = -2
            label = f"EXTREME FEAR (VIX {vix:.1f}) — major stress event"
        elif vix > 25:
            score = -1
            label = f"Elevated fear (VIX {vix:.1f}) — risk-off"
        elif vix > 18:
            score = 0
            label = f"Normal volatility (VIX {vix:.1f}) — neutral"
        elif vix > 13:
            score = 1
            label = f"Low volatility (VIX {vix:.1f}) — risk-on"
        else:
            score = 1  # Complacency is not bullish in itself
            label = f"Complacency risk (VIX {vix:.1f}) — potential for spike"

        return score, label, vix

    def calculate_credit_signal(self):
        """
        High yield credit spreads proxy: HYG price as risk appetite indicator.
        Rising HYG = credit tightening = risk-on. Falling = stress.
        """
        hyg_data = self.get_alpha_vantage_quote("HYG")
        if not hyg_data:
            return 0, "Credit data unavailable", 0.0

        change = hyg_data["change_pct"]

        if change < -1.5:
            score = -2
            label = f"HYG {change:.2f}% — credit stress, avoid risk"
        elif change < -0.5:
            score = -1
            label = f"HYG {change:.2f}% — credit softening"
        elif change < 0.5:
            score = 0
            label = f"HYG {change:.2f}% — credit neutral"
        elif change < 1.5:
            score = 1
            label = f"HYG {change:.2f}% — credit supportive"
        else:
            score = 2
            label = f"HYG {change:.2f}% — credit very strong"

        return score, label, change

    def calculate_momentum_signal(self):
        """
        SPY relative to 200-day MA: above = bull regime, below = bear regime.
        """
        spy_data = self.get_alpha_vantage_quote("SPY")
        if not spy_data:
            return 0, "SPY data unavailable", 0.0

        change = spy_data["change_pct"]

        # Simple day's change as momentum proxy (full MA would need historical)
        if change > 1.0:
            score = 2
            label = f"SPY +{change:.2f}% — strong risk-on day"
        elif change > 0.25:
            score = 1
            label = f"SPY +{change:.2f}% — mild risk-on"
        elif change > -0.25:
            score = 0
            label = f"SPY {change:.2f}% — neutral day"
        elif change > -1.0:
            score = -1
            label = f"SPY {change:.2f}% — mild risk-off"
        else:
            score = -2
            label = f"SPY {change:.2f}% — strong risk-off day"

        return score, label, change

    def detect_regime(self, force_refresh=False):
        """
        Master regime detection. Combines all signals into one regime score.
        Returns full regime analysis dict.
        """
        now = datetime.utcnow()

        # Use cache if valid
        if (
            not force_refresh
            and self.cache_expiry
            and now < self.cache_expiry
            and self.regime_cache
        ):
            return self.regime_cache

        logger.info("Running macro regime detection...")

        # Run all signals
        yc_score, yc_label, yc_value = self.calculate_yield_curve_signal()
        vix_score, vix_label, vix_value = self.calculate_vix_signal()
        credit_score, credit_label, credit_value = self.calculate_credit_signal()
        momentum_score, momentum_label, momentum_value = self.calculate_momentum_signal()

        # Weighted composite score
        # Yield curve gets highest weight (structural), VIX next (real-time fear)
        weights = {"yield_curve": 0.35, "vix": 0.30, "credit": 0.20, "momentum": 0.15}
        composite = (
            yc_score * weights["yield_curve"]
            + vix_score * weights["vix"]
            + credit_score * weights["credit"]
            + momentum_score * weights["momentum"]
        )

        # Map composite to regime
        if composite >= 1.2:
            regime_key = "RISK_ON_BULL"
            bias = "LONG"
            confidence = min(95, int(50 + composite * 20))
        elif composite >= 0.3:
            regime_key = "RISK_ON_CAUTION"
            bias = "LONG_SELECTIVE"
            confidence = int(50 + composite * 15)
        elif composite >= -0.3:
            regime_key = "NEUTRAL"
            bias = "NEUTRAL"
            confidence = 40
        elif composite >= -1.0:
            regime_key = "RISK_OFF_MILD"
            bias = "REDUCED"
            confidence = int(50 + abs(composite) * 15)
        else:
            regime_key = "RISK_OFF_STRESS"
            bias = "DEFENSIVE"
            confidence = min(95, int(50 + abs(composite) * 20))

        result = {
            "timestamp": now.isoformat(),
            "regime": regime_key,
            "regime_label": self.regimes[regime_key],
            "composite_score": round(composite, 3),
            "bias": bias,
            "confidence": confidence,
            "signals": {
                "yield_curve": {
                    "score": yc_score,
                    "label": yc_label,
                    "value": yc_value,
                    "weight": weights["yield_curve"],
                },
                "vix": {
                    "score": vix_score,
                    "label": vix_label,
                    "value": vix_value,
                    "weight": weights["vix"],
                },
                "credit": {
                    "score": credit_score,
                    "label": credit_label,
                    "value": credit_value,
                    "weight": weights["credit"],
                },
                "momentum": {
                    "score": momentum_score,
                    "label": momentum_label,
                    "value": momentum_value,
                    "weight": weights["momentum"],
                },
            },
            "trading_implications": self._get_implications(regime_key, bias),
        }

        self.regime_cache = result
        self.cache_expiry = now + timedelta(seconds=self.cache_duration)

        return result

    def _get_implications(self, regime_key, bias):
        """Translate regime into specific trading implications."""
        implications = {
            "RISK_ON_BULL": [
                "Favour long momentum trades on dips to VWAP",
                "High volume breakouts likely to sustain — participate",
                "Tight stops acceptable — trend is your friend",
                "All asset classes: equities, commodities, high-beta",
            ],
            "RISK_ON_CAUTION": [
                "Long bias but size positions conservatively",
                "Prefer large-cap quality over high-beta",
                "Take profits faster — don't overstay",
                "Watch for reversal signals at key levels",
            ],
            "NEUTRAL": [
                "Reduce position sizes across the board",
                "Focus on range-bound mean-reversion setups",
                "Avoid breakout trades — likely to fail",
                "Wait for regime clarity before adding exposure",
            ],
            "RISK_OFF_MILD": [
                "Reduce long exposure significantly",
                "Short underperforming sectors on bounces",
                "Increase cash allocation",
                "Focus on defensive assets: gold, bonds, staples",
            ],
            "RISK_OFF_STRESS": [
                "DEFENSIVE POSTURE — preserve capital above all",
                "Do not trade against the macro tide",
                "Only counter-trend shorts on technical bounces",
                "Cash is a position in stress regimes",
            ],
        }
        return implications.get(regime_key, [])
