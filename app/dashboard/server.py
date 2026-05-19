"""
Crypto Trader — Flask dashboard (stub).
Provides /health endpoint and a basic status page.
Full dashboard (Step 6) will replace this.
"""
import os
from datetime import datetime

from flask import Flask, jsonify

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret")

START_TIME = datetime.utcnow()


@app.route("/health")
def health():
    """Health check endpoint — used by Docker healthcheck."""
    return jsonify({"status": "ok", "uptime_seconds": (datetime.utcnow() - START_TIME).seconds})


@app.route("/")
def index():
    """Temporary status page until full dashboard is built."""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Crypto Trader</title>
        <style>
            body { font-family: monospace; background: #0f1117; color: #00ff88;
                   display: flex; align-items: center; justify-content: center;
                   height: 100vh; margin: 0; }
            .box { text-align: center; border: 1px solid #00ff88; padding: 40px 60px; }
            h1 { font-size: 2em; margin-bottom: 10px; }
            p  { color: #aaa; }
            a  { color: #00ff88; }
        </style>
    </head>
    <body>
        <div class="box">
            <h1>🤖 Crypto Trader</h1>
            <p>Container is running. Data collectors and AI engine are active.</p>
            <p>Full dashboard coming in Step 6.</p>
            <br>
            <p><a href="/health">/health</a> &nbsp;|&nbsp;
               <a href="/status">/status</a></p>
        </div>
    </body>
    </html>
    """


@app.route("/status")
def status():
    """Basic system status — DB connectivity, env vars set."""
    checks = {}

    # Check DB
    try:
        from app.database import get_engine
        with get_engine().connect() as conn:
            conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"

    # Check AI provider config
    checks["ai_provider"] = os.environ.get("AI_PROVIDER", "not set")
    checks["groq_key_set"] = bool(os.environ.get("GROQ_API_KEY", ""))
    checks["coinbase_key_set"] = bool(os.environ.get("COINBASE_API_KEY", ""))
    checks["watchlist"] = os.environ.get("WATCHLIST", "not set")

    return jsonify(checks)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
