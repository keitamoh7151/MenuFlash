"""
MenuFlash Mini — Ticket imprimable (imprimante thermique 58 ou 80 mm).

Branchement dans app.py, tout en bas, APRÈS init_takeaway(...) :

    from tickets import init_tickets
    init_tickets(app, active_restaurant=active_restaurant)

Fonctionnement
--------------
- /admin/commandes/<id>/ticket : ticket au format imprimante thermique, à
  coller sur le sac d'une commande à emporter (fonctionne aussi pour une
  commande sur place).
- Paramètres : ?w=58 ou ?w=80 (largeur du papier), ?print=1 (lance
  l'impression dès l'ouverture, utilisé par l'écran des commandes).
- L'impression se fait depuis le navigateur de l'appareil de la cuisine :
  le serveur (Render, dans le cloud) ne peut pas joindre une imprimante du
  réseau local du restaurant.

Contenu du ticket : nom du restaurant, « À EMPORTER », référence (3 derniers
caractères en gras), code de retrait, prénom du client, heures de commande et
de retrait, articles, quantités et remarques.
Volontairement ABSENTS : le téléphone du client (le ticket est collé sur un
sac visible de tous) et le total.

Sécurité : réservé à l'équipe connectée du restaurant (gestionnaire et
cuisine), isolation stricte par restaurant, contenu échappé par Jinja. Le
ticket peut être affiché dans un cadre (iframe) UNIQUEMENT par les pages de
l'application elle-même (impression sans ouvrir d'onglet).
"""
from datetime import datetime

from flask import abort, render_template, request, Response
from flask_login import current_user, login_required

from models import Order
from order_refs import ensure_reference

PAPER_WIDTHS = (58, 80)
TICKET_ENDPOINT = "order_ticket"


def init_tickets(app, *, active_restaurant):

    @app.route("/admin/commandes/<int:order_id>/ticket")
    @login_required
    def order_ticket(order_id):
        if getattr(current_user, "is_super_admin", False):
            abort(403)
        restaurant = active_restaurant()
        if restaurant is None:
            abort(403)
        order = Order.query.filter_by(id=order_id, restaurant_id=restaurant.id).first_or_404()

        try:
            width = int(request.args.get("w", 80))
        except ValueError:
            width = 80
        if width not in PAPER_WIDTHS:
            width = 80

        from orders_board import _local, _takeaway_map
        reference = ensure_reference(order)
        info = _takeaway_map([order.id]).get(order.id)
        now = datetime.utcnow()
        pickup = None
        if info and info.get("pickup"):
            p = info["pickup"]
            pickup = p.strftime("%H:%M") if p.date() == _local(now).date() else p.strftime("%d/%m à %H:%M")

        html = render_template(
            "order_ticket.html",
            restaurant=restaurant, order=order, reference=reference,
            takeaway=info, pickup=pickup,
            pickup_asap=bool(info and info.get("pickup_type") == "asap"),
            ordered_at=_local(order.created_at or now),
            printed_at=_local(now),
            width=width, auto_print=request.args.get("print") == "1",
        )
        resp = Response(html)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    def takeaway_public_info(order_id):
        """Infos « à emporter » montrées au CLIENT sur sa page de suivi (jamais le téléphone)."""
        from orders_board import _local, _takeaway_map
        info = _takeaway_map([order_id]).get(order_id)
        if not info:
            return None
        p = info.get("pickup")
        text = None
        if p:
            text = p.strftime("%H:%M") if p.date() == _local(datetime.utcnow()).date() else p.strftime("%d/%m à %H:%M")
        return {"pickup": text, "asap": info.get("pickup_type") == "asap"}

    app.jinja_env.globals["takeaway_public_info"] = takeaway_public_info

    def _allow_same_origin_frame(response):
        if request.endpoint == TICKET_ENDPOINT:
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
            csp = response.headers.get("Content-Security-Policy", "")
            if "frame-ancestors" not in csp:
                response.headers["Content-Security-Policy"] = (csp + " frame-ancestors 'self';").strip()
        return response

    # Les fonctions after_request s'exécutent dans l'ordre INVERSE de leur
    # enregistrement : on se place en premier pour passer en dernier, après
    # l'en-tête global « X-Frame-Options: DENY » d'app.py.
    app.after_request_funcs.setdefault(None, []).insert(0, _allow_same_origin_frame)
