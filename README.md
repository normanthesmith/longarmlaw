# ATLAS — Adaptive Trading & Liquidity Analysis System

> Multi-asset intraday trading platform. Alpaca paper trading. Railway deployment.
> Built on 6 layers of market knowledge: Microstructure → Probability → Behaviour → Risk → Macro → Technical.

---

## Quick Start

### 1. Clone & Configure

```bash
git clone <your-repo>
cd atlas
cp .env.example .env
```

Edit `.env`:
```
ALPACA_API_KEY=your_key
ALPACA_SECRET_KEY=your_secret
ALPACA_BASE_URL=https://paper-api.alpaca.markets
ALPHA_VANTAGE_KEY=your_key
FRED_API_KEY=your_key         # Free at fred.stlouisfed.org
ACCOUNT_SIZE=25000
```

### 2. Run Locally

```bash
pip install -r requirements.txt
python app.py
```

Open `http://localhost:5000`

### 3. Deploy to Railway

1. Push to GitHub
2. New Railway project → Deploy from GitHub repo
3. Add environment variables in Railway dashboard
4. Railway auto-detects Python and uses `Procfile`

---

## Architecture

```
ATLAS/
├── app.py                          # Flask API server (20+ endpoints)
├── backend/
│   ├── atlas_engine.py             # Master orchestrator
│   ├── trader_profile.py           # Personality parameters
│   └── engine/
│       ├── macro_regime.py         # Layer 5: Yield curve, VIX, credit
│       ├── market_structure.py     # Layer 6: VWAP, swing points, trend
│       ├── signal_compositor.py    # Confluence engine (5 factors)
│       └── atr_calculator.py      # ATR stops, targets, thesis progress
│   ├── risk/
│   │   └── risk_manager.py        # Position sizing, drawdown gates
│   └── execution/
│       └── alpaca_executor.py     # Alpaca paper trading integration
└── frontend/
    └── index.html                  # Dashboard (dark institutional UI)
```

---

## Trader Profile — "The Patient Architect"

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Risk per trade | 1% ($250) | Kelly-appropriate for $25k |
| Max portfolio heat | 5% ($1,250) | 5 positions maximum |
| Daily loss gate | 4% ($1,000) | Hard stop — sacred |
| Min confluence | 4.0/5.0 | Patient — only best setups |
| Min signal quality | A | No B trades |
| Min R:R | 2.5:1 | Wide stops, big targets |
| Stop placement | 1.5× ATR | Beyond market noise |
| Target | 4× ATR | ~2.7R from entry |
| Trailing stop | Yes (at 1.5R) | Let winners run |
| Macro gate | STRICT | No longs in risk-off |
| Auto-trade | Yes | Fully automated |
| Override stops | NEVER | Sacred — no exceptions |

### Circuit Breakers (Emotional Protection)
- After 2 consecutive losses → position size drops to 50%
- After 3 consecutive losses → system PAUSES, requires manual restart
- After any stop hit → 15-minute cooldown on that symbol
- After very fast entry post-loss → 10-minute delay (revenge trade protection)

---

## API Endpoints

### Engine Control
- `POST /api/engine/start` — Start background scan loop
- `POST /api/engine/stop` — Stop engine
- `POST /api/engine/scan` — Trigger immediate scan
- `POST /api/engine/auto-trading` — `{"enabled": true/false}`
- `POST /api/engine/resume` — Reset circuit breaker

### Data
- `GET /api/state` — Full dashboard state
- `GET /api/account` — Alpaca account info
- `GET /api/macro` — Current macro regime
- `GET /api/signals` — All active signals
- `GET /api/analysis/<SYMBOL>` — Structure analysis for symbol
- `GET /api/positions` — Open positions
- `GET /api/orders` — Recent orders
- `GET /api/risk` — Risk dashboard
- `GET /api/logs` — System log

### Trading
- `POST /api/trade/execute` — `{"symbol": "SPY", "direction": "LONG"}`
- `POST /api/trade/close/<SYMBOL>` — Close open position

### Watchlist
- `GET /api/watchlist` — Current symbols
- `POST /api/watchlist/add` — `{"symbol": "AAPL"}`
- `POST /api/watchlist/remove` — `{"symbol": "AAPL"}`

### Profile
- `GET /api/profile` — Active trader profile
- `POST /api/profile/update` — `{"param": "min_risk_reward", "value": 3.0}`
- `POST /api/profile/switch` — `{"preset": "conservative_swing"}`

---

## Signal Logic — 5 Factor Confluence

All 5 factors must score ≥ 4.0/5.0 combined:

1. **Macro Regime** (1.0) — Fed rate cycle, VIX, credit spreads, SPY momentum
2. **Market Structure** (1.0) — HH/HL trend confirmation (Wyckoff-based)
3. **VWAP Positioning** (1.0) — Price at or near VWAP, not extended
4. **Volume Confirmation** (1.0) — Institutional participation visible
5. **RSI Momentum** (1.0) — Not overextended against trade direction

**Bonus:** Key level proximity (+0.5)

A signal only fires when the macro regime, the structure, the entry location, the volume, and the momentum ALL agree. This is the Patient Architect's edge — waiting for full confluence, not chasing single-factor noise.

---

## Data Sources

| Source | Used For | Cost |
|--------|----------|------|
| Alpaca | Real-time bars, execution | Free (paper) |
| Alpha Vantage | Quotes, macro ETFs | Free tier |
| FRED | Yield curve (T10Y2Y), VIX | Free |

---

## Asset Universe (Default Watchlist)

**Indices:** SPY, QQQ, IWM, DIA  
**Tech:** AAPL, NVDA, MSFT, TSLA  
**Macro ETFs:** GLD, SLV, USO, TLT  
**Volatility:** UVXY  
**Crypto:** BTCUSD, ETHUSD  

Add any Alpaca-supported symbol via the dashboard or API.

---

## Important Notes

1. **Paper trading only** — configured for `paper-api.alpaca.markets`
2. **$25,000 account** — above PDT threshold, no day trade limits
3. **Not financial advice** — this is a research and learning tool
4. **Validate before live** — run paper trading for minimum 3 months before considering live capital
