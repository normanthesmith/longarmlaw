"""
ATLAS - Trader Profile System
Encodes trader personality into system parameters.
The profile governs every decision the system makes.

"The Patient Architect" — high patience, sacred stops, 
maximise R:R, fully automated, trend-following.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional
import json
import os
import logging

logger = logging.getLogger(__name__)


@dataclass
class TraderProfile:
    """
    Complete trader personality encoding.
    Every parameter has a direct effect on system behaviour.
    """
    
    # ── Identity ──────────────────────────────────────────────
    name: str = "The Patient Architect"
    description: str = (
        "High-patience trend-follower. Waits for maximum confluence. "
        "Sacred stops, wide targets, fully automated. PDT-aware."
    )
    
    # ── Conviction Parameters ──────────────────────────────────
    # How selective the system is about signal quality
    min_confluence_score: float = 4.0      # Out of 5.0 — very selective
    min_signal_quality: str = "A"          # A+ or A only — no B trades
    min_risk_reward: float = 2.5           # Minimum 2.5:1 R:R required
    max_trades_per_day: int = 10           # No PDT constraint at $25k
    require_volume_confirmation: bool = True  # Volume must confirm entry
    
    # ── Risk Parameters ────────────────────────────────────────
    # Core position sizing and exposure limits
    risk_per_trade_pct: float = 0.01       # 1% of account per trade
    max_portfolio_heat_pct: float = 0.05   # 5% total open risk
    max_daily_loss_pct: float = 0.04       # 4% daily loss gate
    account_size: float = 25000.0          # Paper account size
    
    # ── Stop Loss Behaviour ────────────────────────────────────
    # Sacred stop — no overrides allowed
    stops_overridable: bool = False        # NEVER — sacred
    atr_stop_multiplier: float = 1.5       # Stop at 1.5× ATR beyond structure
    atr_period: int = 14                   # ATR calculation period
    
    # ── Target Behaviour ──────────────────────────────────────
    # Wide targets, let winners run
    atr_target_multiplier: float = 4.0     # Initial target 4× ATR (= ~2.7R)
    use_trailing_stop: bool = True         # Trail once profitable
    trailing_stop_activation_r: float = 1.5  # Activate trail at 1.5R profit
    trailing_stop_distance_atr: float = 1.0  # Trail at 1× ATR distance
    allow_averaging_down: bool = False      # NEVER
    
    # ── PDT Management ────────────────────────────────────────
    pdt_protected: bool = False             # Track and enforce PDT limits
    pdt_day_trade_limit: int = 3           # Max day trades per 5-day window
    prefer_overnight_holds: bool = False    # Prefer multi-session setups
    
    # ── Emotional Circuit Breakers ─────────────────────────────
    consecutive_loss_size_reduction: int = 2   # After 2 losses → 50% size
    consecutive_loss_pause_threshold: int = 3  # After 3 losses → PAUSE
    post_stop_cooldown_minutes: int = 15       # Cool down after stop hit
    revenge_trade_delay_minutes: int = 10      # Delay if trade < 2min after loss
    
    # ── Macro Regime Gate ──────────────────────────────────────
    macro_gate_strictness: str = "STRICT"  # STRICT | MODERATE | RELAXED
    # STRICT: no longs in risk-off, no shorts in risk-on
    # MODERATE: reduce size in opposing regime
    # RELAXED: macro context only, no hard gate
    
    # ── Signal Preferences ─────────────────────────────────────
    strategy_bias: str = "TREND_FOLLOWING"  # TREND_FOLLOWING | CONTRARIAN | MIXED
    preferred_timeframe_minutes: int = 5    # Entry timeframe
    context_timeframe_minutes: int = 60     # Higher timeframe context
    min_bars_required: int = 50            # Min data points before analysis
    
    # ── Dashboard Preferences ──────────────────────────────────
    # What the trader sees — de-emphasise dollar P&L
    show_dollar_pnl: bool = False          # Hidden — prevents emotional fixation
    show_r_multiple: bool = True           # Show R-multiple progress instead
    show_thesis_progress: bool = True      # Show entry→stop→target bar
    alert_on_signal: bool = True           # Desktop alert when signal fires
    
    # ── Asset Universe ─────────────────────────────────────────
    asset_classes: list = field(default_factory=lambda: [
        "equities", "etf", "crypto"
    ])
    
    # Default watchlist tuned for multi-asset, high-liquidity
    default_watchlist: list = field(default_factory=lambda: [
        # US indices / macro proxies
        "SPY", "QQQ", "IWM", "DIA",
        # High-volume tech
        "AAPL", "NVDA", "MSFT", "TSLA",
        # Macro/commodity ETFs
        "GLD", "SLV", "USO", "TLT",
        # Volatility
        "UVXY",
        # Crypto (Alpaca supports)
        "BTCUSD", "ETHUSD",
    ])

    def get_dollar_risk(self) -> float:
        """Maximum dollar risk per trade."""
        return self.account_size * self.risk_per_trade_pct

    def get_max_heat_dollars(self) -> float:
        """Maximum total open risk in dollars."""
        return self.account_size * self.max_portfolio_heat_pct

    def get_daily_loss_limit(self) -> float:
        """Daily loss limit in dollars."""
        return self.account_size * self.max_daily_loss_pct

    def allows_long(self, regime_bias: str) -> bool:
        """Check if macro regime allows long trades."""
        if self.macro_gate_strictness == "STRICT":
            return regime_bias in ["LONG", "LONG_SELECTIVE"]
        elif self.macro_gate_strictness == "MODERATE":
            return regime_bias not in ["DEFENSIVE"]
        else:
            return True

    def allows_short(self, regime_bias: str) -> bool:
        """Check if macro regime allows short trades."""
        if self.macro_gate_strictness == "STRICT":
            return regime_bias in ["DEFENSIVE", "REDUCED"]
        elif self.macro_gate_strictness == "MODERATE":
            return regime_bias not in ["LONG"]
        else:
            return True

    def get_size_multiplier(self, consecutive_losses: int) -> float:
        """
        Emotional circuit breaker: reduce size after losses.
        After 2 losses → 50%. After 3 → system paused.
        """
        if consecutive_losses >= self.consecutive_loss_size_reduction:
            return 0.5
        return 1.0

    def should_pause(self, consecutive_losses: int) -> bool:
        """Check if system should pause due to loss streak."""
        return consecutive_losses >= self.consecutive_loss_pause_threshold

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        """Human-readable summary of profile."""
        return f"""
╔══════════════════════════════════════════════╗
║  TRADER PROFILE: {self.name:<27} ║
╠══════════════════════════════════════════════╣
║  Account Size:     ${self.account_size:>10,.2f}              ║
║  Risk Per Trade:   {self.risk_per_trade_pct*100:.1f}%                        ║
║  Max Daily Loss:   ${self.get_daily_loss_limit():>10,.2f}              ║
║  Min R:R:          {self.min_risk_reward:.1f}:1                         ║
║  Confluence Req:   {self.min_confluence_score:.1f}/5.0                     ║
║  Min Quality:      {self.min_signal_quality:<5}                        ║
║  Stops Sacred:     {'YES — NO OVERRIDE':<28} ║
║  PDT Protected:    {'YES — MAX 3/5 days':<28} ║
║  Macro Gate:       {self.macro_gate_strictness:<28} ║
║  Strategy:         {self.strategy_bias:<28} ║
╚══════════════════════════════════════════════╝
""".strip()


# ── Preset Profiles ────────────────────────────────────────────────────────

def patient_architect() -> TraderProfile:
    """The default profile — high patience, sacred stops, wide R:R."""
    return TraderProfile()


def aggressive_scalper() -> TraderProfile:
    """High-frequency, tight stops, small targets, high win rate."""
    return TraderProfile(
        name="Aggressive Scalper",
        description="High-frequency intraday. Tight stops, small targets, high win rate.",
        min_confluence_score=3.0,
        min_signal_quality="B",
        min_risk_reward=1.5,
        max_trades_per_day=10,
        risk_per_trade_pct=0.005,
        max_portfolio_heat_pct=0.04,
        atr_stop_multiplier=0.8,
        atr_target_multiplier=1.5,
        use_trailing_stop=False,
        prefer_overnight_holds=False,
        macro_gate_strictness="RELAXED",
        strategy_bias="MIXED",
        preferred_timeframe_minutes=1,
    )


def conservative_swing() -> TraderProfile:
    """Low frequency, high quality, multi-day holds."""
    return TraderProfile(
        name="Conservative Swing",
        description="Low frequency, maximum quality, multi-day structural trades.",
        min_confluence_score=4.5,
        min_signal_quality="A+",
        min_risk_reward=3.0,
        max_trades_per_day=2,
        risk_per_trade_pct=0.005,
        max_portfolio_heat_pct=0.03,
        max_daily_loss_pct=0.02,
        atr_stop_multiplier=2.0,
        atr_target_multiplier=6.0,
        trailing_stop_activation_r=2.0,
        prefer_overnight_holds=True,
        macro_gate_strictness="STRICT",
        preferred_timeframe_minutes=60,
    )


PRESET_PROFILES = {
    "patient_architect": patient_architect,
    "aggressive_scalper": aggressive_scalper,
    "conservative_swing": conservative_swing,
}


class ProfileManager:
    """Manages trader profiles — load, save, switch."""

    def __init__(self, profile_path: str = "trader_profile.json"):
        self.profile_path = profile_path
        self.active_profile = patient_architect()
        self._load_saved_profile()

    def _load_saved_profile(self):
        """Load saved profile from disk if it exists."""
        if os.path.exists(self.profile_path):
            try:
                with open(self.profile_path, "r") as f:
                    data = json.load(f)
                # Reconstruct profile from saved data
                self.active_profile = TraderProfile(**{
                    k: v for k, v in data.items()
                    if k in TraderProfile.__dataclass_fields__
                })
                logger.info(f"Loaded profile: {self.active_profile.name}")
            except Exception as e:
                logger.warning(f"Could not load profile: {e} — using default")

    def save_profile(self):
        """Persist active profile to disk."""
        try:
            with open(self.profile_path, "w") as f:
                json.dump(self.active_profile.to_dict(), f, indent=2)
            logger.info(f"Profile saved: {self.active_profile.name}")
        except Exception as e:
            logger.error(f"Could not save profile: {e}")

    def switch_preset(self, preset_name: str) -> TraderProfile:
        """Switch to a named preset profile."""
        if preset_name in PRESET_PROFILES:
            # Preserve account size
            old_account_size = self.active_profile.account_size
            self.active_profile = PRESET_PROFILES[preset_name]()
            self.active_profile.account_size = old_account_size
            self.save_profile()
            logger.info(f"Switched to profile: {preset_name}")
            return self.active_profile
        raise ValueError(f"Unknown preset: {preset_name}")

    def update_parameter(self, param: str, value) -> bool:
        """Update a single profile parameter."""
        if hasattr(self.active_profile, param):
            setattr(self.active_profile, param, value)
            self.save_profile()
            logger.info(f"Profile updated: {param} = {value}")
            return True
        return False

    def get_profile(self) -> TraderProfile:
        return self.active_profile

    def get_profile_dict(self) -> dict:
        return self.active_profile.to_dict()

    def list_presets(self) -> list:
        return list(PRESET_PROFILES.keys())
