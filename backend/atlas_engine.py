"""
ATLAS - Main Trading Engine v2
Profile-aware. Wires TraderProfile through all modules.
"""

import os
import logging
import threading
import time
from datetime import datetime, timezone

from backend.engine.macro_regime import MacroRegimeDetector
from backend.engine.market_structure import MarketStructureEngine
from backend.engine.signal_compositor import SignalCompositor
from backend.engine.atr_calculator import ATRCalculator
from backend.risk.risk_manager import RiskManager
from backend.execution.alpaca_executor import AlpacaExecutor
from backend.trader_profile import TraderProfile, ProfileManager, PRESET_PROFILES

logger = logging.getLogger(__name__)


class ATLASEngine:
    def __init__(self):
        # Load trader profile first — governs everything
        self.profile_manager = ProfileManager()
        self.profile = self.profile_manager.get_profile()

        # Update account size from env if set
        env_size = os.getenv("ACCOUNT_SIZE")
        if env_size:
            self.profile.account_size = float(env_size)

        self.macro = MacroRegimeDetector()
        self.structure_engine = MarketStructureEngine()
        self.compositor = SignalCompositor(profile=self.profile)
        self.atr_calc = ATRCalculator()
        self.risk = RiskManager(
            max_position_risk=self.profile.risk_per_trade_pct,
            max_portfolio_heat=self.profile.max_portfolio_heat_pct,
            max_daily_loss=self.profile.max_daily_loss_pct,
            min_signal_quality=self.profile.min_signal_quality,
        )
        self.executor = AlpacaExecutor()

        self.watchlist = list(self.profile.default_watchlist)
        self.is_running = False
        self.scan_interval = 300
        self.last_scan = None
        self.last_macro_refresh = None
        self.macro_refresh_interval = 3600

        self.current_regime = None
        self.symbol_analyses = {}
        self.symbol_bars = {}
        self.active_signals = {}
        self.trade_log = []
        self.system_log = []
        self._auto_trading = False
        self._paused_by_circuit_breaker = False
        self._consecutive_losses = 0
        self._lock = threading.Lock()

        self._log(f"ATLAS Engine v2 initialised | Profile: {self.profile.name}")
        self._log(f"Account: ${self.profile.account_size:,.0f} | Risk/trade: {self.profile.risk_per_trade_pct*100:.1f}%")

    def _log(self, message, level="INFO"):
        entry = {"timestamp": datetime.now(timezone.utc).isoformat(),
                 "level": level, "message": message}
        self.system_log.append(entry)
        self.system_log = self.system_log[-500:]
        getattr(logger, level.lower(), logger.info)(message)

    def start(self):
        if self.is_running:
            return
        self.is_running = True
        thread = threading.Thread(target=self._run_loop, daemon=True)
        thread.start()
        self._log("Engine started")

    def stop(self):
        self.is_running = False
        self._log("Engine stopped")

    def _run_loop(self):
        while self.is_running:
            try:
                if not self._paused_by_circuit_breaker:
                    self._scan_cycle()
            except Exception as e:
                self._log(f"Scan cycle error: {e}", "ERROR")
            time.sleep(self.scan_interval)

    def _scan_cycle(self):
        now = datetime.now(timezone.utc)
        self._log(f"Scan cycle at {now.strftime('%H:%M:%S')} UTC")

        account = self.executor.get_account()
        equity = account.get("equity", self.profile.account_size)

        if self.risk.day_start_equity is None:
            self.risk.set_day_start_equity(equity)

        # Refresh macro
        force_macro = (self.last_macro_refresh is None or
                       (now - self.last_macro_refresh).total_seconds() > self.macro_refresh_interval)
        if force_macro:
            self.current_regime = self.macro.detect_regime(force_refresh=True)
            self.last_macro_refresh = now
            self._log(f"Regime: {self.current_regime.get('regime_label')} | Bias: {self.current_regime.get('bias')}")

        for symbol in self.watchlist:
            try:
                self._analyse_symbol(symbol, equity)
            except Exception as e:
                self._log(f"Error analysing {symbol}: {e}", "ERROR")

        self.last_scan = now
        actionable = sum(1 for s in self.active_signals.values() if s.get("action") != "NO_TRADE")
        self._log(f"Scan complete. {actionable} actionable signals from {len(self.watchlist)} symbols.")

    def _analyse_symbol(self, symbol, account_equity):
        bars = self.executor.get_bars(
            symbol,
            timeframe_minutes=self.profile.preferred_timeframe_minutes,
            limit=max(self.profile.min_bars_required, 80)
        )
        if bars.empty:
            return

        self.symbol_bars[symbol] = bars
        structure = self.structure_engine.analyse(symbol, bars)
        if "error" in structure:
            return

        self.symbol_analyses[symbol] = structure

        if self.current_regime is None:
            return

        signal = self.compositor.compose_signal(
            symbol, structure, self.current_regime, bars_df=bars
        )
        self.active_signals[symbol] = signal

        # Auto-execute if enabled and approved
        if (self._auto_trading and
                not self._paused_by_circuit_breaker and
                signal.get("action") not in [None, "NO_TRADE"]):
            self._evaluate_and_trade(symbol, signal, account_equity)

    def _evaluate_and_trade(self, symbol, signal, account_equity):
        # Circuit breaker check
        if self._consecutive_losses >= self.profile.consecutive_loss_pause_threshold:
            self._paused_by_circuit_breaker = True
            self._log(f"CIRCUIT BREAKER: {self._consecutive_losses} consecutive losses — PAUSED", "WARNING")
            return

        # Size multiplier from emotional circuit breaker
        size_mult = self.profile.get_size_multiplier(self._consecutive_losses)
        if size_mult < 1.0:
            self._log(f"Size reduced to {size_mult*100:.0f}% after {self._consecutive_losses} losses", "WARNING")

        risk_result = self.risk.evaluate_trade(signal, account_equity)

        if risk_result.get("approved"):
            # Apply size multiplier
            base_size = risk_result.get("position_size", 0)
            adjusted_size = max(1, int(base_size * size_mult))
            risk_result["position_size"] = adjusted_size

            self._log(
                f"TRADE: {signal['action']} {adjusted_size} {symbol} | "
                f"Entry: ${signal['entry_price']:.2f} Stop: ${signal['stop_price']:.2f} "
                f"Target: ${signal['target_price']:.2f} | R:R {signal['risk_reward']:.1f}:1 | {signal['quality']}"
            )

            order = self.executor.submit_market_order(
                symbol=symbol, qty=adjusted_size,
                side=signal["action"], risk_check_result=risk_result
            )

            if order:
                self.risk.register_open_position(symbol, signal, adjusted_size)
                self.trade_log.append({
                    "symbol": symbol, "signal": signal,
                    "risk_check": risk_result, "order": order,
                    "size_multiplier": size_mult,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
        else:
            self._log(f"Blocked {symbol}: {risk_result.get('rejection_reason')}", "WARNING")

    def record_trade_outcome(self, symbol, won: bool):
        """Call when a trade closes to update circuit breaker state."""
        if won:
            self._consecutive_losses = 0
            if self._paused_by_circuit_breaker:
                self._paused_by_circuit_breaker = False
                self._log("Circuit breaker RESET — trading resumed", "WARNING")
        else:
            self._consecutive_losses += 1
            self._log(f"Loss recorded. Consecutive losses: {self._consecutive_losses}", "WARNING")

    def manual_scan(self, symbol=None):
        account = self.executor.get_account()
        equity = account.get("equity", self.profile.account_size)

        if self.current_regime is None:
            self.current_regime = self.macro.detect_regime()
            self.last_macro_refresh = datetime.now(timezone.utc)

        targets = [symbol] if symbol else self.watchlist
        for sym in targets:
            try:
                self._analyse_symbol(sym, equity)
            except Exception as e:
                self._log(f"Manual scan error {sym}: {e}", "ERROR")

        return self.get_dashboard_state()

    def resume_from_circuit_breaker(self):
        """Manually resume after circuit breaker pause."""
        self._paused_by_circuit_breaker = False
        self._consecutive_losses = 0
        self._log("Circuit breaker manually reset — trading resumed", "WARNING")

    def set_auto_trading(self, enabled):
        self._auto_trading = enabled
        self._log(f"Auto-trading {'ENABLED' if enabled else 'DISABLED'}", "WARNING" if enabled else "INFO")

    def update_profile_parameter(self, param, value):
        """Update a single profile parameter and propagate."""
        success = self.profile_manager.update_parameter(param, value)
        if success:
            self.profile = self.profile_manager.get_profile()
            self.compositor.update_profile(self.profile)
            self._log(f"Profile updated: {param} = {value}")
        return success

    def switch_profile(self, preset_name):
        """Switch to a named preset profile."""
        try:
            self.profile = self.profile_manager.switch_preset(preset_name)
            self.compositor.update_profile(self.profile)
            self._log(f"Switched to profile: {self.profile.name}")
            return self.profile.to_dict()
        except ValueError as e:
            self._log(str(e), "ERROR")
            return None

    def add_to_watchlist(self, symbol):
        symbol = symbol.upper().strip()
        if symbol not in self.watchlist:
            self.watchlist.append(symbol)
            self._log(f"Added {symbol} to watchlist")

    def remove_from_watchlist(self, symbol):
        symbol = symbol.upper().strip()
        if symbol in self.watchlist:
            self.watchlist.remove(symbol)
            self._log(f"Removed {symbol} from watchlist")

    def get_thesis_progress(self, symbol):
        """Get trade thesis progress for open position."""
        pos = self.risk.open_positions.get(symbol)
        signal = self.active_signals.get(symbol)
        bars = self.symbol_bars.get(symbol)

        if not pos or not bars or bars.empty:
            return None

        current_price = float(bars["close"].iloc[-1])
        direction = pos.get("direction", "LONG")
        entry = pos.get("entry_price", current_price)
        stop = pos.get("stop_price", entry)
        target = pos.get("target_price", entry)

        return self.atr_calc.get_thesis_progress(
            direction=direction, entry_price=entry,
            stop_price=stop, target_price=target,
            current_price=current_price
        )

    def get_dashboard_state(self):
        account = self.executor.get_account()
        equity = account.get("equity", self.profile.account_size)
        positions = self.executor.get_positions()
        orders = self.executor.get_orders(status="all", limit=15)
        risk_dashboard = self.risk.get_risk_dashboard(equity)

        # Thesis progress for all open positions
        thesis_progress = {}
        for sym in self.risk.open_positions:
            progress = self.get_thesis_progress(sym)
            if progress:
                thesis_progress[sym] = progress

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "system": {
                "running": self.is_running,
                "auto_trading": self._auto_trading,
                "paused": self._paused_by_circuit_breaker,
                "consecutive_losses": self._consecutive_losses,
                "last_scan": self.last_scan.isoformat() if self.last_scan else None,
                "watchlist": self.watchlist,
                "connected": self.executor.is_connected(),
            },
            "account": account,
            "profile": self.profile.to_dict(),
            "macro_regime": self.current_regime,
            "risk": risk_dashboard,
            "symbol_analyses": self.symbol_analyses,
            "active_signals": self.active_signals,
            "thesis_progress": thesis_progress,
            "positions": positions,
            "orders": orders,
            "trade_log": self.trade_log[-15:],
            "system_log": self.system_log[-100:],
        }
