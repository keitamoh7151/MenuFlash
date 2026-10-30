"""
Génération du PDF "chevalet de table" imprimable pour un QR code.
Format A6 (105 x 148 mm), pensé pour être imprimé et plié/posé sur une table.
"""
import io
import qrcode
from reportlab.lib.pagesizes import mm
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.lib.colors import HexColor

INK = HexColor("#1A1D23")
INK_SOFT = HexColor("#6B6A66")


def make_table_tent_pdf(restaurant_name, qr_target_url, table_label=None, accent_hex="#D98E1F"):
    accent = HexColor(accent_hex)
    page_w, page_h = 105 * mm, 148 * mm
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_w, page_h))

    c.setFillColor(INK)
    c.rect(0, page_h - 22 * mm, page_w, 22 * mm, fill=1, stroke=0)
    c.setFillColor(HexColor("#FFFFFF"))
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(page_w / 2, page_h - 14 * mm, restaurant_name[:28])

    qr = qrcode.QRCode(box_size=10, border=1)
    qr.add_data(qr_target_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    qr_buf = io.BytesIO()
    img.save(qr_buf, format="PNG")
    qr_buf.seek(0)

    qr_size = 68 * mm
    qr_x = (page_w - qr_size) / 2
    qr_y = page_h - 22 * mm - qr_size - 10 * mm
    c.drawImage(ImageReader(qr_buf), qr_x, qr_y, width=qr_size, height=qr_size)

    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(page_w / 2, qr_y - 8 * mm, "Scannez pour voir le menu")

    if table_label:
        c.setFont("Helvetica", 10)
        c.setFillColor(accent)
        c.drawCentredString(page_w / 2, qr_y - 14 * mm, table_label)

    c.setFont("Helvetica", 7)
    c.setFillColor(INK_SOFT)
    c.drawCentredString(page_w / 2, 6 * mm, "Propulsé par MenuFlash Mini")

    c.showPage()
    c.save()
    buf.seek(0)
    return buf
