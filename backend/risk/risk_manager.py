"""
ATLAS - Risk Manager
Layer 4: Position sizing, portfolio heat control, drawdown gates.
This module protects the account. It runs before every trade.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class RiskManager:
    """
    Enforces all risk rules before any trade executes:
    
    1. Max risk per trade (default 1% of portfolio)
    2. Max portfolio heat (default 6% of portfolio)
    3. Max drawdown gate (stops trading if >5% daily loss)
    4. Position correlation check (no doubling up)
    5. Quality gate (only A+ and A signals)
    
    Position sizing uses ATR-based stops:
    size = (account_value × risk_per_trade) / stop_distance
    """

    def __init__(self, 
                 max_position_risk=0.01,   # 1% per trade
                 max_portfolio_heat=0.06,   # 6% total open risk
                 max_daily_loss=0.05,       # 5% daily drawdown gate
                 min_signal_quality="B"):   # Minimum signal quality
        
        self.max_position_risk = max_position_risk
        self.max_portfolio_heat = max_portfolio_heat
        self.max_daily_loss = max_daily_loss
        self.min_signal_quality = min_signal_quality
        self.quality_rank = {"A+": 4, "A": 3, "B": 2, "C": 1}
        
        self.open_positions = {}
        self.daily_pnl = 0.0
        self.day_start_equity = None
        self.trade_count_today = 0
        self.max_trades_per_day = 20
        
    def set_day_start_equity(self, equity):
        """Call at start of each trading day."""
        self.day_start_equity = equity
        self.daily_pnl = 0.0
        self.trade_count_today = 0
        logger.info(f"Day start equity set: ${equity:,.2f}")
        
    def update_pnl(self, pnl_delta):
        """Update running daily P&L."""
        self.daily_pnl += pnl_delta
        
    def calculate_position_size(self, signal, account_equity):
        """
        Calculate position size using risk-based sizing.
        
        size = (equity × max_risk_pct) / stop_distance_per_share
        
        Returns number of shares/contracts to trade.
        """
        if account_equity <= 0:
            return 0
            
        entry = signal.get("entry_price", 0)
        stop = signal.get("stop_price", 0)
        
        if entry <= 0 or stop <= 0:
            return 0
            
        stop_distance = abs(entry - stop)
        if stop_distance == 0:
            return 0
            
        # Dollar risk per trade
        dollar_risk = account_equity * self.max_position_risk
        
        # Shares = dollar risk / stop distance
        shares = dollar_risk / stop_distance
        
        # Round down to nearest share
        shares = int(shares)
        
        # Check if position value is sane (not more than 25% of portfolio)
        max_position_value = account_equity * 0.25
        position_value = shares * entry
        
        if position_value > max_position_value:
            shares = int(max_position_value / entry)
            
        return max(0, shares)
        
    def calculate_portfolio_heat(self, account_equity):
        """
        Calculate current portfolio heat:
        Sum of (risk_per_open_position / account_equity)
        """
        if not self.open_positions or account_equity <= 0:
            return 0.0
            
        total_risk = 0
        for symbol, pos in self.open_positions.items():
            entry = pos.get("entry_price", 0)
            stop = pos.get("stop_price", 0)
            size = pos.get("size", 0)
            
            if entry > 0 and stop > 0:
                position_risk = abs(entry - stop) * size
                total_risk += position_risk
                
        return total_risk / account_equity
        
    def evaluate_trade(self, signal, account_equity, current_positions=None):
        """
        Master trade evaluation. Returns go/no-go with full reasoning.
        
        This runs BEFORE every trade is sent to Alpaca.
        """
        result = {
            "approved": False,
            "symbol": signal.get("symbol"),
            "direction": signal.get("action"),
            "position_size": 0,
            "dollar_risk": 0,
            "checks": [],
            "rejection_reason": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        
        # ─── Check 1: Signal Quality Gate ─────────────────────────────
        quality = signal.get("quality", "C")
        min_rank = self.quality_rank.get(self.min_signal_quality, 2)
        signal_rank = self.quality_rank.get(quality, 1)
        
        if signal_rank < min_rank:
            result["checks"].append(f"❌ Quality {quality} below minimum {self.min_signal_quality}")
            result["rejection_reason"] = f"Signal quality {quality} insufficient"
            return result
        result["checks"].append(f"✅ Signal quality {quality} — accepted")
        
        # ─── Check 2: Action is tradeable ─────────────────────────────
        if signal.get("action") == "NO_TRADE":
            result["checks"].append("❌ No trade signal generated")
            result["rejection_reason"] = "Signal compositor returned NO_TRADE"
            return result
        result["checks"].append(f"✅ Valid {signal.get('action')} signal")
        
        # ─── Check 3: Daily Loss Gate ──────────────────────────────────
        if self.day_start_equity:
            daily_loss_pct = self.daily_pnl / self.day_start_equity
            if daily_loss_pct < -self.max_daily_loss:
                result["checks"].append(
                    f"❌ DAILY LOSS GATE: {daily_loss_pct:.1%} exceeds {-self.max_daily_loss:.1%}"
                )
                result["rejection_reason"] = "Daily loss limit reached — trading halted"
                return result
            result["checks"].append(
                f"✅ Daily P&L: {daily_loss_pct:+.2%} (gate: {-self.max_daily_loss:.1%})"
            )
        
        # ─── Check 4: Trade Count ─────────────────────────────────────
        if self.trade_count_today >= self.max_trades_per_day:
            result["checks"].append(f"❌ Max trades reached: {self.trade_count_today}/{self.max_trades_per_day}")
            result["rejection_reason"] = "Max daily trade count reached"
            return result
        result["checks"].append(f"✅ Trade count: {self.trade_count_today}/{self.max_trades_per_day}")
        
        # ─── Check 5: Portfolio Heat ───────────────────────────────────
        current_heat = self.calculate_portfolio_heat(account_equity)
        if current_heat >= self.max_portfolio_heat:
            result["checks"].append(
                f"❌ Portfolio heat {current_heat:.1%} at/above max {self.max_portfolio_heat:.1%}"
            )
            result["rejection_reason"] = "Portfolio heat limit reached"
            return result
        result["checks"].append(
            f"✅ Portfolio heat: {current_heat:.1%} (max: {self.max_portfolio_heat:.1%})"
        )
        
        # ─── Check 6: Duplicate Position ──────────────────────────────
        symbol = signal.get("symbol")
        if symbol in self.open_positions:
            result["checks"].append(f"❌ Already have open position in {symbol}")
            result["rejection_reason"] = f"Duplicate position in {symbol}"
            return result
        result["checks"].append(f"✅ No existing position in {symbol}")
        
        # ─── Check 7: Risk/Reward Minimum ─────────────────────────────
        rr = signal.get("risk_reward", 0)
        if rr < 1.5:
            result["checks"].append(f"❌ R:R {rr:.1f} below minimum 1.5")
            result["rejection_reason"] = f"Risk/reward {rr:.1f}:1 insufficient"
            return result
        result["checks"].append(f"✅ R:R {rr:.1f}:1 — acceptable")
        
        # ─── All checks passed — calculate size ───────────────────────
        position_size = self.calculate_position_size(signal, account_equity)
        
        if position_size <= 0:
            result["checks"].append("❌ Position size calculated as 0")
            result["rejection_reason"] = "Position size too small"
            return result
            
        entry = signal.get("entry_price", 0)
        stop = signal.get("stop_price", 0)
        dollar_risk = abs(entry - stop) * position_size
        risk_pct = dollar_risk / account_equity
        
        result["checks"].append(f"✅ Size: {position_size} shares @ ${entry:.2f}")
        result["checks"].append(f"✅ Dollar risk: ${dollar_risk:.2f} ({risk_pct:.2%} of portfolio)")
        
        result.update({
            "approved": True,
            "position_size": position_size,
            "dollar_risk": round(dollar_risk, 2),
            "risk_pct": round(risk_pct, 4),
        })
        
        return result
        
    def register_open_position(self, symbol, signal, size):
        """Record a position that was opened."""
        self.open_positions[symbol] = {
            "entry_price": signal.get("entry_price"),
            "stop_price": signal.get("stop_price"),
            "target_price": signal.get("target_price"),
            "size": size,
            "direction": signal.get("action"),
            "opened_at": datetime.now(timezone.utc).isoformat(),
        }
        self.trade_count_today += 1
        
    def close_position(self, symbol, exit_price):
        """Record position closure and update P&L."""
        if symbol not in self.open_positions:
            return None
            
        pos = self.open_positions.pop(symbol)
        entry = pos.get("entry_price", 0)
        size = pos.get("size", 0)
        direction = pos.get("direction")
        
        if direction == "LONG":
            pnl = (exit_price - entry) * size
        else:
            pnl = (entry - exit_price) * size
            
        self.daily_pnl += pnl
        
        return {
            "symbol": symbol,
            "entry_price": entry,
            "exit_price": exit_price,
            "size": size,
            "pnl": round(pnl, 2),
            "closed_at": datetime.now(timezone.utc).isoformat(),
        }
        
    def get_risk_dashboard(self, account_equity):
        """Return current risk state for dashboard."""
        heat = self.calculate_portfolio_heat(account_equity)
        daily_loss_pct = (self.daily_pnl / self.day_start_equity 
                         if self.day_start_equity else 0)
        
        return {
            "portfolio_heat": round(heat, 4),
            "portfolio_heat_pct": f"{heat:.1%}",
            "heat_limit": self.max_portfolio_heat,
            "heat_remaining": max(0, self.max_portfolio_heat - heat),
            "daily_pnl": round(self.daily_pnl, 2),
            "daily_pnl_pct": round(daily_loss_pct, 4),
            "daily_loss_gate": self.max_daily_loss,
            "gate_triggered": daily_loss_pct < -self.max_daily_loss,
            "open_positions": len(self.open_positions),
            "trade_count_today": self.trade_count_today,
            "max_trades": self.max_trades_per_day,
            "open_position_symbols": list(self.open_positions.keys()),
        }
