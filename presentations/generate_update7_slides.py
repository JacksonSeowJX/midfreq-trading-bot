"""Generate FYP Update 7 Presentation — Moving to the Cloud: Deploying the
Bot for Unattended Trading.

Matches the FYP theme (see generate_update5_slides.py). Deployment-only;
the live track record and the state-drift bug live in Update 8.
"""
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.dml import MSO_LINE_DASH_STYLE
from pathlib import Path
import sys

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)

BG = RGBColor(0xED, 0xE8, 0xE0)
BG_CARD = RGBColor(0xE0, 0xDB, 0xD3)
DARK = RGBColor(0x2D, 0x2D, 0x2D)
BRACKET = RGBColor(0x33, 0x33, 0x2D)
ACCENT_GREEN = RGBColor(0x2E, 0x7D, 0x32)
ACCENT_RED = RGBColor(0xC6, 0x28, 0x28)
ACCENT_AMBER = RGBColor(0xE6, 0x8A, 0x00)
DIM = RGBColor(0x6B, 0x6B, 0x63)
MID = RGBColor(0x55, 0x55, 0x50)

CHART_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent


def set_slide_bg(slide, color=BG):
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = color


def add_text(slide, left, top, width, height, text, font_size=18, color=DARK, bold=False, alignment=PP_ALIGN.LEFT, font_name="Calibri"):
    txBox = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    tf = txBox.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = text
    p.font.size = Pt(font_size)
    p.font.color.rgb = color
    p.font.bold = bold
    p.font.name = font_name
    p.alignment = alignment
    return tf


def add_para(tf, text, font_size=18, color=DARK, bold=False, alignment=PP_ALIGN.LEFT, font_name="Calibri", space_before=Pt(6)):
    p = tf.add_paragraph()
    p.text = text
    p.font.size = Pt(font_size)
    p.font.color.rgb = color
    p.font.bold = bold
    p.font.name = font_name
    p.alignment = alignment
    p.space_before = space_before
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
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(left), Inches(top), Inches(width), Pt(2))
    shape.fill.solid(); shape.fill.fore_color.rgb = BRACKET; shape.line.fill.background()


def add_card(slide, left, top, width, height, fill_color=BG_CARD):
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(left), Inches(top), Inches(width), Inches(height))
    shape.fill.solid(); shape.fill.fore_color.rgb = fill_color
    shape.line.color.rgb = RGBColor(0xCC, 0xC7, 0xBF); shape.line.width = Pt(1)
    return shape


# ==================== SLIDE 1: TITLE ====================
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
add_bracket_tl(slide, 1.5, 1.0, size=2.0, thickness=0.15)
add_bracket_br(slide, 11.8, 6.5, size=2.0, thickness=0.15)
add_text(slide, 2.5, 2.6, 8.5, 1.2, "FYP UPDATE 7", font_size=44, color=DARK, bold=True, alignment=PP_ALIGN.CENTER)
add_text(slide, 2.5, 3.8, 8.5, 0.8, "Moving to the Cloud: Deploying the Bot for Unattended Trading", font_size=20, color=MID, alignment=PP_ALIGN.CENTER)
add_text(slide, 2.5, 5.0, 8.5, 0.5, "Jackson Seow  •  FYP 2025/2026", font_size=16, color=DIM, alignment=PP_ALIGN.CENTER)


# ==================== SLIDE 2: THE SETUP ====================
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
add_bracket_tl(slide, 0.5, 0.3, size=0.8, thickness=0.1)
add_text(slide, 0.8, 0.5, 11.5, 0.7, "Moving Off My Laptop", font_size=30, color=DARK, bold=True)
add_divider(slide, 0.8, 1.15, 4)

add_card(slide, 0.8, 1.5, 5.6, 4.3)
tf = add_text(slide, 1.1, 1.7, 5.0, 0.5, "The Setup", font_size=20, color=DARK, bold=True)
add_para(tf, "")
add_para(tf, "A small server on Google Cloud, in Singapore,", font_size=15, color=DARK)
add_para(tf, "runs the broker connection and the bot.", font_size=15, color=DARK)
add_para(tf, "")
add_para(tf, "Every market morning, on its own:", font_size=15, color=DARK, bold=True)
add_para(tf, "1.  Pull the latest version of my code", font_size=14, color=MID)
add_para(tf, "2.  Run the full test suite", font_size=14, color=MID)
add_para(tf, "     — broken code never gets to trade", font_size=12, color=DIM)
add_para(tf, "3.  Trade until the market closes", font_size=14, color=MID)
add_para(tf, "4.  Save the day's results", font_size=14, color=MID)
add_para(tf, "")
add_para(tf, "No laptop, no reminders, no manual steps.", font_size=14, color=ACCENT_GREEN, bold=True)

add_card(slide, 6.7, 1.5, 5.9, 4.3)
tf = add_text(slide, 7.0, 1.65, 5.3, 0.4, "From Manual to Unattended", font_size=18, color=DARK, bold=True)
img = CHART_DIR / 'chart_u7_timeline.png'
if img.exists():
    slide.shapes.add_picture(str(img), Inches(6.85), Inches(2.3), width=Inches(5.6))
add_text(slide, 7.0, 5.1, 5.4, 0.9, "Grey = I ran it by hand. Orange = the day I deployed it. "
         "Blue = every day since, running completely on its own.", font_size=13, color=DIM)


# ==================== SLIDE 3: TWO BUGS + BUDGET ====================
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
add_bracket_tl(slide, 0.5, 0.3, size=0.8, thickness=0.1)
add_text(slide, 0.8, 0.5, 11.5, 0.7, "Two Real Bugs, Found by Going Live", font_size=30, color=DARK, bold=True)
add_divider(slide, 0.8, 1.15, 4)

add_card(slide, 0.8, 1.5, 5.6, 4.3)
tf = add_text(slide, 1.1, 1.7, 5.0, 0.5, "What Went Wrong at First", font_size=19, color=DARK, bold=True)
add_para(tf, "")
add_para(tf, "Clock bug", font_size=15, color=DARK, bold=True)
add_para(tf, "The server's schedule read the wrong time zone.", font_size=14, color=DARK)
add_para(tf, "The first morning's trading started late.", font_size=14, color=DARK)
add_para(tf, "")
add_para(tf, "Shared-account bug", font_size=15, color=DARK, bold=True)
add_para(tf, "Two strategies trade through the same paper", font_size=14, color=DARK)
add_para(tf, "account. A brief network drop made the system", font_size=14, color=DARK)
add_para(tf, "briefly lose track of whose shares were whose.", font_size=14, color=DARK)
add_para(tf, "")
add_para(tf, "Both found, both fixed, both now covered by", font_size=14, color=ACCENT_GREEN, bold=True)
add_para(tf, "automated tests so they can't happen silently again.", font_size=14, color=ACCENT_GREEN, bold=True)

ph = add_card(slide, 6.7, 1.5, 5.9, 4.3, RGBColor(0xF5, 0xF2, 0xEC))
ph.line.dash_style = MSO_LINE_DASH_STYLE.DASH
tf = add_text(slide, 7.0, 1.7, 5.3, 0.5, "This Costs Real Money — Mine", font_size=19, color=DARK, bold=True)
add_para(tf, "")
add_para(tf, "[ Paste GCP Billing → Budgets & Alerts", font_size=14, color=DIM)
add_para(tf, "   screenshot here ]", font_size=14, color=DIM)
add_para(tf, "")
add_para(tf, "Personal Google Cloud account, paid out of", font_size=14, color=MID)
add_para(tf, "pocket — about US$16 a month.", font_size=14, color=MID)
add_para(tf, "")
add_para(tf, "A budget alert is set so I know immediately", font_size=14, color=MID)
add_para(tf, "if costs ever run higher than expected.", font_size=14, color=MID)


# ==================== SLIDE 4: SUMMARY & NEXT ====================
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
add_bracket_tl(slide, 1.5, 0.7, size=2.0, thickness=0.15)
add_bracket_br(slide, 11.8, 6.8, size=2.0, thickness=0.15)

add_text(slide, 2.5, 1.1, 8.5, 0.7, "SUMMARY", font_size=32, color=DARK, bold=True, alignment=PP_ALIGN.CENTER)

tf = add_text(slide, 2.0, 2.1, 9.5, 0.5, "✅   The bot now trades on its own, every market day, no laptop needed", font_size=17, color=DARK)
add_para(tf, "")
add_para(tf, "✅   A test gate runs before every trading day — broken code can't trade", font_size=17, color=DARK)
add_para(tf, "")
add_para(tf, "✅   Two real deployment bugs found and fixed, both now tested", font_size=17, color=DARK)
add_para(tf, "")
add_para(tf, "✅   Running on my own cloud account, budget-capped, at my own cost", font_size=17, color=DARK)

add_card(slide, 2.0, 5.0, 9.5, 1.3)
tf = add_text(slide, 2.3, 5.15, 9, 0.5, "🎯  Next: What the Bot Has Actually Done", font_size=18, color=ACCENT_GREEN, bold=True)
add_para(tf, "Now that it runs on its own, Update 8 covers what it's actually traded — including a", font_size=14, color=MID)
add_para(tf, "full trade followed start to finish, and a data bug I caught while checking the results.", font_size=14, color=MID, space_before=Pt(2))

add_text(slide, 2.5, 6.6, 8.5, 0.6, "Thank You", font_size=26, color=BRACKET, bold=True, alignment=PP_ALIGN.CENTER)


output_path = "/Users/jacksonetherchainstake/FYP/Documents/FYP_Update_7.pptx"
prs.save(output_path)
print(f"Saved to {output_path}")
