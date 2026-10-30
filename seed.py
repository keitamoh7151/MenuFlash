"""
Script de démo : crée un compte super-admin, un restaurant, quelques plats,
un compte cuisinier, une suggestion de vente additionnelle de démonstration,
et une commande servie déjà notée.
Usage :
    python seed.py
"""
from datetime import datetime, timedelta
from app import app
from models import db, Restaurant, User, MenuCategory, MenuItem, Order, OrderItem, DishReview, MenuItemSuggestion

with app.app_context():
    db.create_all()

    if not User.query.filter_by(email="admin@menuflash.com").first():
        tech = Restaurant.query.filter_by(slug="menuflash-admin").first()
        if not tech:
            tech = Restaurant(name="MenuFlash Admin", slug="menuflash-admin",
                               is_active=True,
                               subscription_active_until=datetime.utcnow() + timedelta(days=36500))
            db.session.add(tech)
            db.session.commit()

        super_admin = User(restaurant_id=tech.id, email="admin@menuflash.com",
                            role="owner", is_super_admin=True, email_verified=True)
        super_admin.set_password("SuperAdmin123!")
        db.session.add(super_admin)
        db.session.commit()
        print("Super-admin créé : admin@menuflash.com / SuperAdmin123!")

    if not Restaurant.query.filter_by(slug="chez-awa").first():
        r = Restaurant(
            name="Chez Awa", slug="chez-awa", currency="FCFA",
            subscription_plan="basic", theme="amber",
            subscription_active_until=datetime.utcnow() + timedelta(days=30),
        )
        db.session.add(r)
        db.session.commit()

        owner = User(restaurant_id=r.id, email="awa@example.com", role="owner", email_verified=True)
        owner.set_password("motdepasse123")
        db.session.add(owner)

        cuisinier = User(restaurant_id=r.id, email="cuisine@example.com", role="staff", email_verified=True)
        cuisinier.set_password("cuisine1234")
        db.session.add(cuisinier)

        entrees = MenuCategory(restaurant_id=r.id, name="Entrées", position=1)
        plats = MenuCategory(restaurant_id=r.id, name="Plats", position=2)
        boissons = MenuCategory(restaurant_id=r.id, name="Boissons", position=3)
        db.session.add_all([entrees, plats, boissons])
        db.session.commit()

        salade = MenuItem(restaurant_id=r.id, category_id=entrees.id,
                           name="Salade malienne", price=1500, position=1, prep_minutes=8)
        riz = MenuItem(restaurant_id=r.id, category_id=plats.id,
                        name="Riz au gras", description="Riz, viande de bœuf, légumes",
                        price=2500, position=1, prep_minutes=20)
        to = MenuItem(restaurant_id=r.id, category_id=plats.id,
                       name="Tô + sauce gombo", price=2000, position=2, prep_minutes=15)
        bissap = MenuItem(restaurant_id=r.id, category_id=boissons.id,
                           name="Bissap", price=500, position=1, prep_minutes=2)
        gingembre = MenuItem(restaurant_id=r.id, category_id=boissons.id,
                              name="Gingembre", price=500, position=2, prep_minutes=2)
        db.session.add_all([salade, riz, to, bissap, gingembre])
        db.session.commit()

        db.session.add_all([
            MenuItemSuggestion(restaurant_id=r.id, menu_item_id=riz.id, suggested_item_id=bissap.id),
            MenuItemSuggestion(restaurant_id=r.id, menu_item_id=riz.id, suggested_item_id=gingembre.id),
            MenuItemSuggestion(restaurant_id=r.id, menu_item_id=to.id, suggested_item_id=bissap.id),
        ])
        db.session.commit()

        demo_order = Order(restaurant_id=r.id, table_label="Table démo", status="servie", reviewed=True)
        db.session.add(demo_order)
        db.session.commit()
        db.session.add_all([
            OrderItem(order_id=demo_order.id, menu_item_id=riz.id, item_name=riz.name,
                      item_price=riz.price, quantity=1, prep_minutes=riz.prep_minutes),
            OrderItem(order_id=demo_order.id, menu_item_id=salade.id, item_name=salade.name,
                      item_price=salade.price, quantity=1, prep_minutes=salade.prep_minutes),
        ])
        db.session.add_all([
            DishReview(restaurant_id=r.id, order_id=demo_order.id, menu_item_id=riz.id,
                       item_name=riz.name, rating=5),
            DishReview(restaurant_id=r.id, order_id=demo_order.id, menu_item_id=riz.id,
                       item_name=riz.name, rating=4),
            DishReview(restaurant_id=r.id, order_id=demo_order.id, menu_item_id=salade.id,
                       item_name=salade.name, rating=4),
        ])
        db.session.commit()

        print("Restaurant de démo créé : slug='chez-awa'")
        print("  Gestionnaire : awa@example.com / motdepasse123")
        print("  Cuisinier (accès limité) : cuisine@example.com / cuisine1234")
        print("  Suggestions de vente additionnelle pré-configurées (Riz au gras → Bissap/Gingembre).")
        print("  Astuce : ajoutez un logo depuis Boutique > Apparence & logo pour remplacer")
        print("  la marque MenuFlash Mini par celle de votre restaurant.")
    else:
        print("Le restaurant de démo existe déjà.")
