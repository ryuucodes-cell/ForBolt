"""
Lightweight Flask API server for the Telegram Mini App (web frontend).

Serves read-only shop data and purchase/deposit actions from the same
SQLite database the bot uses. Started in a background thread by bot.py.
"""

import logging
from flask import Flask, jsonify, request
from flask_cors import CORS
import db

log = logging.getLogger("api")

app = Flask(__name__)
CORS(app)


@app.route("/api/config")
def api_config():
    return jsonify(db.cfg_all())


@app.route("/api/user/<int:tg_id>")
def api_user(tg_id):
    db.ensure_user(tg_id, request.args.get("username", ""))
    u = db.get_user(tg_id)
    if not u:
        return jsonify({"mmk_balance": 0, "total_spent": 0, "bonus_earned": 0, "is_reseller": 0})
    return jsonify({
        "mmk_balance": u["mmk_balance"],
        "total_spent": u["total_spent"],
        "bonus_earned": u["bonus_earned"],
        "is_reseller": u["is_reseller"],
    })


@app.route("/api/products")
def api_products():
    products = db.list_products(active_only=True)
    result = []
    for p in products:
        result.append({
            "product_id": p["product_id"],
            "name": p["name"],
            "category": p["category"],
            "description": p["description"],
            "price_mmk": p["price_mmk"],
            "discount_pct": p["discount_pct"],
            "discount_until": p["discount_until"],
            "fulfillment": p["fulfillment"],
            "emoji": p["emoji"],
            "emoji_id": p["emoji_id"],
            "stock": db.stock_count(p["product_id"]),
            "effective_price": db.get_effective_price(p),
        })
    return jsonify(result)


@app.route("/api/orders/<int:tg_id>")
def api_orders(tg_id):
    orders = db.list_orders(limit=50, telegram_id=tg_id)
    return jsonify([{
        "order_id": o["order_id"],
        "product_name": o["product_name"],
        "amount_mmk": o["amount_mmk"],
        "credential": o["credential"],
        "status": o["status"],
        "created_at": o["created_at"],
    } for o in orders])


@app.route("/api/deposits/<int:tg_id>")
def api_deposits(tg_id):
    deps = db.list_deposits(limit=50, telegram_id=tg_id)
    return jsonify([{
        "deposit_id": d["deposit_id"],
        "amount_mmk": d["amount_mmk"],
        "method": d["method"],
        "status": d["status"],
        "created_at": d["created_at"],
        "note": d["note"],
    } for d in deps])


@app.route("/api/giveaways")
def api_giveaways():
    gws = db.list_giveaways(limit=20)
    return jsonify([{
        "giveaway_id": g["giveaway_id"],
        "title": g["title"],
        "description": g["description"],
        "winner_slots": g["winner_slots"],
        "end_time": g["end_time"],
        "status": g["status"],
        "created_at": g["created_at"],
    } for g in gws])


@app.route("/api/ld_rounds")
def api_ld_rounds():
    rounds = db.list_ld_rounds(limit=20)
    result = []
    for r in rounds:
        entries = db.ld_entries(r["round_id"])
        result.append({
            "round_id": r["round_id"],
            "product_name": r["product_name"],
            "prize_mode": r["prize_mode"],
            "entry_price": r["entry_price"],
            "total_slots": r["total_slots"],
            "deadline": r["deadline"],
            "status": r["status"],
            "seed_hash": r["seed_hash"],
            "winner_id": r["winner_id"],
            "winner_slot": r["winner_slot"],
            "entries": [{"slot_no": e["slot_no"], "telegram_id": e["telegram_id"]} for e in entries],
        })
    return jsonify(result)


@app.route("/api/purchase", methods=["POST"])
def api_purchase():
    data = request.get_json(force=True)
    tg_id = data.get("telegram_id")
    username = data.get("username", "")
    product_id = data.get("product_id")
    qty = data.get("qty", 1)

    if not tg_id or not product_id:
        return jsonify({"error": "missing parameters"}), 400

    db.ensure_user(tg_id, username)
    product = db.get_product(product_id)
    if not product or not product["active"]:
        return jsonify({"error": "product not available"}), 400

    if db.get_fulfillment(product) == "preorder":
        total = db.get_effective_price(product) * qty
        oid = db.create_preorder_atomic(tg_id, username, product_id, product["name"], total)
        if oid is None:
            return jsonify({"error": "insufficient balance"})
        return jsonify({"ok": True, "order_id": oid, "charged": total})

    unit_price = db.get_effective_price(product)
    result = db.purchase_atomic(product_id, tg_id, username, unit_price, qty, product["name"])
    return jsonify(result)


@app.route("/api/ld_buy_slot", methods=["POST"])
def api_ld_buy_slot():
    data = request.get_json(force=True)
    tg_id = data.get("telegram_id")
    username = data.get("username", "")
    round_id = data.get("round_id")

    if not tg_id or not round_id:
        return jsonify({"error": "missing parameters"}), 400

    db.ensure_user(tg_id, username)
    result = db.ld_buy_slot(round_id, tg_id, username)
    return jsonify(result)


def start_api_server(host="0.0.0.0", port=5000):
    """Start the Flask server. Call from a background thread in bot.py."""
    log.info(f"Starting API server on {host}:{port}")
    app.run(host=host, port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    db.init_db()
    start_api_server()
