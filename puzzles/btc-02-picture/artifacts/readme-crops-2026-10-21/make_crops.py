#!/usr/bin/env python3
"""Crops of artifacts/puzzle.png for the public page (2026-10-21).

Every crop is a plain pixel copy of a box of the canonical 1600 x 1200 file, at native
resolution. The four ZOOM crops are enlarged by whole-pixel repetition (nearest neighbour),
which adds no detail; nothing here is super-resolved, sharpened or recoloured. The boxes
are (x0, y0, x1, y1) in canvas pixels. Run from anywhere:

    python3 make_crops.py            # writes the PNGs next to this file and prints their sha256
"""
import hashlib
import os
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "puzzle.png")
SHA = "d0b04378f75d63997b8034ec2ef1bdd108178e4546de78237bd35abf4189a782"

# name, box, rotation (degrees counter-clockwise, Pillow's convention), integer zoom
CROPS = [
    ("overview_800",        (0, 0, 1600, 1200),      0, 0),      # the whole picture at half size (the one resampled file)
    ("left_border_address", (30, 590, 95, 1160),     -90, 1),    # the address, written upward along the left border
    ("top_border_runes",    (195, 30, 575, 120),     0, 1),      # three rows of the rune cipher
    ("right_border_runes",  (1520, 25, 1578, 1180),  90, 1),     # the right-border rune row, turned to read along
    ("clock_and_seal",      (300, 740, 845, 1190),   0, 1),      # the clock under the mirrored Great Seal
    ("great_seal",          (455, 750, 840, 1140),   0, 1),
    ("liberty",             (45, 450, 365, 1175),    0, 1),      # the statue, the tablet, "1865-202...?", the base
    ("liberty_base_zoom",   (80, 1035, 250, 1075),   0, 3),      # "ONLY real BITCOIN"
    ("flag_and_fist",       (0, 300, 390, 640),      0, 1),
    ("masked_group",        (40, 30, 560, 290),      0, 1),      # four masked people, the yellow face boxes
    ("cameras",             (1270, 50, 1530, 270),   0, 1),
    ("order_and_stability", (640, 68, 1000, 118),    0, 2),      # the only yellow text
    ("chart_hand_sign",     (0, 200, 490, 470),      0, 1),      # the gold chart, the gloved hand, the COVID sign
    ("floyd_and_slogans",   (900, 140, 1320, 440),   0, 1),
    ("space_needle",        (1060, 350, 1330, 850),  0, 1),
    ("bust_and_pedestal",   (1270, 420, 1590, 1170), 0, 1),
    ("trump_biden",         (840, 760, 1300, 1170),  0, 1),      # the profiles, the ?, .VS., 11.03.20, the cipher row
    ("cipher_row_zoom",     (835, 825, 920, 875),    0, 4),      # the Bill-Cipher row above the left profile
    ("header",              (470, 45, 980, 430),     0, 1),      # FIND THE SEED PHRASE IN THE THIS PICTURE
    ("title_microtext",     (420, 400, 1100, 700),   0, 1),      # WELCOME TO THE BRAVE NEW WORLD, letters of micro-text
    ("maps_and_rifle",      (600, 600, 1120, 840),   0, 1),
    ("bottom_border",       (30, 1140, 1480, 1195),  0, 1),      # the whitepaper line and the Latin proverb
    ("signature_zoom",      (40, 30, 82, 96),        0, 4),      # CHaRLy, top left
    ("monogram_zoom",       (1500, 1110, 1565, 1165), 0, 4),     # -CHR-, bottom right
]


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def main():
    assert sha256(SRC) == SHA, "artifacts/puzzle.png is not the canonical file"
    im = Image.open(SRC).convert("RGB")
    assert im.size == (1600, 1200)
    for name, box, rot, zoom in CROPS:
        c = im.crop(box)
        if zoom == 0:
            c = c.resize((800, 600), Image.LANCZOS)
        elif zoom > 1:
            c = c.resize((c.width * zoom, c.height * zoom), Image.NEAREST)
        if rot:
            c = c.rotate(rot, expand=True)
        out = os.path.join(HERE, name + ".png")
        c.save(out, optimize=True)
        print(f"{sha256(out)}  {name}.png  {c.width}x{c.height}  box={box} rot={rot} zoom={zoom}")


if __name__ == "__main__":
    main()
