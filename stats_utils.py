"""
Calculs du tableau de bord "Statistiques" : chiffre d'affaires par période,
plats les plus vendus, répartition des commandes par heure.
"""
from collections import Counter
from datetime import datetime, timedelta

from models import db, Order, OrderItem


def _period_bounds(now=None):
    now = now or datetime.utcnow()
    today_start = datetime(now.year, now.month, now.day)
    week_start = today_start - timedelta(days=today_start.weekday())
    month_start = datetime(now.year, now.month, 1)
    return today_start, week_start, month_start


def _period_stats(restaurant_id, start):
    orders = Order.query.filter(
        Order.restaurant_id == restaurant_id,
        Order.status != "annulee",
        Order.created_at >= start,
    ).all()
    revenue = sum(o.total for o in orders)
    count = len(orders)
    avg = (revenue / count) if count else 0
    return {"revenue": revenue, "count": count, "avg": round(avg)}


def top_selling_items(restaurant_id, days=30, limit=5):
    since = datetime.utcnow() - timedelta(days=days)
    rows = (
        db.session.query(OrderItem.item_name, db.func.sum(OrderItem.quantity))
        .join(Order, Order.id == OrderItem.order_id)
        .filter(
            Order.restaurant_id == restaurant_id,
            Order.status != "annulee",
            Order.created_at >= since,
        )
        .group_by(OrderItem.item_name)
        .order_by(db.func.sum(OrderItem.quantity).desc())
        .limit(limit)
        .all()
    )
    return [{"name": name, "quantity": int(qty)} for name, qty in rows]


def peak_hours(restaurant_id, days=30):
    since = datetime.utcnow() - timedelta(days=days)
    orders = Order.query.filter(
        Order.restaurant_id == restaurant_id,
        Order.status != "annulee",
        Order.created_at >= since,
    ).all()
    counter = Counter(o.created_at.hour for o in orders)
    return [{"hour": h, "count": counter.get(h, 0)} for h in range(24)]


def build_dashboard_stats(restaurant_id, now=None):
    today_start, week_start, month_start = _period_bounds(now)
    return {
        "today": _period_stats(restaurant_id, today_start),
        "week": _period_stats(restaurant_id, week_start),
        "month": _period_stats(restaurant_id, month_start),
        "top_items": top_selling_items(restaurant_id),
        "peak_hours": peak_hours(restaurant_id),
    }
