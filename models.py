"""
MenuFlash Mini — Modèles de données (SQLAlchemy)
Architecture multi-tenant à schéma partagé : chaque table métier
porte une colonne restaurant_id qui isole les données d'un client.
"""
import secrets
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

THEME_PRESETS = {
    "amber":    {"label": "Ambre",    "accent": "#D98E1F", "accent_dark": "#B5730F", "accent_light": "#FBE9CC"},
    "emerald":  {"label": "Émeraude", "accent": "#1F6F4F", "accent_dark": "#164F38", "accent_light": "#DCEFE4"},
    "burgundy": {"label": "Bordeaux", "accent": "#7A2436", "accent_dark": "#591A27", "accent_light": "#F0D9DD"},
    "sapphire": {"label": "Saphir",   "accent": "#2A5BA8", "accent_dark": "#1E4480", "accent_light": "#DCE6F7"},
    "olive":    {"label": "Olive",    "accent": "#7A6B22", "accent_dark": "#5C4F18", "accent_light": "#EAE3C4"},
    "coral":    {"label": "Corail",   "accent": "#D1573B", "accent_dark": "#A83F2A", "accent_light": "#FAE0D8"},
}
DEFAULT_THEME = "amber"
DEFAULT_PREP_MINUTES = 10
TRIAL_DAYS = 7

MAX_FAILED_LOGIN_ATTEMPTS = 5
LOCKOUT_DURATION_MINUTES = 15


class Restaurant(db.Model):
    __tablename__ = "restaurants"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    slug = db.Column(db.String(80), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(30))
    address = db.Column(db.String(200))
    logo_url = db.Column(db.String(300))
    currency = db.Column(db.String(10), default="FCFA")
    language = db.Column(db.String(5), default="fr")
    theme = db.Column(db.String(20), default=DEFAULT_THEME)

    subscription_plan = db.Column(db.String(20), default="trial")
    subscription_active_until = db.Column(db.DateTime)
    is_active = db.Column(db.Boolean, default=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    users = db.relationship("User", backref="restaurant", lazy=True)
    categories = db.relationship("MenuCategory", backref="restaurant", lazy=True,
                                  order_by="MenuCategory.position")
    qrcodes = db.relationship("QRCode", backref="restaurant", lazy=True,
                               order_by="QRCode.created_at")

    @property
    def theme_colors(self):
        return THEME_PRESETS.get(self.theme, THEME_PRESETS[DEFAULT_THEME])

    @property
    def is_subscription_valid(self):
        if not self.subscription_active_until:
            return False
        return self.subscription_active_until >= datetime.utcnow()

    @property
    def days_remaining(self):
        if not self.subscription_active_until:
            return 0
        delta = self.subscription_active_until - datetime.utcnow()
        return max(delta.days, 0)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    restaurant_id = db.Column(db.Integer, db.ForeignKey("restaurants.id"), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="owner")
    is_super_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    failed_login_attempts = db.Column(db.Integer, default=0, nullable=False)
    locked_until = db.Column(db.DateTime, nullable=True)
    last_login_at = db.Column(db.DateTime, nullable=True)

    security_stamp = db.Column(db.String(32), nullable=False, default=lambda: secrets.token_hex(16))

    email_verified = db.Column(db.Boolean, default=False, nullable=False)
    email_verified_at = db.Column(db.DateTime, nullable=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
        self.rotate_security_stamp()

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def rotate_security_stamp(self):
        self.security_stamp = secrets.token_hex(16)

    def get_id(self):
        return f"{self.id}:{self.security_stamp}"

    @property
    def is_owner(self):
        return self.role == "owner"

    @property
    def is_locked(self):
        return bool(self.locked_until and self.locked_until > datetime.utcnow())

    @property
    def lock_minutes_remaining(self):
        if not self.is_locked:
            return 0
        delta = self.locked_until - datetime.utcnow()
        return max(int(delta.total_seconds() // 60) + 1, 1)

    def register_failed_login(self):
        self.failed_login_attempts = (self.failed_login_attempts or 0) + 1
        if self.failed_login_attempts >= MAX_FAILED_LOGIN_ATTEMPTS:
            from datetime import timedelta
            self.locked_until = datetime.utcnow() + timedelta(minutes=LOCKOUT_DURATION_MINUTES)

    def register_successful_login(self):
        self.failed_login_attempts = 0
        self.locked_until = None
        self.last_login_at = datetime.utcnow()


class RestaurantAccess(db.Model):
    __tablename__ = "restaurant_access"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    restaurant_id = db.Column(db.Integer, db.ForeignKey("restaurants.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class MenuCategory(db.Model):
    __tablename__ = "menu_categories"

    id = db.Column(db.Integer, primary_key=True)
    restaurant_id = db.Column(db.Integer, db.ForeignKey("restaurants.id"), nullable=False)
    name = db.Column(db.String(80), nullable=False)
    position = db.Column(db.Integer, default=0)
    is_paused = db.Column(db.Boolean, default=False)

    items = db.relationship("MenuItem", backref="category", lazy=True,
                             order_by="MenuItem.position")


class MenuItem(db.Model):
    __tablename__ = "menu_items"

    id = db.Column(db.Integer, primary_key=True)
    restaurant_id = db.Column(db.Integer, db.ForeignKey("restaurants.id"), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("menu_categories.id"), nullable=False)

    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(300))
    price = db.Column(db.Integer, nullable=False)
    photo_url = db.Column(db.String(300))
    is_available = db.Column(db.Boolean, default=True)
    position = db.Column(db.Integer, default=0)
    prep_minutes = db.Column(db.Integer, default=DEFAULT_PREP_MINUTES)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MenuItemSuggestion(db.Model):
    __tablename__ = "menu_item_suggestions"

    id = db.Column(db.Integer, primary_key=True)
    restaurant_id = db.Column(db.Integer, db.ForeignKey("restaurants.id"), nullable=False)
    menu_item_id = db.Column(db.Integer, db.ForeignKey("menu_items.id"), nullable=False)
    suggested_item_id = db.Column(db.Integer, db.ForeignKey("menu_items.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class QRCode(db.Model):
    __tablename__ = "qr_codes"

    id = db.Column(db.Integer, primary_key=True)
    restaurant_id = db.Column(db.Integer, db.ForeignKey("restaurants.id"), nullable=False)
    label = db.Column(db.String(50), default="Menu principal")
    scan_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class SubscriptionPayment(db.Model):
    __tablename__ = "subscription_payments"

    id = db.Column(db.Integer, primary_key=True)
    restaurant_id = db.Column(db.Integer, db.ForeignKey("restaurants.id"), nullable=False)
    amount = db.Column(db.Integer, nullable=False)
    method = db.Column(db.String(30))
    reference = db.Column(db.String(80))
    months_added = db.Column(db.Integer, default=1)
    recorded_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    restaurant = db.relationship("Restaurant", backref="payments")


class Order(db.Model):
    __tablename__ = "orders"

    id = db.Column(db.Integer, primary_key=True)
    restaurant_id = db.Column(db.Integer, db.ForeignKey("restaurants.id"), nullable=False)
    qr_code_id = db.Column(db.Integer, db.ForeignKey("qr_codes.id"), nullable=True)
    table_label = db.Column(db.String(50))
    status = db.Column(db.String(20), default="nouvelle")
    notes = db.Column(db.String(300))
    comment = db.Column(db.String(500))
    reviewed = db.Column(db.Boolean, default=False)
    cancelled_by = db.Column(db.String(20))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    restaurant = db.relationship("Restaurant", backref="orders")
    items = db.relationship("OrderItem", backref="order", lazy=True,
                             cascade="all, delete-orphan")

    @property
    def total(self):
        return sum(item.item_price * item.quantity for item in self.items)

    @property
    def estimated_prep_minutes(self):
        if not self.items:
            return 0
        return max((it.prep_minutes or DEFAULT_PREP_MINUTES) for it in self.items)


class OrderItem(db.Model):
    __tablename__ = "order_items"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    menu_item_id = db.Column(db.Integer, db.ForeignKey("menu_items.id"), nullable=True)
    item_name = db.Column(db.String(120), nullable=False)
    item_price = db.Column(db.Integer, nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    prep_minutes = db.Column(db.Integer, default=DEFAULT_PREP_MINUTES)


class DishReview(db.Model):
    __tablename__ = "dish_reviews"

    id = db.Column(db.Integer, primary_key=True)
    restaurant_id = db.Column(db.Integer, db.ForeignKey("restaurants.id"), nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    menu_item_id = db.Column(db.Integer, db.ForeignKey("menu_items.id"), nullable=True)
    item_name = db.Column(db.String(120), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class MenuChangeLog(db.Model):
    __tablename__ = "menu_change_logs"

    id = db.Column(db.Integer, primary_key=True)
    restaurant_id = db.Column(db.Integer, db.ForeignKey("restaurants.id"), nullable=False)
    user_email = db.Column(db.String(120))
    action = db.Column(db.String(40), nullable=False)
    description = db.Column(db.String(300), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    restaurant_id = db.Column(db.Integer, db.ForeignKey("restaurants.id"), nullable=True)
    user_email = db.Column(db.String(120), nullable=True)
    action = db.Column(db.String(40), nullable=False)
    success = db.Column(db.Boolean, default=True, nullable=False)
    ip_address = db.Column(db.String(64), nullable=True)
    details = db.Column(db.String(300), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
