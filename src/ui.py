"""
Look and feel of the dashboard: palette, page header and the CSS that turns
Streamlit's defaults into cards, quiet section labels and uniform buttons.

Kept out of app.py so the styling can change without touching any view's
logic. Nothing here affects what the dashboard computes.
"""
import streamlit as st

INK = '#1F2328'
MUTED = '#667085'
PRIMARY = '#1F4E79'
BLUE = '#2a78d6'
ORANGE = '#eb6834'
GREEN = '#2E7D32'
RED = '#C62828'
BORDER = '#E5E1D8'
NEUTRAL_BAR = '#B8B3AA'

_CSS = f"""
<style>
/* page chrome */
[data-testid="stDecoration"], [data-testid="stAppDeployButton"], .stDeployButton {{ display: none !important; }}
header[data-testid="stHeader"] {{ background: transparent; }}
[data-testid="stAppViewBlockContainer"], [data-testid="stMainBlockContainer"] {{
    padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1500px; }}

/* header band */
.fyp-header {{ display: flex; align-items: flex-end; justify-content: space-between; gap: 1rem;
    border-bottom: 1px solid {BORDER}; padding-bottom: .8rem; margin-bottom: 1.2rem; }}
.fyp-header .title {{ font-size: 1.7rem; font-weight: 700; letter-spacing: -.01em; color: {INK}; line-height: 1.2; }}
.fyp-header .sub {{ color: {MUTED}; font-size: .93rem; margin-top: .15rem; }}

/* headings */
[data-testid="stMarkdownContainer"] h3 {{ font-size: 1.18rem; font-weight: 650; color: {INK};
    padding: .5rem 0 .35rem 0; }}
[data-testid="stMarkdownContainer"] h4 {{ font-size: 1.02rem; font-weight: 650; color: {INK}; padding-top: .4rem; }}

/* numbers as cards */
[data-testid="stMetric"] {{ background: #FFFFFF; border: 1px solid {BORDER}; border-radius: 10px;
    padding: .8rem 1rem .75rem 1rem; box-shadow: 0 1px 2px rgba(16, 24, 40, .04); }}
[data-testid="stMetricLabel"] p {{ font-size: .72rem; font-weight: 600; text-transform: uppercase;
    letter-spacing: .05em; color: {MUTED}; }}
[data-testid="stMetricValue"] {{ font-size: 1.42rem; font-weight: 650; line-height: 1.3; color: {INK}; }}
[data-testid="stMetricDelta"] {{ font-size: .82rem; }}

/* charts and tables as cards */
.stPlotlyChart, [data-testid="stPlotlyChart"] {{ background: #FFFFFF; border: 1px solid {BORDER};
    border-radius: 10px; overflow: hidden; }}
[data-testid="stDataFrame"] {{ border: 1px solid {BORDER}; border-radius: 10px; overflow: hidden; }}
[data-testid="stExpander"] details {{ border-radius: 10px; border-color: {BORDER}; background: #FFFFFF; }}
[data-testid="stAlert"] {{ border-radius: 10px; }}

/* sidebar */
[data-testid="stSidebar"] {{ border-right: 1px solid {BORDER}; }}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h2,
[data-testid="stSidebar"] h2 {{ font-size: 1.08rem; font-weight: 700; color: {INK}; }}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h3,
[data-testid="stSidebar"] h3 {{ font-size: .76rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: .07em; color: {MUTED}; padding: .2rem 0 .1rem 0; }}
[data-testid="stSidebar"] hr {{ margin: .8rem 0; border-color: {BORDER}; }}

/* buttons */
.stButton > button, .stDownloadButton > button {{ border-radius: 8px; font-weight: 600;
    border: 1px solid {BORDER}; background: #FFFFFF; }}
.stButton > button:hover, .stDownloadButton > button:hover {{ border-color: {PRIMARY}; color: {PRIMARY}; }}
.stButton > button[kind="primary"] {{ background: {PRIMARY}; border-color: {PRIMARY}; color: #FFFFFF; }}
.stButton > button[kind="primary"]:hover {{ color: #FFFFFF; filter: brightness(1.12); }}

/* tabs */
[data-baseweb="tab-list"] {{ gap: 1.5rem; border-bottom: 1px solid {BORDER}; }}
[data-baseweb="tab"] {{ font-weight: 600; }}
</style>
"""


def apply():
    """Inject the stylesheet. Call once, straight after st.set_page_config."""
    st.markdown(_CSS, unsafe_allow_html=True)


def page_header(title: str, subtitle: str):
    """Title band at the top of every view."""
    st.markdown(
        f'<div class="fyp-header"><div><div class="title">{title}</div>'
        f'<div class="sub">{subtitle}</div></div></div>',
        unsafe_allow_html=True)
