"""Génère doc/logo.png (tuile du portail YunoHost) : une puce (le SoC) et une courbe de mesure.

Outil de développement uniquement (Pillow) : `pip install pillow && python tools/make_logo.py`.
Le favicon de l'interface (static/index.html) en reprend la couleur et la courbe, sans la puce,
trop fine à 16 px.
"""
import os

from PIL import Image, ImageDraw

SIZE = 512                 # taille finale
SS = 4                     # sur-échantillonnage pour l'anticrénelage
BLUE = (42, 120, 214)      # --s1 de l'interface
WHITE = (255, 255, 255)

# Géométrie dans une grille de 32 (comme le favicon), mise à l'échelle ensuite
TILE = (1, 1, 31, 31)      # carré arrondi de fond
TILE_RADIUS = 7
CHIP = (9, 9, 23, 23)      # contour de la puce
CHIP_RADIUS = 2.5
STROKE = 1.7
PINS = (12.5, 16, 19.5)    # positions des broches sur chaque côté
PIN_LEN = 3.2
CURVE = [(12, 18.3), (14.4, 15.6), (16.6, 17.4), (20, 13.4)]     # courbe de mesure, dans la puce


def main(out):
    k = SIZE * SS / 32.0
    img = Image.new("RGBA", (SIZE * SS, SIZE * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def box(b):
        return [round(v * k) for v in b]

    def line(points, width):
        pts = [(round(x * k), round(y * k)) for x, y in points]
        w = round(width * k)
        d.line(pts, fill=WHITE, width=w, joint="curve")
        for x, y in (pts[0], pts[-1]):                     # extrémités arrondies
            d.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=WHITE)

    d.rounded_rectangle(box(TILE), radius=round(TILE_RADIUS * k), fill=BLUE)
    d.rounded_rectangle(box(CHIP), radius=round(CHIP_RADIUS * k), outline=WHITE, width=round(STROKE * k))
    x0, y0, x1, y1 = CHIP
    for p in PINS:
        line([(p, y0 - PIN_LEN), (p, y0 - STROKE / 2)], STROKE)     # haut
        line([(p, y1 + STROKE / 2), (p, y1 + PIN_LEN)], STROKE)     # bas
        line([(x0 - PIN_LEN, p), (x0 - STROKE / 2, p)], STROKE)     # gauche
        line([(x1 + STROKE / 2, p), (x1 + PIN_LEN, p)], STROKE)     # droite
    line(CURVE, STROKE)

    img.resize((SIZE, SIZE), Image.LANCZOS).save(out, optimize=True)
    print("écrit :", out)


if __name__ == "__main__":
    main(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "doc", "logo.png"))
