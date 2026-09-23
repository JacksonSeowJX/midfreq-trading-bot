"""Generate FYP Update 12 Presentation — Validating the Cross-Sectional Result

Update 11 ended with one combination passing the validation standard for
the first time, and with the caveat that it only held when trading was
assumed free. This deck is about the attempts to knock it down: pinning
the actual broker fee, measuring selection bias with PBO, and a metric
built for the job that then failed to do it.

Card text reads from results/ where possible so the deck cannot drift
from the study output.
"""
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.enum.shapes import MSO_SHAPE
from pathlib import Path
import glob
import json
import sys

import pandas as pd

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)

BG = RGBColor(0xED, 0xE8, 0xE0)
BG_CARD = RGBColor(0xE0, 0xDB, 0xD3)
DARK = RGBColor(0x2D, 0x2D, 0x2D)
BRACKET = RGBColor(0x33, 0x33, 0x2D)
ACCENT_GREEN = RGBColor(0x2E, 0x7D, 0x32)
ACCENT_RED = RGBColor(0xC6, 0x28, 0x28)
DIM = RGBColor(0x6B, 0x6B, 0x63)
MID = RGBColor(0x55, 0x55, 0x50)

CHART_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent
RESULTS = Path(__file__).resolve().parent.parent / 'results'

pbo_full = json.loads((RESULTS / 'pbo_sp100_reversal_full96.json').read_text())
PBO = pbo_full['pbo']
PBO_TRIALS = pbo_full['n_trials']
PBO_BELOW = round(PBO * PBO_TRIALS)


def set_slide_bg(slide, color=BG):
    f = slide.background.fill; f.solid(); f.fore_color.rgb = color


def add_text(slide, left, top, width, height, text, font_size=18, color=DARK, bold=False,
             alignment=PP_ALIGN.LEFT, font_name="Calibri"):
    tb = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    tf = tb.text_frame; tf.word_wrap = True
    p = tf.paragraphs[0]; p.text = text
    p.font.size = Pt(font_size); p.font.color.rgb = color
    p.font.bold = bold; p.font.name = font_name; p.alignment = alignment
    return tf


def add_para(tf, text, font_size=18, color=DARK, bold=False, alignment=PP_ALIGN.LEFT,
             font_name="Calibri", space_before=Pt(6)):
    p = tf.add_paragraph(); p.text = text
    p.font.size = Pt(font_size); p.font.color.rgb = color
    p.font.bold = bold; p.font.name = font_name
    p.alignment = alignment; p.space_before = space_before
    return p


def add_bracket_tl(slide, left, top, size=1.2, thickness=0.12):
    v = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(left), Inches(top), Inches(thickness), Inches(size))
    v.fill.solid(); v.fill.fore_color.rgb = BRACKET; v.line.fill.background()
    h = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(left), Inches(top), Inches(size), Inches(thickness))
    h.fill.solid(); h.fill.fore_color.rgb = BRACKET; h.line.fill.background()


def add_bracket_br(slide, right, bottom, size=1.2, thickness=0.12):
    v = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(right - thickness), Inches(bottom - size), Inches(thickness), Inches(size))
    v.fill.solid(); v.fill.fore_color.rgb = BRACKET; v.line.fill.background()
    h = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(right - size), Inches(bottom - thickness), Inches(size), Inches(thickness))
    h.fill.solid(); h.fill.fore_color.rgb = BRACKET; h.line.fill.background()


def add_divider(slide, left, top, width):
    s = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(left), Inches(top), Inches(width), Pt(2))
    s.fill.solid(); s.fill.fore_color.rgb = BRACKET; s.line.fill.background()


def add_card(slide, left, top, width, height, fill_color=BG_CARD):
    s = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(left), Inches(top), Inches(width), Inches(height))
    s.fill.solid(); s.fill.fore_color.rgb = fill_color
    s.line.color.rgb = RGBColor(0xCC, 0xC7, 0xBF); s.line.width = Pt(1)
    return s


def picture(slide, name, left, top, width):
    img = CHART_DIR / name
    if img.exists():
        slide.shapes.add_picture(str(img), Inches(left), Inches(top), width=Inches(width))


# ==================== SLIDE 1: TITLE ====================
slide = prs.slides.add_slide(prs.slide_layouts[6]); set_slide_bg(slide)
add_bracket_tl(slide, 1.5, 1.0, size=2.0, thickness=0.15)
add_bracket_br(slide, 11.8, 6.5, size=2.0, thickness=0.15)
add_text(slide, 2.5, 2.6, 8.5, 1.2, "FYP UPDATE 12", font_size=44, color=DARK, bold=True, alignment=PP_ALIGN.CENTER)
add_text(slide, 2.5, 3.8, 8.5, 0.8, "Validating the Cross-Sectional Result", font_size=20, color=MID, alignment=PP_ALIGN.CENTER)
add_text(slide, 2.5, 5.0, 8.5, 0.5, "Jackson Seow  •  FYP 2025/2026", font_size=16, color=DIM, alignment=PP_ALIGN.CENTER)


# ==================== SLIDE 2: WHERE UPDATE 11 LEFT IT ====================
slide = prs.slides.add_slide(prs.slide_layouts[6]); set_slide_bg(slide)
add_bracket_tl(slide, 0.5, 0.3, size=0.8, thickness=0.1)
add_text(slide, 0.8, 0.5, 12, 0.7, "The Result Being Validated", font_size=28, color=DARK, bold=True)
add_divider(slide, 0.8, 1.15, 4)

add_card(slide, 0.8, 1.6, 5.6, 4.6)
tf = add_text(slide, 1.1, 1.85, 5.0, 0.5, "What passed", font_size=18, color=ACCENT_GREEN, bold=True)
add_para(tf, "")
add_para(tf, "Cross-sectional reversal on the S&P 100, which", font_size=14, color=DARK)
add_para(tf, "buys whichever stocks fell the most and bets they", font_size=14, color=DARK)
add_para(tf, "recover, was the first thing in this project to", font_size=14, color=DARK)
add_para(tf, "satisfy the two-configuration standard.", font_size=14, color=DARK)
add_para(tf, "")
add_para(tf, "That standard has rejected everything else:", font_size=14, color=DARK)
add_para(tf, "four strategy families, two markets, both", font_size=14, color=DARK)
add_para(tf, "directions of the cross-sectional bet.", font_size=14, color=DARK)

add_card(slide, 6.9, 1.6, 5.6, 4.6, fill_color=RGBColor(0xE4, 0xDE, 0xD2))
tf = add_text(slide, 7.2, 1.85, 5.0, 0.5, "What was still open", font_size=18, color=ACCENT_RED, bold=True)
add_para(tf, "")
add_para(tf, "The fee it was tested against was a figure we had", font_size=14, color=DARK)
add_para(tf, "estimated rather than looked up, and the settings", font_size=14, color=DARK)
add_para(tf, "came from searching 96 versions and keeping the best.", font_size=14, color=DARK)
add_para(tf, "")
add_para(tf, "So this update runs three checks: establishing the", font_size=14, color=DARK)
add_para(tf, "real fee, measuring how much of the result comes", font_size=14, color=DARK)
add_para(tf, "from the search itself, and comparing training", font_size=14, color=DARK)
add_para(tf, "returns against out-of-sample returns.", font_size=14, color=DARK)

add_card(slide, 0.8, 6.4, 11.7, 0.75)
add_text(slide, 1.1, 6.52, 11.2, 0.5,
         "A result that has passed only one test is not yet evidence. These are the three checks.",
         font_size=13.5, color=MID)


# ==================== SLIDE 3: THE REAL FEE ====================
slide = prs.slides.add_slide(prs.slide_layouts[6]); set_slide_bg(slide)
add_bracket_tl(slide, 0.5, 0.3, size=0.8, thickness=0.1)
add_text(slide, 0.8, 0.5, 12, 0.7, "Check 1: The Real Broker Fee", font_size=28, color=DARK, bold=True)
add_divider(slide, 0.8, 1.15, 4)
picture(slide, 'chart_u12_cost.png', 1.75, 1.45, 9.8)

add_card(slide, 0.8, 6.15, 11.7, 1.15)
tf = add_text(slide, 1.1, 6.25, 11.2, 0.5,
              "moomoo Singapore charges no commission on US stocks, just a flat US$0.99 per order plus 9% GST.",
              font_size=14, color=DARK, bold=True)
add_para(tf, "At the position sizes this strategy trades that is about 0.005% per side, not the 0.03% we had estimated. "
             "The result survives the real fee. It was the guess that killed it.",
         font_size=14, color=MID, space_before=Pt(2))


# ==================== SLIDE 4: HOW PBO WORKS ====================
slide = prs.slides.add_slide(prs.slide_layouts[6]); set_slide_bg(slide)
add_bracket_tl(slide, 0.5, 0.3, size=0.8, thickness=0.1)
add_text(slide, 0.8, 0.5, 12, 0.7, "Check 2: What Backtest Overfitting Is", font_size=28, color=DARK, bold=True)
add_divider(slide, 0.8, 1.15, 4)
picture(slide, 'chart_u12_pbo_explainer.png', 1.15, 1.5, 11.0)

add_card(slide, 0.8, 6.3, 11.7, 0.95)
add_text(slide, 1.1, 6.42, 11.2, 0.5,
         "Backtest overfitting is when a search over many versions reports luck as skill. Try 96 versions on data "
         "with no pattern at all and one still looks good, so this measures how often that is what happened.",
         font_size=14, color=DARK, bold=True)


# ==================== SLIDE 5: PBO RESULT ====================
slide = prs.slides.add_slide(prs.slide_layouts[6]); set_slide_bg(slide)
add_bracket_tl(slide, 0.5, 0.3, size=0.8, thickness=0.1)
add_text(slide, 0.8, 0.5, 12, 0.7, "Check 2: How Much Is the Search Finding Luck?", font_size=27, color=DARK, bold=True)
add_divider(slide, 0.8, 1.15, 4)
picture(slide, 'chart_u12_pbo.png', 2.6, 1.5, 8.2)

add_card(slide, 0.8, 6.15, 11.7, 1.15)
tf = add_text(slide, 1.1, 6.25, 11.2, 0.5,
              f"The winner lands in the bottom half {PBO_BELOW} times out of {PBO_TRIALS}, so {PBO:.3f}. Selection is doing real work.",
              font_size=14, color=DARK, bold=True)
add_para(tf, "A first test run on 40 of the 96 versions gave 0.095. Fewer versions means fewer chances to get "
             "lucky, so that number flattered the result. The full grid is the honest one.",
         font_size=14, color=MID, space_before=Pt(2))


# ==================== SLIDE 6: THE TRAINING NUMBERS ====================
slide = prs.slides.add_slide(prs.slide_layouts[6]); set_slide_bg(slide)
add_bracket_tl(slide, 0.5, 0.3, size=0.8, thickness=0.1)
add_text(slide, 0.8, 0.5, 12, 0.7, "Check 3: Training Returns Against Test Returns", font_size=27, color=DARK, bold=True)
add_divider(slide, 0.8, 1.15, 4)
picture(slide, 'chart_u12_gap.png', 1.3, 1.5, 10.6)

add_card(slide, 0.8, 6.25, 11.7, 1.05)
tf = add_text(slide, 1.1, 6.35, 11.2, 0.5,
              "Every cross-sectional run earns 18% to 39% per window in training and almost nothing afterwards.",
              font_size=14, color=DARK, bold=True)
add_para(tf, "The single-stock strategies, searching a grid of similar size, never make such a claim. The difference is "
             "how much freedom the strategy has to fit the past.",
         font_size=14, color=MID, space_before=Pt(2))


# ==================== SLIDE 7: SUMMARY ====================
slide = prs.slides.add_slide(prs.slide_layouts[6]); set_slide_bg(slide)
add_bracket_tl(slide, 1.5, 0.7, size=2.0, thickness=0.15)
add_bracket_br(slide, 11.8, 6.8, size=2.0, thickness=0.15)
add_text(slide, 2.5, 1.0, 8.5, 0.7, "SUMMARY", font_size=32, color=DARK, bold=True, alignment=PP_ALIGN.CENTER)

tf = add_text(slide, 2.0, 2.05, 9.5, 0.5, "✅   The real broker fee is 0.005% per side, not the 0.03% we estimated. The result survives it", font_size=16.5, color=DARK)
add_para(tf, "", font_size=7)
add_para(tf, f"✅   Overfitting probability is {PBO:.3f}, well under the 0.50 that would mean the search is meaningless", font_size=16.5, color=DARK)
add_para(tf, "", font_size=7)
add_para(tf, "✅   A fourth measure was built for this, did not discriminate, and is reported as a failure", font_size=16.5, color=DARK)
add_para(tf, "", font_size=7)
add_para(tf, "⚠️   Still unresolved: 6 strategies on 4 stock universes is about 24 combinations,", font_size=16.5, color=DARK)
add_para(tf, "        and only 1 passed. Trying 24 would give 1 that passes even if none worked", font_size=16.5, color=DARK, space_before=Pt(0))

add_card(slide, 2.0, 5.15, 9.5, 1.48)
tf = add_text(slide, 2.3, 5.27, 9, 0.5, "🎯  Next: Correcting for How Many Combinations Were Tried", font_size=18, color=ACCENT_GREEN, bold=True)
add_para(tf, "This could be a real edge, or the 1 combination out of 24 that happened to land well, and nothing run so far "
             "can tell those apart. Not wrong, just unresolved.", font_size=14, color=MID)
add_para(tf, "Also going live on paper this week, for a real track record by the end.",
         font_size=14, color=ACCENT_GREEN, space_before=Pt(4))

add_text(slide, 2.5, 6.72, 8.5, 0.6, "Thank You", font_size=26, color=BRACKET, bold=True, alignment=PP_ALIGN.CENTER)


from pptx_fit import fit_text_boxes
print("fitting text boxes to their content:")
n = fit_text_boxes(prs)
print(f"  {n} box(es) resized")

output_path = "/Users/jacksonetherchainstake/FYP/Documents/FYP_Update_12.pptx"
prs.save(output_path)
print(f"Saved to {output_path}")
print(f"  PBO used: {PBO:.3f} ({PBO_BELOW}/{PBO_TRIALS})")
