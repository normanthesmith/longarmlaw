"""
ATLAS - Signal Compositor v2
Profile-aware. Uses ATR for all stop/target calculations.
"""

import logging
from datetime import datetime, timezone
from backend.engine.atr_calculator import ATRCalculator
from backend.trader_profile import TraderProfile

logger = logging.getLogger(__name__)


class SignalCompositor:
    def __init__(self, profile: TraderProfile = None):
        self.profile = profile or TraderProfile()
        self.atr_calc = ATRCalculator()
        self.signals_history = []

    def update_profile(self, profile: TraderProfile):
        self.profile = profile

    def compose_signal(self, symbol, structure_analysis, macro_regime, bars_df=None):
        if "error" in structure_analysis:
            return {"symbol": symbol, "action": "NO_TRADE",
                    "reason": structure_analysis["error"], "signals_fired": [],
                    "confluence_score": 0, "required_score": self.profile.min_confluence_score,
                    "timestamp": datetime.now(timezone.utc).isoformat()}

        p = self.profile
        signals_fired = []
        confluence_score = 0.0
        direction = None

        regime = macro_regime.get("regime", "NEUTRAL")
        bias = macro_regime.get("bias", "NEUTRAL")
        trend = structure_analysis.get("trend", {}).get("direction", "UNDEFINED")
        vwap_bands = structure_analysis.get("vwap", {})
        momentum = structure_analysis.get("momentum", {})
        volume = structure_analysis.get("volume", {})
        key_levels = structure_analysis.get("key_levels", {})
        current_price = structure_analysis.get("current_price", 0)
        vwap = vwap_bands.get("vwap") if vwap_bands else None
        vwap_dev = structure_analysis.get("vwap_deviation_pct", 0)

        # ATR
        atr = None
        if bars_df is not None and not bars_df.empty:
            atr = self.atr_calc.calculate_atr(bars_df, period=p.atr_period)

        # Factor 1: Macro gate
        regime_allows_long = p.allows_long(bias)
        regime_allows_short = p.allows_short(bias)

        if regime_allows_long:
            signals_fired.append(f"✓ Macro SUPPORTS long — {regime} ({bias})")
            confluence_score += 1.0
            direction = "LONG"
        elif regime_allows_short:
            signals_fired.append(f"✓ Macro SUPPORTS short — {regime} ({bias})")
            confluence_score += 1.0
            direction = "SHORT"
        elif bias == "NEUTRAL":
            signals_fired.append(f"○ Macro NEUTRAL — {regime}")
        else:
            signals_fired.append(f"✗ Macro BLOCKS — {regime} ({bias})")
            if p.macro_gate_strictness == "STRICT":
                return self._no_trade(symbol, signals_fired, confluence_score,
                                      "Macro regime gate blocked this direction")

        # Factor 2: Structure
        if "BULLISH" in trend:
            score = 0.5 if "WEAK" in trend else 1.0
            if direction is None or direction == "LONG":
                signals_fired.append(f"✓ Structure {trend}")
                confluence_score += score
                direction = "LONG"
            else:
                signals_fired.append(f"✗ Structure {trend} conflicts with SHORT")
                confluence_score -= 0.5
        elif "BEARISH" in trend:
            score = 0.5 if "WEAK" in trend else 1.0
            if direction is None or direction == "SHORT":
                signals_fired.append(f"✓ Structure {trend}")
                confluence_score += score
                direction = "SHORT"
            else:
                signals_fired.append(f"✗ Structure {trend} conflicts with LONG")
                confluence_score -= 0.5
        elif trend == "RANGING" and p.strategy_bias == "TREND_FOLLOWING":
            return self._no_trade(symbol, signals_fired, confluence_score,
                                  "Ranging — no edge for trend-follower")
        else:
            signals_fired.append(f"✗ Structure {trend} — no directional edge")

        if direction is None:
            return self._no_trade(symbol, signals_fired, confluence_score,
                                  "No directional bias established")

        # Factor 3: VWAP
        if vwap and vwap_bands:
            upper_1 = vwap_bands.get("upper_1", vwap * 1.01)
            lower_1 = vwap_bands.get("lower_1", vwap * 0.99)
            if direction == "LONG":
                if lower_1 <= current_price <= vwap * 1.003:
                    signals_fired.append(f"✓ Price AT VWAP ${vwap:.2f} — ideal entry")
                    confluence_score += 1.0
                elif current_price < lower_1:
                    signals_fired.append(f"✓ Price below VWAP -1σ — pullback entry")
                    confluence_score += 0.75
                elif vwap_dev > 1.5:
                    signals_fired.append(f"✗ Price {vwap_dev:.1f}% above VWAP — chasing")
                    confluence_score -= 0.5
                else:
                    signals_fired.append(f"○ VWAP dev {vwap_dev:+.2f}%")
                    confluence_score += 0.25
            else:
                if vwap * 0.997 <= current_price <= upper_1:
                    signals_fired.append(f"✓ Price AT VWAP ${vwap:.2f} — ideal short")
                    confluence_score += 1.0
                elif current_price > upper_1:
                    signals_fired.append(f"✓ Price above VWAP +1σ — extended short")
                    confluence_score += 0.75
                elif vwap_dev < -1.5:
                    signals_fired.append(f"✗ Price {vwap_dev:.1f}% below VWAP — chasing")
                    confluence_score -= 0.5
                else:
                    signals_fired.append(f"○ VWAP dev {vwap_dev:+.2f}%")
                    confluence_score += 0.25
        else:
            signals_fired.append("○ VWAP insufficient data")

        # Factor 4: Volume
        vol_ratio = volume.get("ratio", 1.0)
        vol_surge = volume.get("surge", False)
        if vol_surge:
            signals_fired.append(f"✓ Volume SURGE {vol_ratio:.1f}× avg")
            confluence_score += 1.0
        elif vol_ratio > 1.3:
            signals_fired.append(f"✓ Above-avg volume {vol_ratio:.1f}×")
            confluence_score += 0.75
        elif vol_ratio < 0.7:
            signals_fired.append(f"✗ Low volume {vol_ratio:.1f}×")
            confluence_score -= 0.5
        else:
            signals_fired.append(f"○ Normal volume {vol_ratio:.1f}×")
            confluence_score += 0.25

        # Factor 5: RSI
        rsi = momentum.get("rsi", 50)
        if direction == "LONG":
            if 40 <= rsi <= 62:
                signals_fired.append(f"✓ RSI {rsi:.0f} — healthy, not overbought")
                confluence_score += 1.0
            elif 30 <= rsi < 40:
                signals_fired.append(f"✓ RSI {rsi:.0f} — oversold bounce potential")
                confluence_score += 0.75
            elif rsi > 75:
                signals_fired.append(f"✗ RSI {rsi:.0f} — overbought")
                confluence_score -= 1.0
            else:
                signals_fired.append(f"○ RSI {rsi:.0f} — elevated but acceptable")
                confluence_score += 0.25
        else:
            if 38 <= rsi <= 60:
                signals_fired.append(f"✓ RSI {rsi:.0f} — good short entry")
                confluence_score += 1.0
            elif 60 < rsi <= 70:
                signals_fired.append(f"✓ RSI {rsi:.0f} — elevated, short valid")
                confluence_score += 0.75
            elif rsi < 25:
                signals_fired.append(f"✗ RSI {rsi:.0f} — oversold, bounce risk")
                confluence_score -= 1.0
            else:
                signals_fired.append(f"○ RSI {rsi:.0f} — near oversold, caution")
                confluence_score += 0.25

        # Structural proximity bonus
        resistances = key_levels.get("resistance", [])
        supports = key_levels.get("support", [])
        if direction == "LONG" and supports:
            prox = abs(current_price - supports[0]) / current_price * 100
            if prox < 0.25:
                signals_fired.append(f"✓ Near support ${supports[0]:.2f}")
                confluence_score += 0.5
        if direction == "SHORT" and resistances:
            prox = abs(current_price - resistances[0]) / current_price * 100
            if prox < 0.25:
                signals_fired.append(f"✓ Near resistance ${resistances[0]:.2f}")
                confluence_score += 0.5

        confluence_score = round(min(5.0, max(0.0, confluence_score)), 2)

        # Confluence gate
        if confluence_score < p.min_confluence_score:
            return self._no_trade(symbol, signals_fired, confluence_score,
                                  f"Confluence {confluence_score:.1f}/{p.min_confluence_score}")

        # ATR levels
        nearest_structure = (supports[0] if direction == "LONG" and supports
                             else resistances[0] if direction == "SHORT" and resistances
                             else None)

        if atr:
            levels = self.atr_calc.calculate_trade_levels(
                direction=direction, entry_price=current_price, atr=atr,
                stop_multiplier=p.atr_stop_multiplier,
                target_multiplier=p.atr_target_multiplier,
                nearest_structure=nearest_structure)
        else:
            risk_d = current_price * 0.015
            reward_d = current_price * 0.04
            if direction == "SHORT":
                stop = current_price + risk_d
                target = current_price - reward_d
            else:
                stop = current_price - risk_d
                target = current_price + reward_d
            levels = {"entry_price": current_price, "stop_price": round(stop, 4),
                      "target_price": round(target, 4), "stop_method": "FALLBACK",
                      "atr": None, "risk": round(risk_d, 4), "reward": round(reward_d, 4),
                      "risk_pct": 1.5, "reward_pct": 4.0,
                      "risk_reward": round(reward_d / risk_d, 2)}

        if not levels:
            return self._no_trade(symbol, signals_fired, confluence_score,
                                  "Could not calculate trade levels")

        rr = levels.get("risk_reward", 0)
        if rr < p.min_risk_reward:
            signals_fired.append(f"✗ R:R {rr:.1f} below minimum {p.min_risk_reward}")
            return self._no_trade(symbol, signals_fired, confluence_score,
                                  f"R:R {rr:.1f}:1 below {p.min_risk_reward}:1")

        signals_fired.append(f"✓ R:R {rr:.1f}:1 — meets {p.min_risk_reward}:1 minimum")

        quality = self._assess_quality(confluence_score, rr)
        quality_rank = {"A+": 4, "A": 3, "B": 2, "C": 1}
        if quality_rank.get(quality, 0) < quality_rank.get(p.min_signal_quality, 3):
            return self._no_trade(symbol, signals_fired, confluence_score,
                                  f"Quality {quality} below minimum {p.min_signal_quality}")

        signal = {
            "symbol": symbol, "action": direction, "quality": quality,
            "entry_price": levels["entry_price"], "stop_price": levels["stop_price"],
            "target_price": levels["target_price"], "stop_method": levels.get("stop_method"),
            "atr": levels.get("atr"), "risk_pct": levels["risk_pct"],
            "reward_pct": levels["reward_pct"], "risk_reward": levels["risk_reward"],
            "confluence_score": confluence_score, "required_score": p.min_confluence_score,
            "macro_regime": regime, "macro_bias": bias, "trend": trend, "rsi": rsi,
            "vwap": vwap, "signals_fired": signals_fired,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "PENDING", "profile_name": p.name,
        }

        self.signals_history.append(signal)
        logger.info(f"Signal: {direction} {symbol} | {quality} | Conf:{confluence_score:.1f} | R:R:{rr:.1f}")
        return signal

    def _no_trade(self, symbol, signals_fired, confluence_score, reason):
        return {"symbol": symbol, "action": "NO_TRADE", "quality": None,
                "confluence_score": confluence_score,
                "required_score": self.profile.min_confluence_score,
                "signals_fired": signals_fired, "reason": reason,
                "timestamp": datetime.now(timezone.utc).isoformat()}

    def _assess_quality(self, confluence, rr):
        if confluence >= 4.5 and rr >= 3.5: return "A+"
        elif confluence >= 4.0 and rr >= 2.5: return "A"
        elif confluence >= 3.5 and rr >= 2.0: return "B"
        return "C"

    def get_recent_signals(self, limit=20):
        return self.signals_history[-limit:]
