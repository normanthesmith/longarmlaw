"""
ATLAS - Flask API Server
Serves the dashboard and exposes REST endpoints for all engine functions.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logging
from flask import Flask, send_from_directory, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder="frontend", static_url_path="")
CORS(app, origins="*")

# Initialise engine
from backend.atlas_engine import ATLASEngine
engine = ATLASEngine()


# ─── Dashboard ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return app.send_static_file("index.html")


# ─── Engine State ────────────────────────────────────────────────────────────

@app.route("/api/state", methods=["GET"])
def get_state():
    """Full dashboard state."""
    return jsonify(engine.get_dashboard_state())


@app.route("/api/account", methods=["GET"])
def get_account():
    return jsonify(engine.executor.get_account())


@app.route("/api/macro", methods=["GET"])
def get_macro():
    """Current macro regime."""
    force = request.args.get("refresh", "false").lower() == "true"
    regime = engine.macro.detect_regime(force_refresh=force)
    return jsonify(regime)


@app.route("/api/positions", methods=["GET"])
def get_positions():
    return jsonify(engine.executor.get_positions())


@app.route("/api/orders", methods=["GET"])
def get_orders():
    status = request.args.get("status", "all")
    return jsonify(engine.executor.get_orders(status=status))


@app.route("/api/signals", methods=["GET"])
def get_signals():
    """Active signals for all watched symbols."""
    return jsonify(engine.active_signals)


@app.route("/api/analysis/<symbol>", methods=["GET"])
def get_analysis(symbol):
    """Structure analysis for a specific symbol."""
    symbol = symbol.upper()
    if symbol in engine.symbol_analyses:
        return jsonify(engine.symbol_analyses[symbol])
    return jsonify({"error": f"No analysis available for {symbol}. Run a scan first."}), 404


@app.route("/api/risk", methods=["GET"])
def get_risk():
    account = engine.executor.get_account()
    equity = account.get("equity", 100000)
    return jsonify(engine.risk.get_risk_dashboard(equity))


@app.route("/api/logs", methods=["GET"])
def get_logs():
    limit = int(request.args.get("limit", 50))
    return jsonify(engine.system_log[-limit:])


# ─── Engine Control ──────────────────────────────────────────────────────────

@app.route("/api/engine/start", methods=["POST"])
def start_engine():
    engine.start()
    return jsonify({"status": "started"})


@app.route("/api/engine/stop", methods=["POST"])
def stop_engine():
    engine.stop()
    return jsonify({"status": "stopped"})


@app.route("/api/engine/scan", methods=["POST"])
def manual_scan():
    """Trigger immediate scan."""
    data = request.get_json(silent=True) or {}
    symbol = data.get("symbol")
    result = engine.manual_scan(symbol=symbol)
    return jsonify({"status": "scan_complete", "signals": engine.active_signals})


@app.route("/api/engine/auto-trading", methods=["POST"])
def toggle_auto_trading():
    data = request.get_json(silent=True) or {}
    enabled = data.get("enabled", False)
    engine.set_auto_trading(enabled)
    return jsonify({"auto_trading": enabled})


# ─── Watchlist Management ────────────────────────────────────────────────────

@app.route("/api/watchlist", methods=["GET"])
def get_watchlist():
    return jsonify({"watchlist": engine.watchlist})


@app.route("/api/watchlist/add", methods=["POST"])
def add_to_watchlist():
    data = request.get_json(silent=True) or {}
    symbol = data.get("symbol", "").upper().strip()
    if not symbol:
        return jsonify({"error": "Symbol required"}), 400
    engine.add_to_watchlist(symbol)
    return jsonify({"watchlist": engine.watchlist})


@app.route("/api/watchlist/remove", methods=["POST"])
def remove_from_watchlist():
    data = request.get_json(silent=True) or {}
    symbol = data.get("symbol", "").upper().strip()
    engine.remove_from_watchlist(symbol)
    return jsonify({"watchlist": engine.watchlist})


# ─── Trade Management ────────────────────────────────────────────────────────

@app.route("/api/trade/execute", methods=["POST"])
def execute_trade():
    """Manual trade execution with full risk check."""
    data = request.get_json(silent=True) or {}
    symbol = data.get("symbol", "").upper()
    direction = data.get("direction", "LONG").upper()

    if symbol not in engine.active_signals:
        return jsonify({"error": f"No signal for {symbol} — run scan first"}), 400

    signal = engine.active_signals[symbol]
    if signal.get("action") == "NO_TRADE":
        return jsonify({"error": "Signal is NO_TRADE — insufficient confluence"}), 400

    account = engine.executor.get_account()
    equity = account.get("equity", 100000)
    risk_result = engine.risk.evaluate_trade(signal, equity)

    if not risk_result.get("approved"):
        return jsonify({
            "approved": False,
            "reason": risk_result.get("rejection_reason"),
            "checks": risk_result.get("checks"),
        }), 200

    size = risk_result.get("position_size", 0)
    order = engine.executor.submit_market_order(
        symbol=symbol,
        qty=size,
        side=direction,
        risk_check_result=risk_result,
    )

    if order:
        engine.risk.register_open_position(symbol, signal, size)

    return jsonify({
        "approved": True,
        "order": order,
        "risk_check": risk_result,
        "signal": signal,
    })


@app.route("/api/trade/close/<symbol>", methods=["POST"])
def close_trade(symbol):
    """Close an open position."""
    symbol = symbol.upper()
    result = engine.executor.close_position(symbol)

    # Get current price for P&L tracking
    bars = engine.executor.get_bars(symbol, limit=1)
    if not bars.empty:
        exit_price = float(bars["close"].iloc[-1])
        pnl = engine.risk.close_position(symbol, exit_price)
        return jsonify({"status": "closed", "pnl": pnl, "order": result})

    return jsonify({"status": "closed", "order": result})


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "connected": engine.executor.is_connected(),
        "running": engine.is_running,
        "timestamp": __import__("datetime").datetime.now().isoformat(),
    })


if __name__ == "__main__":
    port = int(os.getenv("FLASK_PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"

    logger.info(f"ATLAS starting on port {port}")
    engine.start()

    app.run(host="0.0.0.0", port=port, debug=debug, use_reloader=False)


# ─── Profile Management ──────────────────────────────────────────────────────

@app.route("/api/profile", methods=["GET"])
def get_profile():
    return jsonify(engine.profile_manager.get_profile_dict())


@app.route("/api/profile/update", methods=["POST"])
def update_profile():
    data = request.get_json(silent=True) or {}
    param = data.get("param")
    value = data.get("value")
    if not param:
        return jsonify({"error": "param required"}), 400
    success = engine.update_profile_parameter(param, value)
    return jsonify({"success": success, "profile": engine.profile.to_dict()})


@app.route("/api/profile/switch", methods=["POST"])
def switch_profile():
    data = request.get_json(silent=True) or {}
    preset = data.get("preset", "patient_architect")
    result = engine.switch_profile(preset)
    if result:
        return jsonify({"success": True, "profile": result})
    return jsonify({"error": "Unknown preset"}), 400


@app.route("/api/profile/presets", methods=["GET"])
def list_presets():
    return jsonify({"presets": engine.profile_manager.list_presets()})


@app.route("/api/engine/resume", methods=["POST"])
def resume_engine():
    engine.resume_from_circuit_breaker()
    return jsonify({"status": "resumed"})


@app.route("/api/thesis/<symbol>", methods=["GET"])
def get_thesis(symbol):
    progress = engine.get_thesis_progress(symbol.upper())
    if progress:
        return jsonify(progress)
    return jsonify({"error": "No open position or data"}), 404
