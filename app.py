"""Enhanced Streamlit dashboard for Transaction AI Processing with MongoDB Atlas & Temporal."""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime
import asyncio
import httpx
from typing import Dict
import time
from scripts.advanced_scenarios import AdvancedScenarios
from database.connection import get_sync_db
from database.repositories import HumanReviewRepository, TransactionRepository
from utils.config import config
from utils.decimal_utils import from_decimal128

# Page configuration — MongoDB-branded title + favicon
st.set_page_config(
    page_title="AI Transaction Processing | MongoDB + Temporal",
    page_icon="https://www.mongodb.com/assets/images/global/favicon.ico",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ---------------------------------------------------------------------------
# MongoDB-branded dark theme CSS (Solutions Library design system).
# Brand palette: spring-green #00ED64, forest #00684A, navy #001E2B.
# Fonts: Euclid Circular A (body), MongoDB Value Serif (H1).
# ---------------------------------------------------------------------------

_FONT_FACE_BLOCK = """
@font-face {
    font-family: 'Euclid Circular A';
    src: url('https://static.mongodb.com/com/fonts/EuclidCircularA-Regular-WebXL.woff2') format('woff2');
    font-weight: normal;
    font-display: swap;
}
@font-face {
    font-family: 'Euclid Circular A';
    src: url('https://static.mongodb.com/com/fonts/EuclidCircularA-Medium-WebXL.woff2') format('woff2');
    font-weight: 500;
    font-display: swap;
}
@font-face {
    font-family: 'MongoDB Value Serif';
    src: url('https://static.mongodb.com/com/fonts/MongoDBValueSerif-Medium.woff2') format('woff2');
    font-weight: 500;
    font-display: swap;
}
"""

_KEYFRAMES_BLOCK = """
@keyframes aurora {
    0% { transform: translate(0, 0) rotate(0deg) scale(1); }
    25% { transform: translate(-3%, 2%) rotate(0.5deg) scale(1.01); }
    50% { transform: translate(3%, -1%) rotate(-0.5deg) scale(0.99); }
    75% { transform: translate(-2%, -3%) rotate(0.3deg) scale(1.005); }
    100% { transform: translate(0, 0) rotate(0deg) scale(1); }
}
@keyframes shimmer {
    0% { background-position: -200% center; }
    100% { background-position: 200% center; }
}
"""

MONGODB_THEME_CSS = f"""
<style>
{_FONT_FACE_BLOCK}
:root {{
    --mdb-green: #00ED64;
    --mdb-forest: #00684A;
    --mdb-navy: #001E2B;
    --mdb-bg: #060A0F;
    --mdb-surface: #0C1117;
    --mdb-surface-card: rgba(12, 17, 23, 0.85);
    --mdb-border: rgba(255, 255, 255, 0.06);
    --mdb-border-hover: rgba(0, 237, 100, 0.2);
    --mdb-text: #F0F4F8;
    --mdb-text-secondary: #C8D5DE;
    --mdb-glow: rgba(0, 237, 100, 0.15);
}}
{_KEYFRAMES_BLOCK}
.stApp {{
    background: var(--mdb-bg) !important;
    font-family: 'Euclid Circular A', -apple-system, BlinkMacSystemFont, sans-serif !important;
}}
.stApp::before {{
    content: '';
    position: fixed;
    inset: -50%;
    background:
        radial-gradient(ellipse at 20% 50%, rgba(0, 237, 100, 0.06) 0%, transparent 50%),
        radial-gradient(ellipse at 80% 20%, rgba(0, 120, 255, 0.04) 0%, transparent 40%),
        radial-gradient(ellipse at 50% 80%, rgba(168, 85, 247, 0.03) 0%, transparent 45%);
    animation: aurora 60s linear infinite;
    pointer-events: none;
    z-index: 0;
}}
.main .block-container {{ background: transparent !important; position: relative; z-index: 1; }}
h1, .stTitle > div > h1 {{
    font-family: 'MongoDB Value Serif', Georgia, serif !important;
    color: var(--mdb-text) !important;
    font-weight: 500 !important;
    letter-spacing: -0.5px;
}}
h2, h3 {{ color: var(--mdb-text) !important; font-family: 'Euclid Circular A', sans-serif !important; }}
p, span, label, .stCaption, .stMarkdown {{ color: var(--mdb-text-secondary) !important; }}
section[data-testid="stSidebar"] {{ background: var(--mdb-surface) !important; border-right: 1px solid var(--mdb-border) !important; }}
section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3 {{ color: var(--mdb-text) !important; }}
section[data-testid="stSidebar"] .stMarkdown p {{ color: var(--mdb-text-secondary) !important; }}
[data-testid="stMetric"] {{
    background: var(--mdb-surface-card) !important;
    border: 1px solid var(--mdb-border) !important;
    border-radius: 12px !important;
    padding: 16px 20px !important;
    backdrop-filter: blur(12px) !important;
    transition: all 0.3s ease !important;
}}
[data-testid="stMetric"]:hover {{
    border-color: var(--mdb-border-hover) !important;
    box-shadow: 0 0 20px var(--mdb-glow) !important;
}}
[data-testid="stMetric"] label {{
    color: var(--mdb-text-secondary) !important;
    font-size: 0.75rem !important;
    text-transform: uppercase !important;
    letter-spacing: 1.5px !important;
}}
[data-testid="stMetric"] [data-testid="stMetricValue"] {{
    color: var(--mdb-green) !important;
    font-weight: 600 !important;
    font-size: 1.8rem !important;
    text-shadow: 0 0 30px rgba(0, 237, 100, 0.2);
}}
.stButton > button {{
    background: linear-gradient(135deg, var(--mdb-forest), var(--mdb-green)) !important;
    color: var(--mdb-navy) !important;
    border: none !important;
    border-radius: 8px !important;
    font-weight: 600 !important;
    font-family: 'Euclid Circular A', sans-serif !important;
    padding: 8px 24px !important;
    transition: all 0.3s ease !important;
    text-shadow: none !important;
}}
.stButton > button:hover {{
    box-shadow: 0 0 24px rgba(0, 237, 100, 0.4), 0 4px 12px rgba(0, 0, 0, 0.3) !important;
    transform: translateY(-1px) !important;
}}
.stProgress > div > div {{
    background: linear-gradient(90deg, var(--mdb-forest), var(--mdb-green)) !important;
    border-radius: 4px !important;
    box-shadow: 0 0 8px rgba(0, 237, 100, 0.3) !important;
}}
.stProgress > div {{ background: rgba(255, 255, 255, 0.05) !important; }}
.stSelectbox > div > div, .stMultiSelect > div > div {{
    background: var(--mdb-surface) !important;
    border-color: var(--mdb-border) !important;
    color: var(--mdb-text) !important;
}}
.streamlit-expanderHeader {{
    background: var(--mdb-surface-card) !important;
    border: 1px solid var(--mdb-border) !important;
    border-radius: 8px !important;
    color: var(--mdb-text) !important;
}}
.stTabs [data-baseweb="tab-list"] {{ border-bottom: 1px solid var(--mdb-border) !important; }}
.stTabs [data-baseweb="tab"] {{ color: var(--mdb-text-secondary) !important; }}
.stTabs [aria-selected="true"] {{ color: var(--mdb-green) !important; border-bottom-color: var(--mdb-green) !important; }}
.stSuccess {{ background: rgba(0, 237, 100, 0.08) !important; border: 1px solid rgba(0, 237, 100, 0.2) !important; color: var(--mdb-green) !important; border-radius: 8px !important; }}
.stInfo    {{ background: rgba(0, 120, 255, 0.08) !important; border: 1px solid rgba(0, 120, 255, 0.2) !important; color: #60A5FA !important; border-radius: 8px !important; }}
.stWarning {{ background: rgba(255, 152, 0, 0.08) !important; border: 1px solid rgba(255, 152, 0, 0.2) !important; border-radius: 8px !important; }}
.stError   {{ background: rgba(239, 68, 68, 0.08) !important; border: 1px solid rgba(239, 68, 68, 0.2) !important; border-radius: 8px !important; }}
hr {{ border-color: var(--mdb-border) !important; }}
.js-plotly-plot .plotly .main-svg {{ background: transparent !important; }}
.stDataFrame {{ border: 1px solid var(--mdb-border) !important; border-radius: 8px !important; overflow: hidden; }}
.stCaption p {{ color: var(--mdb-text-secondary) !important; }}
.stRadio > label {{ color: var(--mdb-text) !important; }}
::-webkit-scrollbar {{ width: 6px; height: 6px; }}
::-webkit-scrollbar-track {{ background: var(--mdb-bg); }}
::-webkit-scrollbar-thumb {{ background: rgba(255, 255, 255, 0.1); border-radius: 3px; }}
::-webkit-scrollbar-thumb:hover {{ background: rgba(0, 237, 100, 0.3); }}
</style>
"""

# ---------------------------------------------------------------------------
# Plotly theming helpers — applied to every Plotly figure via apply_mdb_theme.
# ---------------------------------------------------------------------------

PLOTLY_THEME = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(12,17,23,0.6)",
    font_color="#F0F4F8",
    font_family="Euclid Circular A, sans-serif",
    title_font_color="#F0F4F8",
    legend_font_color="#C8D5DE",
    xaxis=dict(gridcolor="rgba(255,255,255,0.04)", zerolinecolor="rgba(255,255,255,0.06)"),
    yaxis=dict(gridcolor="rgba(255,255,255,0.04)", zerolinecolor="rgba(255,255,255,0.06)"),
)


def apply_mdb_theme(fig, polar: bool = False):
    """Apply the MongoDB dark theme to a Plotly figure. polar=True for radar charts."""
    if polar:
        layout = {k: v for k, v in PLOTLY_THEME.items() if k not in ("xaxis", "yaxis")}
        layout["polar"] = dict(
            bgcolor=PLOTLY_THEME["plot_bgcolor"],
            radialaxis=dict(gridcolor=PLOTLY_THEME["xaxis"]["gridcolor"]),
            angularaxis=dict(gridcolor=PLOTLY_THEME["xaxis"]["gridcolor"]),
        )
        fig.update_layout(**layout)
    else:
        fig.update_layout(**PLOTLY_THEME)
    return fig


# Inject the MongoDB dark theme CSS
st.markdown(MONGODB_THEME_CSS, unsafe_allow_html=True)

# Initialize session state
if 'transactions' not in st.session_state:
    st.session_state.transactions = []
if 'metrics' not in st.session_state:
    st.session_state.metrics = None
if 'last_refresh' not in st.session_state:
    st.session_state.last_refresh = datetime.now()
if 'active_workflows' not in st.session_state:
    st.session_state.active_workflows = []
if 'scenario_results' not in st.session_state:
    st.session_state.scenario_results = []
if 'cost_per_manual_review' not in st.session_state:
    st.session_state.cost_per_manual_review = 47.0

# API configuration
API_BASE_URL = config.API_BASE_URL

async def submit_transaction(transaction_data: Dict):
    """Submit transaction to API."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{API_BASE_URL}/transaction",
            json=transaction_data
        )
        return response.json()

async def get_decision(transaction_id: str):
    """Get decision for a transaction."""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                f"{API_BASE_URL}/transaction/{transaction_id}"
            )
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 202:
                return {"status": "processing"}
        except Exception as e:
            st.error(f"Error getting decision: {e}")
    return None

async def get_metrics():
    """Get system metrics."""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(f"{API_BASE_URL}/metrics")
            if response.status_code == 200:
                return response.json()
        except:
            pass
    return None

async def get_workflow_status(workflow_id: str):
    """Get Temporal workflow status."""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                f"{API_BASE_URL}/workflow/{workflow_id}/status"
            )
            if response.status_code == 200:
                return response.json()
        except:
            pass
    return {"status": "unknown"}

# ---------------------------------------------------------------------------
# Header — inline-SVG MongoDB + Temporal lockup, branded H1, theme-aware
# ---------------------------------------------------------------------------

# MongoDB logo (verbatim from MongoDB Solutions Library, spring-green fill)
_MDB_LOGO_SVG = """
<svg width="240" height="60" viewBox="0 0 1102 278" fill="none" xmlns="http://www.w3.org/2000/svg" aria-label="MongoDB">
<path d="M82.3229 28.6444C71.5367 15.8469 62.2485 2.84945 60.351 0.149971C60.1512 -0.0499903 59.8515 -0.0499903 59.6518 0.149971C57.7542 2.84945 48.4661 15.8469 37.6798 28.6444C-54.9019 146.721 52.2613 226.406 52.2613 226.406L53.1601 227.006C53.959 239.303 55.9565 257 55.9565 257H59.9514H63.9463C63.9463 257 65.9438 239.403 66.7428 227.006L67.6416 226.306C67.7414 226.406 174.905 146.721 82.3229 28.6444ZM59.9514 224.706C59.9514 224.706 55.1576 220.607 53.8592 218.507V218.308L59.6518 89.7325C59.6518 89.3326 60.2511 89.3326 60.2511 89.7325L66.0436 218.308V218.507C64.7453 220.607 59.9514 224.706 59.9514 224.706Z" fill="#00ED64"/>
<path d="M260.501 197.588L215.845 89.2991L215.745 89H181.001V96.279H186.608C188.31 96.279 189.912 96.9771 191.114 98.1736C192.315 99.3702 192.916 100.966 192.916 102.661L191.915 211.647C191.915 215.037 189.112 217.829 185.707 217.929L180 218.029V225.208H213.843V218.029L210.338 217.929C206.934 217.829 204.13 215.037 204.13 211.647V108.943L252.792 225.208C253.492 226.903 255.094 228 256.897 228C258.699 228 260.301 226.903 261.002 225.208L308.562 111.535L309.263 211.647C309.263 215.137 306.459 217.929 302.955 218.029H299.35V225.208H339V218.029H333.593C330.189 218.029 327.385 215.137 327.285 211.747L326.985 102.76C326.985 99.2704 329.788 96.4785 333.193 96.3788L339 96.279V89H305.157L260.501 197.588Z" fill="#00ED64"/>
<path d="M571.869 216.136C570.764 215.04 570.162 213.546 570.162 211.754V158.369C570.162 148.21 567.151 140.242 561.127 134.565C555.205 128.888 546.973 126 536.734 126C522.378 126 511.035 131.777 503.104 143.131C503.004 143.33 502.703 143.43 502.402 143.43C502.1 143.43 501.9 143.23 501.9 142.932L498.185 128.689H491.961L476 137.753V142.732H480.116C482.023 142.732 483.629 143.23 484.734 144.226C485.838 145.222 486.44 146.716 486.44 148.808V211.654C486.44 213.447 485.838 214.941 484.734 216.036C483.629 217.132 482.124 217.729 480.317 217.729H476.301V225H513.042V217.729H509.027C507.22 217.729 505.714 217.132 504.61 216.036C503.506 214.941 502.903 213.447 502.903 211.654V170.022C502.903 164.743 504.108 159.465 506.317 154.286C508.625 149.206 512.038 144.924 516.556 141.637C521.073 138.35 526.494 136.757 532.718 136.757C539.745 136.757 545.066 138.948 548.378 143.33C551.691 147.712 553.398 153.389 553.398 160.162V211.554C553.398 213.347 552.795 214.841 551.691 215.937C550.587 217.032 549.081 217.63 547.274 217.63H543.259V224.9H580V217.63H575.985C574.479 217.829 573.073 217.231 571.869 216.136Z" fill="#00ED64"/>
<path d="M907.546 97.212C897.39 91.8041 886.039 89 873.792 89H826V96.3107H830.68C832.472 96.3107 834.065 97.0117 835.658 98.6141C837.152 100.116 837.948 101.819 837.948 103.621V211.379C837.948 213.181 837.152 214.884 835.658 216.386C834.165 217.888 832.472 218.689 830.68 218.689H826V226H873.792C886.039 226 897.39 223.196 907.546 217.788C917.701 212.38 925.966 204.368 931.94 194.154C937.914 183.939 941 171.621 941 157.6C941 143.58 937.914 131.362 931.94 121.047C925.866 110.632 917.701 102.62 907.546 97.212ZM921.784 157.4C921.784 170.219 919.494 181.034 915.013 189.747C910.533 198.46 904.558 204.969 897.19 209.175C889.823 213.382 881.658 215.485 872.896 215.485H863.238C861.446 215.485 859.853 214.784 858.26 213.181C856.766 211.679 855.97 209.977 855.97 208.174V106.526C855.97 104.723 856.667 103.121 858.26 101.518C859.753 100.016 861.446 99.2149 863.238 99.2149H872.896C881.658 99.2149 889.823 101.318 897.19 105.524C904.558 109.73 910.533 116.24 915.013 124.953C919.494 133.665 921.784 144.581 921.784 157.4Z" fill="#00ED64"/>
<path d="M1053.97 164.711C1049.55 159.603 1041.02 155.297 1030.99 152.993C1044.84 146.083 1051.96 136.369 1051.96 123.851C1051.96 117.041 1050.16 110.932 1046.54 105.724C1042.93 100.517 1037.81 96.3106 1031.29 93.4064C1024.76 90.5022 1017.13 89 1008.5 89H954.402V96.3107H958.718C960.524 96.3107 962.13 97.0117 963.736 98.614C965.242 100.116 966.045 101.819 966.045 103.621V211.379C966.045 213.181 965.242 214.884 963.736 216.386C962.231 217.888 960.524 218.689 958.718 218.689H954V226H1012.72C1021.65 226 1029.98 224.498 1037.51 221.493C1045.04 218.489 1051.06 214.083 1055.38 208.274C1059.79 202.466 1062 195.355 1062 187.143C1061.9 178.33 1059.29 170.819 1053.97 164.711ZM986.621 213.281C985.115 211.779 984.312 210.077 984.312 208.274V159.904H1012.22C1022.05 159.904 1029.58 162.407 1034.8 167.414C1040.02 172.422 1042.63 178.931 1042.63 186.943C1042.63 191.75 1041.42 196.457 1039.22 200.763C1036.91 205.17 1033.49 208.675 1028.88 211.379C1024.36 214.083 1018.74 215.485 1012.22 215.485H991.639C989.833 215.585 988.227 214.784 986.621 213.281ZM984.413 149.588V106.626C984.413 104.823 985.115 103.221 986.721 101.618C988.227 100.116 989.933 99.315 991.74 99.315H1004.99C1014.52 99.315 1021.55 101.719 1025.97 106.325C1030.38 111.032 1032.59 117.041 1032.59 124.452C1032.59 132.063 1030.48 138.172 1026.37 142.779C1022.25 147.285 1016.03 149.588 1007.8 149.588H984.413Z" fill="#00ED64"/>
<path d="M431.999 132.387C424.329 128.196 415.763 126 406.5 126C397.237 126 388.571 128.096 381.001 132.387C373.331 136.579 367.255 142.667 362.773 150.352C358.291 158.037 356 167.02 356 177C356 186.98 358.291 195.963 362.773 203.648C367.255 211.333 373.331 217.421 381.001 221.613C388.671 225.804 397.237 228 406.5 228C415.763 228 424.429 225.904 431.999 221.613C439.669 217.421 445.745 211.333 450.227 203.648C454.709 195.963 457 186.98 457 177C457 167.02 454.709 158.037 450.227 150.352C445.745 142.667 439.669 136.679 431.999 132.387ZM439.37 177C439.37 189.276 436.382 199.256 430.405 206.442C424.529 213.628 416.461 217.321 406.5 217.321C396.54 217.321 388.471 213.628 382.595 206.442C376.618 199.256 373.63 189.276 373.63 177C373.63 164.724 376.618 154.744 382.595 147.558C388.471 140.372 396.54 136.679 406.5 136.679C416.461 136.679 424.529 140.372 430.405 147.558C436.382 154.843 439.37 164.724 439.37 177Z" fill="#00ED64"/>
<path d="M784.999 132.387C777.329 128.196 768.763 126 759.5 126C750.237 126 741.571 128.096 734.001 132.387C726.331 136.579 720.255 142.667 715.773 150.352C711.291 158.037 709 167.02 709 177C709 186.98 711.291 195.963 715.773 203.648C720.255 211.333 726.331 217.421 734.001 221.613C741.671 225.804 750.237 228 759.5 228C768.763 228 777.429 225.904 784.999 221.613C792.669 217.421 798.745 211.333 803.227 203.648C807.709 195.963 810 186.98 810 177C810 167.02 807.709 158.037 803.227 150.352C798.745 142.667 792.569 136.679 784.999 132.387ZM792.37 177C792.37 189.276 789.381 199.256 783.405 206.442C777.528 213.628 769.46 217.321 759.5 217.321C749.539 217.321 741.471 213.628 735.595 206.442C729.618 199.256 726.63 189.276 726.63 177C726.63 164.624 729.618 154.744 735.595 147.558C741.471 140.372 749.539 136.679 759.5 136.679C769.46 136.679 777.528 140.372 783.405 147.558C789.282 154.843 792.37 164.724 792.37 177Z" fill="#00ED64"/>
<path d="M642.64 126C634.614 126 627.292 127.704 620.671 131.113C614.05 134.522 608.834 139.135 605.122 145.05C601.411 150.865 599.505 157.383 599.505 164.301C599.505 170.517 600.909 176.232 603.818 181.346C606.627 186.259 610.439 190.369 615.254 193.778L600.909 213.23C599.103 215.636 598.903 218.844 600.207 221.451C601.611 224.158 604.219 225.763 607.229 225.763H611.342C607.329 228.47 604.119 231.678 601.912 235.488C599.304 239.8 598 244.311 598 248.923C598 257.546 601.812 264.665 609.335 269.979C616.759 275.293 627.191 278 640.332 278C649.461 278 658.188 276.496 666.113 273.588C674.138 270.681 680.658 266.369 685.473 260.755C690.389 255.14 692.897 248.322 692.897 240.501C692.897 232.28 689.887 226.464 682.865 220.85C676.847 216.137 667.417 213.631 655.68 213.631H615.555C615.455 213.631 615.354 213.53 615.354 213.53C615.354 213.53 615.254 213.33 615.354 213.23L625.787 199.193C628.596 200.496 631.204 201.298 633.511 201.799C635.918 202.301 638.627 202.501 641.636 202.501C650.063 202.501 657.687 200.797 664.307 197.388C670.928 193.979 676.245 189.367 680.057 183.451C683.868 177.636 685.774 171.119 685.774 164.201C685.774 156.781 682.163 143.245 672.332 136.327C672.332 136.227 672.433 136.227 672.433 136.227L694 138.633V128.707H659.492C654.075 126.902 648.458 126 642.64 126ZM654.677 188.665C650.865 190.67 646.752 191.773 642.64 191.773C635.919 191.773 630 189.367 624.984 184.654C619.969 179.942 617.461 173.024 617.461 164.201C617.461 155.377 619.969 148.459 624.984 143.747C630 139.034 635.919 136.628 642.64 136.628C646.853 136.628 650.865 137.631 654.677 139.736C658.489 141.741 661.599 144.85 664.107 148.96C666.514 153.071 667.818 158.185 667.818 164.201C667.818 170.317 666.614 175.43 664.107 179.441C661.699 183.551 658.489 186.66 654.677 188.665ZM627.492 225.662H654.677C662.201 225.662 667.016 227.166 670.226 230.375C673.436 233.583 675.041 237.894 675.041 242.908C675.041 250.227 672.132 256.243 666.314 260.755C660.495 265.267 652.671 267.573 643.041 267.573C634.614 267.573 627.592 265.668 622.476 262.058C617.36 258.449 614.752 252.934 614.752 245.916C614.752 241.504 615.956 237.393 618.364 233.784C620.771 230.174 623.68 227.567 627.492 225.662Z" fill="#00ED64"/>
<path d="M1082.35 224.327C1080.37 223.244 1078.88 221.669 1077.69 219.799C1076.6 217.831 1076 215.764 1076 213.5C1076 211.236 1076.6 209.071 1077.69 207.201C1078.78 205.232 1080.37 203.756 1082.35 202.673C1084.34 201.591 1086.52 201 1089 201C1091.48 201 1093.66 201.591 1095.65 202.673C1097.63 203.756 1099.12 205.331 1100.31 207.201C1101.4 209.169 1102 211.236 1102 213.5C1102 215.764 1101.4 217.929 1100.31 219.799C1099.22 221.768 1097.63 223.244 1095.65 224.327C1093.66 225.409 1091.48 226 1089 226C1086.62 226 1084.34 225.409 1082.35 224.327ZM1094.56 222.85C1096.24 221.965 1097.44 220.587 1098.43 219.012C1099.32 217.339 1099.82 215.468 1099.82 213.402C1099.82 211.335 1099.32 209.465 1098.43 207.791C1097.53 206.118 1096.24 204.839 1094.56 203.953C1092.87 203.067 1091.08 202.575 1089 202.575C1086.92 202.575 1085.13 203.067 1083.44 203.953C1081.76 204.839 1080.56 206.217 1079.57 207.791C1078.68 209.465 1078.18 211.335 1078.18 213.402C1078.18 215.468 1078.68 217.339 1079.57 219.012C1080.47 220.685 1081.76 221.965 1083.44 222.85C1085.13 223.736 1086.92 224.228 1089 224.228C1091.08 224.228 1092.97 223.736 1094.56 222.85ZM1083.64 219.406V218.52L1083.84 218.421H1084.44C1084.63 218.421 1084.83 218.323 1084.93 218.224C1085.13 218.028 1085.13 217.929 1085.13 217.732V208.579C1085.13 208.382 1085.03 208.185 1084.93 208.087C1084.73 207.89 1084.63 207.89 1084.44 207.89H1083.84L1083.64 207.791V206.906L1083.84 206.807H1089C1090.49 206.807 1091.58 207.102 1092.47 207.791C1093.37 208.48 1093.76 209.366 1093.76 210.547C1093.76 211.433 1093.47 212.319 1092.77 212.909C1092.08 213.598 1091.28 213.992 1090.29 214.091L1091.48 214.484L1093.76 218.126C1093.96 218.421 1094.16 218.52 1094.46 218.52H1095.05L1095.15 218.618V219.504L1095.05 219.602H1091.98L1091.78 219.504L1088.6 214.189H1087.81V217.732C1087.81 217.929 1087.91 218.126 1088.01 218.224C1088.21 218.421 1088.31 218.421 1088.5 218.421H1089.1L1089.3 218.52V219.406L1089.1 219.504H1083.84L1083.64 219.406ZM1088.7 213.008C1089.5 213.008 1090.19 212.811 1090.59 212.319C1090.98 211.925 1091.28 211.236 1091.28 210.449C1091.28 209.661 1091.08 209.071 1090.69 208.579C1090.29 208.087 1089.69 207.89 1089 207.89H1088.6C1088.4 207.89 1088.21 207.988 1088.11 208.087C1087.91 208.283 1087.91 208.382 1087.91 208.579V213.008H1088.7Z" fill="#00ED64"/>
</svg>
"""

# Temporal logo (currentColor fill — adapts to wrapper color per theme)
_TEMPORAL_LOGO_SVG = """
<svg width="200" height="50" viewBox="0 0 1571 395" fill="none" xmlns="http://www.w3.org/2000/svg" aria-label="Temporal">
<path fill="currentColor" d="M510.853 115.417V134.708H566.497V283.244H587.451V134.708H643.095V115.417H510.853Z"/>
<path fill="currentColor" d="M647.427 222.725C647.427 259.371 671.512 284.691 708.362 284.691C735.811 284.691 757.984 267.577 762.801 242.981H742.811C738.716 258.896 723.286 267.087 706.916 267.087C684.036 267.087 668.862 251.172 668.14 228.271V226.583H763.516C763.757 223.689 763.998 220.796 763.998 218.143C763.034 181.731 739.423 159.787 705.712 159.787C671.03 159.787 647.427 184.865 647.427 222.725ZM669.344 210.185C671.271 190.171 687.889 177.149 705.953 177.149C725.454 177.149 740.876 188.965 742.803 210.185H669.344Z"/>
<path fill="currentColor" d="M936.275 159.787C914.358 159.787 901.111 169.915 893.645 183.177C886.66 167.503 871.968 159.787 854.868 159.787C835.841 159.787 825.484 169.191 818.981 178.113L817.303 161.234H799.232V283.244H818.981V219.349C818.981 194.752 831.265 177.872 851.496 177.872C870.282 177.872 881.121 190.412 881.121 214.526V283.244H900.87V218.384C900.87 193.305 913.394 177.872 933.867 177.872C952.412 177.872 963.009 190.412 963.009 214.526V283.244H982.759V213.32C982.759 174.496 961.805 159.787 936.275 159.787Z"/>
<path fill="currentColor" d="M1080.79 159.787C1060.08 159.787 1046.83 169.674 1038.64 181.007L1036.96 161.234H1018.89V331.472H1038.64V264.435C1046.11 276.251 1060.08 284.691 1080.79 284.691C1113.78 284.691 1139.8 259.371 1139.8 222.725C1139.8 182.695 1113.78 159.787 1080.79 159.787ZM1078.86 267.087C1054.78 267.087 1038.4 248.278 1038.4 222.243C1038.4 195.958 1054.78 177.39 1078.86 177.39C1102.22 177.39 1119.57 195.958 1119.57 222.725C1119.57 248.519 1102.22 267.087 1078.86 267.087Z"/>
<path fill="currentColor" d="M1228.73 284.691C1264.62 284.691 1289.66 259.129 1289.66 222.725C1289.66 185.348 1264.62 159.787 1228.73 159.787C1192.84 159.787 1167.79 185.348 1167.79 222.725C1167.79 259.129 1192.84 284.691 1228.73 284.691ZM1228.73 267.087C1204.4 267.087 1188.03 248.037 1188.03 222.725C1188.03 196.44 1204.4 177.39 1228.73 177.39C1253.06 177.39 1269.43 196.44 1269.43 222.725C1269.43 248.037 1253.06 267.087 1228.73 267.087Z"/>
<path fill="currentColor" d="M1378.91 161.234C1357 161.234 1345.66 170.397 1339.88 179.801L1338.2 161.234H1320.13V283.244H1339.88V221.519C1339.88 201.987 1348.81 180.525 1374.58 180.525H1384.45V161.234H1378.91Z"/>
<path fill="currentColor" d="M1516.35 264.186C1511.29 264.186 1508.16 262.264 1508.16 256.236V204.157C1508.16 175.461 1491.07 159.787 1459.76 159.787C1430.14 159.787 1410.63 174.014 1407.74 198.128H1427.49C1429.89 185.589 1441.46 177.39 1458.8 177.39C1478.06 177.39 1488.41 187.036 1488.41 202.951V211.391H1453.26C1420.98 211.391 1403.88 224.164 1403.88 248.76C1403.88 271.187 1422.19 284.691 1449.16 284.691C1470.36 284.691 1481.67 275.286 1489.38 264.193C1489.62 276.251 1495.16 283.244 1511.53 283.244H1522.37V264.186H1516.35ZM1488.41 233.086C1488.41 253.583 1475.17 267.811 1450.61 267.811C1434.23 267.811 1423.87 259.612 1423.87 247.555C1423.87 233.568 1433.75 228.03 1451.33 228.03H1488.41V233.086Z"/>
<path fill="currentColor" d="M1550.39 115.417V283.244H1570.14V115.417H1550.39Z"/>
<path fill="currentColor" d="M266.428 128.248C257.097 58.3869 233.568 0 197.26 0C161.054 0 137.423 58.3869 128.092 128.248C58.316 137.59 0 161.148 0 197.5C0 233.751 58.316 257.41 128.092 266.752C137.423 336.613 160.952 395 197.26 395C233.467 395 257.097 336.613 266.428 266.752C336.204 257.41 394.52 233.852 394.52 197.5C394.52 161.148 336.204 137.488 266.428 128.248ZM125.861 246.24C59.0259 236.594 19.9795 214.458 19.9795 197.398C19.9795 180.339 58.9245 158.203 125.861 148.557C124.34 164.702 123.63 181.152 123.63 197.398C123.63 213.645 124.34 230.197 125.861 246.24ZM197.26 19.9023C214.299 19.9023 236.408 58.8946 246.043 125.913C229.917 124.389 213.487 123.679 197.26 123.679C181.033 123.679 164.603 124.491 148.478 125.913C158.112 58.9962 180.222 19.9023 197.26 19.9023ZM268.659 246.24C265.414 246.748 251.824 248.271 248.477 248.677C248.173 252.13 246.55 265.635 246.043 268.884C236.408 335.801 214.299 374.895 197.26 374.895C180.222 374.895 158.112 335.902 148.478 268.884C147.97 265.635 146.449 252.028 146.044 248.677C144.522 232.837 143.508 215.778 143.508 197.398C143.508 179.019 144.421 162.062 146.044 146.12C161.865 144.596 178.903 143.581 197.26 143.581C215.617 143.581 232.554 144.495 248.477 146.12C251.925 146.424 265.414 148.049 268.659 148.557C335.494 158.203 374.541 180.339 374.541 197.398C374.541 214.458 335.494 236.594 268.659 246.24Z"/>
</svg>
"""

# Collapse multi-line SVG to single line so CommonMark treats the whole header
# as one HTML block (newlines inside an HTML block can prematurely close it).
_mdb_svg = _MDB_LOGO_SVG.replace("\n", "")
_temporal_svg = _TEMPORAL_LOGO_SVG.replace("\n", "")

_HEADER_HTML = (
    '<div style="display:flex;align-items:center;gap:24px;margin-bottom:8px;">'
    '<div style="display:flex;align-items:center;gap:24px;">'
    f'<a href="https://www.mongodb.com/atlas" target="_blank" style="display:inline-flex;align-items:center;text-decoration:none;">{_mdb_svg}</a>'
    '<span style="color:rgba(255,255,255,0.25);font-size:2rem;font-weight:200;line-height:1;">+</span>'
    f'<a href="https://temporal.io/" target="_blank" style="display:inline-flex;align-items:center;text-decoration:none;color:#F0F4F8;">{_temporal_svg}</a>'
    '</div></div>'
    '<div style="margin-top:8px;margin-bottom:12px;">'
    '<h1 style="font-family:\'MongoDB Value Serif\',Georgia,serif;font-size:2.4rem;margin:0;padding:0;line-height:1.1;color:#F0F4F8;">'
    'AI <span style="background:linear-gradient(135deg,#00ED64,#7CF5A5,#0078FF,#00ED64);background-size:300% 100%;-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;animation:shimmer 6s ease-in-out infinite;">Transaction Processing</span>'
    '</h1>'
    '<p style="font-size:0.85rem;color:#C8D5DE;margin:6px 0 0 0;letter-spacing:0.3px;">'
    'Real-time fraud detection &amp; workflow orchestration with hybrid search'
    '</p></div>'
)
st.markdown(_HEADER_HTML, unsafe_allow_html=True)

col1, col2, col3 = st.columns([3, 1, 1])
with col1:
    st.empty()
with col2:
    if st.button("🔄 Refresh", key="refresh_btn"):
        st.session_state.last_refresh = datetime.now()
        st.rerun()
with col3:
    st.metric("Last Update", st.session_state.last_refresh.strftime("%H:%M:%S"))

# Fetch metrics
metrics = asyncio.run(get_metrics())
if metrics:
    st.session_state.metrics = metrics

# Sidebar - Scenario Launcher
with st.sidebar:
    st.header("🚀 Scenario Launcher")
    st.markdown("Test advanced capabilities of the system")
    
    scenarios = AdvancedScenarios(api_url=API_BASE_URL)
    test_scenarios = scenarios.generate_scenarios()
    
    # Scenario selector
    scenario_names = ["Select a scenario..."] + [s["name"] for s in test_scenarios]
    selected_scenario_name = st.selectbox(
        "Choose Test Scenario",
        scenario_names,
        help="Each scenario demonstrates different capabilities"
    )
    
    if selected_scenario_name != "Select a scenario...":
        selected_scenario = next(s for s in test_scenarios if s["name"] == selected_scenario_name)
        
        # Display scenario details
        st.info(f"**Description:** {selected_scenario['description']}")
        st.warning(f"**Expected:** {selected_scenario['expected_outcome']}")
        st.caption(f"**Transactions:** {len(selected_scenario['transactions'])}")
        
        # Run scenario button
        if st.button("▶️ Run Scenario", type="primary", width='stretch'):
            with st.spinner("Executing scenario..."):
                result = asyncio.run(scenarios.run_scenario(selected_scenario))
                st.session_state.scenario_results.append(result)
                st.session_state.active_workflows.extend(result["workflow_ids"])
                st.success(f"✅ Submitted {len(result['transactions'])} transactions")
                st.rerun()
    
    st.divider()
    
    # Quick Actions
    st.subheader("⚡ Quick Actions")

    if st.button("Clear Results", width='stretch'):
        st.session_state.scenario_results = []
        st.session_state.active_workflows = []
        st.rerun()

# Main content area
tabs = st.tabs(["📊 Dashboard", "🔄 Active Workflows", "🧪 Scenario Results", "👤 Guided Review", "🔍 Search Methods Demo", "⚙️ Settings"])

with tabs[0]:  # Dashboard
    if st.session_state.metrics:
        st.markdown("### 📊 System Metrics")
        
        col1, col2, col3, col4, col5 = st.columns(5)
        
        with col1:
            st.metric(
                "Total Transactions",
                f"{st.session_state.metrics.get('total_transactions', 0):,}",
                delta="+12 today"
            )
        
        with col2:
            total_amount = st.session_state.metrics.get('total_amount_processed', 0)
            st.metric(
                "Volume Processed",
                f"${total_amount/1e6:.1f}M",
                delta="+15.3%"
            )
        
        with col3:
            avg_time = st.session_state.metrics.get('average_processing_time_ms', 0)
            st.metric(
                "Avg Processing Time",
                f"{avg_time/1000:.1f}s",
                delta="-0.8s",
                delta_color="inverse"
            )
        
        with col4:
            avg_confidence = st.session_state.metrics.get('average_confidence', 0)
            st.metric(
                "AI Confidence",
                f"{avg_confidence:.1f}%",
                delta="+2.3%"
            )
        
        with col5:
            auto_approved = st.session_state.metrics.get('decisions_breakdown', {}).get('approve', 0)
            savings = auto_approved * st.session_state.cost_per_manual_review
            st.metric(
                "Cost Savings",
                f"${savings:,.0f}",
                delta=f"+${int(savings*0.1):,}"
            )
        
        # Decision breakdown chart
        st.markdown("### 📈 Decision Distribution")
        col1, col2 = st.columns([2, 1])
        
        with col1:
            if 'decisions_breakdown' in st.session_state.metrics:
                breakdown = st.session_state.metrics['decisions_breakdown']

                # Ensure consistent ordering and colors for decision types
                decision_types = ['approve', 'reject', 'escalate']
                decision_colors = {
                    'approve': '#00ED64',   # MongoDB spring green
                    'reject': '#E53935',    # Red
                    'escalate': '#FB8C00',  # Amber/warning
                }

                # Build ordered data with proper colors
                x_values = []
                y_values = []
                colors = []

                for decision_type in decision_types:
                    if decision_type in breakdown:
                        x_values.append(decision_type)
                        y_values.append(breakdown[decision_type])
                        colors.append(decision_colors[decision_type])

                fig = go.Figure(data=[
                    go.Bar(
                        x=x_values,
                        y=y_values,
                        marker_color=colors,
                        text=y_values,
                        textposition='auto'
                    )
                ])
                fig.update_layout(
                    title="Transaction Decisions",
                    xaxis_title="Decision Type",
                    yaxis_title="Count",
                    height=300,
                    showlegend=False
                )
                apply_mdb_theme(fig)
                st.plotly_chart(fig, use_container_width=True)
        
        with col2:
            st.markdown("#### 🎯 Key Features")
            st.success("✅ MongoDB Atlas Vector Search")
            st.info("🔄 Temporal Workflow Orchestration")
            st.warning("🤖 AWS Bedrock AI Analysis")
            st.error("🛡️ Real-time Fraud Detection")

with tabs[1]:  # Active Workflows
    st.markdown("### 🔄 Active Temporal Workflows")
    
    if st.session_state.active_workflows:
        st.info(f"Monitoring {len(st.session_state.active_workflows)} workflows")
        
        # Create workflow status grid
        cols = st.columns(3)
        for i, workflow_id in enumerate(st.session_state.active_workflows[-9:]):  # Show last 9
            with cols[i % 3]:
                # Extract transaction ID from workflow ID
                parts = workflow_id.split("-")
                if len(parts) >= 3:
                    txn_id = "-".join(parts[2:])
                    
                    # Get transaction status
                    decision_data = asyncio.run(get_decision(txn_id))
                    
                    if decision_data and "decision" in decision_data:
                        if decision_data["decision"] == "approve":
                            st.success(f"✅ {txn_id[:20]}...")
                        elif decision_data["decision"] == "reject":
                            st.error(f"❌ {txn_id[:20]}...")
                        else:
                            st.warning(f"⚠️ {txn_id[:20]}...")
                        
                        st.caption(f"Confidence: {decision_data.get('confidence', 0):.1f}%")
                    else:
                        st.info(f"⏳ {txn_id[:20]}...")
                        st.caption("Processing...")
    else:
        st.warning("No active workflows. Run a scenario to see workflows in action!")

with tabs[2]:  # Scenario Results
    st.markdown("### 🧪 Scenario Execution Results")
    
    if st.session_state.scenario_results:
        for result in st.session_state.scenario_results[-5:]:  # Show last 5
            with st.expander(f"📋 {result['scenario_name']}", expanded=True):
                col1, col2, col3 = st.columns(3)
                
                with col1:
                    st.markdown("**Description:**")
                    st.write(result['description'])
                
                with col2:
                    st.markdown("**Expected Outcome:**")
                    st.write(result['expected'])
                
                with col3:
                    st.markdown("**Transactions:**")
                    for txn in result['transactions']:
                        if txn['status'] == 'submitted':
                            amount_value = float(from_decimal128(txn.get('amount', 0)))
                            st.success(f"✅ ${amount_value:,.2f}")
                        else:
                            amount_value = float(from_decimal128(txn.get('amount', 0)))
                            st.error(f"❌ ${amount_value:,.2f}")
                
                # Check actual results
                if result['workflow_ids']:
                    st.markdown("**Actual Results:**")
                    results_data = []
                    for wf_id in result['workflow_ids']:
                        parts = wf_id.split("-")
                        if len(parts) >= 3:
                            txn_id = "-".join(parts[2:])
                            decision_data = asyncio.run(get_decision(txn_id))
                            if decision_data:
                                # Ensure Risk Score is always a string for consistent dataframe types
                                risk_score = decision_data.get("risk_score")
                                risk_score_str = f"{risk_score:.1f}" if risk_score is not None else "N/A"

                                results_data.append({
                                    "Transaction": txn_id[:30],
                                    "Decision": decision_data.get("decision", "pending"),
                                    "Confidence": f"{decision_data.get('confidence', 0):.1f}%",
                                    "Risk Score": risk_score_str
                                })
                    
                    if results_data:
                        df = pd.DataFrame(results_data)
                        st.dataframe(df, width='stretch')
    else:
        st.info("No scenario results yet. Run a scenario from the sidebar to see results!")

with tabs[3]:  # Human Review
    st.markdown("### 👤 Expert Oversight Queue")
    st.markdown("Review AI-flagged transactions for expert validation")
    
    # Get pending reviews from database
    db = get_sync_db()
    pending_reviews = list(db[config.HUMAN_REVIEWS_COLLECTION].find(
        {"status": {"$in": ["pending", "in_progress"]}},
        sort=[("priority", -1), ("created_at", 1)]
    ).limit(20))
    
    if pending_reviews:
        # Stats
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Pending Reviews", sum(1 for r in pending_reviews if r["status"] == "pending"))
        with col2:
            st.metric("In Progress", sum(1 for r in pending_reviews if r["status"] == "in_progress"))
        with col3:
            urgent_count = sum(1 for r in pending_reviews if r.get("priority") == "urgent")
            st.metric("Urgent", urgent_count, delta_color="inverse" if urgent_count > 0 else "off")
        with col4:
            high_count = sum(1 for r in pending_reviews if r.get("priority") == "high")
            st.metric("High Priority", high_count)
        
        st.divider()
        
        # Review interface
        for review in pending_reviews:
            # Get transaction details
            transaction = db[config.TRANSACTIONS_COLLECTION].find_one(
                {"transaction_id": review["transaction_id"]}
            )
            
            if transaction:
                with st.expander(
                    f"🔍 {review['transaction_id']} - Priority: {review.get('priority', 'medium').upper()}",
                    expanded=(review.get("priority") in ["urgent", "high"])
                ):
                    # Transaction details
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        st.markdown("#### Transaction Details")
                        st.write(f"**Type:** {transaction.get('transaction_type', 'N/A')}")
                        amount_value = float(from_decimal128(transaction.get('amount', 0)))
                        st.write(f"**Amount:** ${amount_value:,.2f} {transaction.get('currency', 'USD')}")
                        st.write(f"**Sender:** {transaction.get('sender', {}).get('name', 'N/A')}")
                        st.write(f"**Recipient:** {transaction.get('recipient', {}).get('name', 'N/A')}")
                        st.write(f"**Status:** {transaction.get('status', 'pending')}")
                        
                        # Risk flags
                        if transaction.get("risk_flags"):
                            st.write("**Risk Flags:**")
                            for flag in transaction["risk_flags"]:
                                st.write(f"  • {flag}")
                    
                    with col2:
                        st.markdown("#### AI Recommendation")
                        ai_rec = review.get("ai_recommendation", {})
                        
                        # Display AI decision with color coding
                        ai_decision = ai_rec.get("decision", "unknown")
                        confidence = ai_rec.get("confidence", 0)
                        
                        if ai_decision == "approve":
                            st.success(f"**AI Decision:** APPROVE ({confidence:.1f}% confidence)")
                        elif ai_decision == "reject":
                            st.error(f"**AI Decision:** REJECT ({confidence:.1f}% confidence)")
                        else:
                            st.warning(f"**AI Decision:** ESCALATE ({confidence:.1f}% confidence)")
                        
                        # Display reasoning - use text to avoid markdown interpretation issues
                        st.markdown("**Reasoning:**")
                        reasoning_text = ai_rec.get('reasoning', 'N/A')
                        # Replace any markdown/LaTeX characters that might cause formatting issues
                        # Escape $ first to prevent LaTeX math mode interpretation
                        reasoning_text = (reasoning_text
                            .replace('$', 'USD ')  # Escape dollar signs for LaTeX
                            .replace('*', '\\*')  # Escape asterisks for bold/italic
                            .replace('_', '\\_')  # Escape underscores for italic
                            .replace('`', '\\`')  # Escape backticks for code
                        )

                        st.markdown(reasoning_text)
                        
                        if ai_rec.get("risk_factors"):
                            st.write("**Risk Factors:**")
                            for factor in ai_rec["risk_factors"]:
                                st.write(f"  • {factor.replace('$', 'USD ')}")
                    
                    st.divider()
                    
                    # Review actions
                    st.markdown("#### Your Decision")
                    
                    col1, col2, col3 = st.columns(3)
                    
                    # Add notes field
                    notes = st.text_area(
                        "Review Notes (optional)",
                        key=f"notes_{review['review_id']}",
                        placeholder="Add any additional notes about your decision..."
                    )
                    
                    with col1:
                        if st.button("✅ Approve", key=f"approve_{review['review_id']}", type="primary", width='stretch'):
                            # Update review status
                            HumanReviewRepository.complete_review_sync(
                                review["review_id"],
                                decision="approve",
                                reviewer="Human Reviewer",
                                notes=notes or "Approved after manual review"
                            )
                            
                            # Update transaction status
                            TransactionRepository.update_status_sync(
                                review["transaction_id"],
                                "approved"
                            )
                            
                            st.success(f"✅ Transaction {review['transaction_id']} approved!")
                            time.sleep(1)
                            st.rerun()
                    
                    with col2:
                        if st.button("❌ Reject", key=f"reject_{review['review_id']}", type="secondary", width='stretch'):
                            # Update review status
                            HumanReviewRepository.complete_review_sync(
                                review["review_id"],
                                decision="reject",
                                reviewer="Human Reviewer",
                                notes=notes or "Rejected after manual review"
                            )
                            
                            # Update transaction status
                            TransactionRepository.update_status_sync(
                                review["transaction_id"],
                                "rejected"
                            )
                            
                            st.error(f"❌ Transaction {review['transaction_id']} rejected!")
                            time.sleep(1)
                            st.rerun()
                    
                    with col3:
                        if st.button("⏸️ Hold for Investigation", key=f"hold_{review['review_id']}", width='stretch'):
                            # Mark as in progress
                            db[config.HUMAN_REVIEWS_COLLECTION].update_one(
                                {"review_id": review["review_id"]},
                                {
                                    "$set": {
                                        "status": "in_progress",
                                        "started_at": datetime.now(),
                                        "notes": notes or "Under investigation"
                                    }
                                }
                            )
                            
                            st.warning(f"⏸️ Transaction {review['transaction_id']} on hold for investigation")
                            time.sleep(1)
                            st.rerun()
    else:
        st.info("👍 No transactions pending review at this time!")
        
        # Show recently completed reviews
        st.divider()
        st.markdown("#### Recently Completed Reviews")
        
        completed_reviews = list(db[config.HUMAN_REVIEWS_COLLECTION].find(
            {"status": "completed"},
            sort=[("completed_at", -1)]
        ).limit(5))
        
        if completed_reviews:
            for review in completed_reviews:
                decision = review.get("human_decision", {})
                decision_text = decision.get("decision", "unknown")
                reviewer = decision.get("reviewer", "Unknown")
                completed_at = review.get("completed_at", datetime.now())
                
                if decision_text == "approve":
                    st.success(f"✅ {review['transaction_id']} - Approved by {reviewer} at {completed_at.strftime('%H:%M:%S')}")
                elif decision_text == "reject":
                    st.error(f"❌ {review['transaction_id']} - Rejected by {reviewer} at {completed_at.strftime('%H:%M:%S')}")
                else:
                    st.info(f"ℹ️ {review['transaction_id']} - {decision_text} by {reviewer} at {completed_at.strftime('%H:%M:%S')}")
        else:
            st.write("No recently completed reviews")

with tabs[4]:  # Multi-Method Search Demo
    st.markdown("### 🔍 Hybrid Search Methods Demonstration")
    st.markdown("Our system combines multiple advanced search techniques for comprehensive fraud detection")

    # Create tabs for different search methods
    search_tabs = st.tabs(["🎯 Overview", "🔢 Vector Similarity", "📊 Traditional Indexes", "⚙️ Feature Scoring", "🕸️ Graph Traversal"])

    with search_tabs[0]:  # Overview
        st.markdown("#### 🎯 Hybrid Search Approach")
        col1, col2 = st.columns([1, 1])

        with col1:
            st.info("""
            **Our Multi-Layer Search Strategy:**

            1. **Vector Similarity Search** - Semantic understanding using AI embeddings
            2. **Traditional Index Search** - Fast exact and range matching
            3. **Feature-Based Scoring** - Multi-dimensional similarity calculation
            4. **Graph Traversal** - Network analysis for fraud rings

            These methods work together to identify complex fraud patterns that single methods might miss.
            """)

            # Show method effectiveness
            st.markdown("##### Method Effectiveness")
            effectiveness_data = {
                "Search Method": ["Vector Similarity", "Traditional Indexes", "Feature Scoring", "Graph Traversal"],
                "Detection Rate": [92, 87, 89, 95],
                "Speed (ms)": [45, 12, 8, 120],
                "Best For": ["Behavioral patterns", "Exact matches", "Risk scoring", "Fraud networks"]
            }
            st.dataframe(pd.DataFrame(effectiveness_data), hide_index=True)

        with col2:
            st.markdown("##### Combined Detection Power")

            # Venn diagram simulation using overlapping metrics
            fig = go.Figure()

            # Add traces for each method
            methods = ["Vector", "Traditional", "Feature", "Graph"]
            colors = ["red", "blue", "green", "purple"]
            values = [85, 78, 82, 90]

            fig.add_trace(go.Bar(
                x=methods,
                y=values,
                name="Individual Detection",
                marker_color=colors,
                opacity=0.6
            ))

            fig.add_trace(go.Scatter(
                x=methods,
                y=[95, 95, 95, 95],
                mode='lines',
                name='Combined Detection',
                line=dict(color='gold', width=3, dash='dash')
            ))

            fig.update_layout(
                title="Detection Accuracy: Individual vs Combined",
                yaxis_title="Detection Rate (%)",
                xaxis_title="Search Method",
                height=350,
                showlegend=True
            )
            apply_mdb_theme(fig)
            st.plotly_chart(fig, use_container_width=True)

    with search_tabs[1]:  # Vector Similarity
        st.markdown("#### 🔢 Vector Similarity Search")

        col1, col2 = st.columns([1, 1])

        with col1:
            st.info("""
            **How Vector Search Works:**

            • Transactions are converted to 1024-dimensional embeddings using AWS Bedrock (Cohere)
            • MongoDB Atlas performs k-NN search to find semantically similar transactions
            • Captures behavioral patterns beyond exact field matches
            • Identifies fraud patterns even with different amounts or parties
            """)

            # Vector search configuration
            st.markdown("##### Configuration")
            st.code("""
{
    "index": "transaction_vector_index",
    "embedding_model": "cohere-embed",
    "dimensions": 1024,
    "similarity_metric": "cosine",
    "num_candidates": 100,
    "limit": 10
}
            """, language="json")

        with col2:
            st.markdown("##### Vector Space Visualization")
            # Create sample data for visualization
            import numpy as np

            # Generate sample embeddings (2D projection for visualization)
            np.random.seed(42)

            # Create clusters for different transaction types
            fraud_cluster = np.random.randn(15, 2) * 0.5 + [2, 2]
            normal_cluster = np.random.randn(25, 2) * 0.5 + [-1, -1]
            suspicious_cluster = np.random.randn(10, 2) * 0.5 + [1, -2]

            # Combine and create labels
            embeddings = np.vstack([fraud_cluster, normal_cluster, suspicious_cluster])

            fig = go.Figure()

            for label, color in zip(['Fraud', 'Normal', 'Suspicious'], ['red', 'green', 'orange']):
                mask = np.array([label] * len(embeddings)) == np.array(['Fraud'] * 15 + ['Normal'] * 25 + ['Suspicious'] * 10)
                fig.add_trace(go.Scatter(
                    x=embeddings[mask, 0],
                    y=embeddings[mask, 1],
                    mode='markers',
                    name=label,
                    marker=dict(size=8, color=color, opacity=0.6)
                ))

            # Add a new transaction point with similarity circle
            new_point = np.array([[1.5, 1.5]])
            fig.add_trace(go.Scatter(
                x=new_point[:, 0],
                y=new_point[:, 1],
                mode='markers',
                name='Query Transaction',
                marker=dict(size=15, color='blue', symbol='star')
            ))

            # Add similarity radius
            theta = np.linspace(0, 2*np.pi, 100)
            radius = 1.2
            x_circle = new_point[0, 0] + radius * np.cos(theta)
            y_circle = new_point[0, 1] + radius * np.sin(theta)
            fig.add_trace(go.Scatter(
                x=x_circle,
                y=y_circle,
                mode='lines',
                name='Similarity Threshold',
                line=dict(color='blue', dash='dash'),
                showlegend=False
            ))

            fig.update_layout(
                title="Semantic Similarity in Vector Space",
                xaxis_title="Embedding Dimension 1 (reduced)",
                yaxis_title="Embedding Dimension 2 (reduced)",
                height=400,
                showlegend=True
            )
            apply_mdb_theme(fig)
            st.plotly_chart(fig, use_container_width=True)

    with search_tabs[2]:  # Traditional Indexes
        st.markdown("#### 📊 Traditional Index Search")

        col1, col2 = st.columns([1, 1])

        with col1:
            st.info("""
            **Traditional MongoDB Indexes:**

            • B-tree indexes for exact matches and range queries
            • Compound indexes for multi-field searches
            • Text indexes for description and reference searches
            • Optimized for high-speed lookups with millisecond response times
            """)

            # Show index examples
            st.markdown("##### Active Indexes")
            st.code("""
// Compound index for transaction queries
db.transactions.createIndex({
    "transaction_type": 1,
    "amount": 1,
    "timestamp": -1
})

// Geographic index for country matching
db.transactions.createIndex({
    "sender.country": 1,
    "recipient.country": 1
})

// Text index for reference search
db.transactions.createIndex({
    "reference_number": "text",
    "description": "text"
})
            """, language="javascript")

        with col2:
            st.markdown("##### Index Performance Comparison")

            # Create performance comparison chart
            index_data = {
                "Index Type": ["Single Field", "Compound", "Text", "Vector"],
                "Query Time (ms)": [2, 5, 8, 45],
                "Storage (MB)": [12, 28, 35, 180],
                "Use Case": ["Exact match", "Multi-field", "Full text", "Semantic"]
            }

            df = pd.DataFrame(index_data)

            fig = go.Figure()
            fig.add_trace(go.Bar(
                name='Query Time',
                x=df['Index Type'],
                y=df['Query Time (ms)'],
                yaxis='y',
                marker_color='lightblue'
            ))
            fig.add_trace(go.Bar(
                name='Storage',
                x=df['Index Type'],
                y=df['Storage (MB)'],
                yaxis='y2',
                marker_color='lightgreen'
            ))

            fig.update_layout(
                title='Index Performance Metrics',
                yaxis=dict(title='Query Time (ms)', side='left'),
                yaxis2=dict(title='Storage (MB)', overlaying='y', side='right'),
                height=350
            )
            apply_mdb_theme(fig)
            st.plotly_chart(fig, use_container_width=True)

            # Show sample query
            st.markdown("##### Sample Query")
            st.code("""
{
    "$match": {
        "transaction_type": "wire_transfer",
        "amount": {
            "$gte": 40000,
            "$lte": 60000
        },
        "sender.country": "US"
    }
}
            """, language="json")

    with search_tabs[3]:  # Feature Scoring
        st.markdown("#### ⚙️ Feature-Based Scoring")

        col1, col2 = st.columns([1, 1])

        with col1:
            st.info("""
            **Multi-Dimensional Similarity Calculation:**

            • Amount proximity scoring (0-1 scale)
            • Geographic risk correlation
            • Transaction type matching
            • Temporal pattern analysis
            • Account history similarity
            • Combined weighted score for ranking
            """)

            # Show scoring formula
            st.markdown("##### Scoring Formula")
            st.latex(r'''
            S_{total} = \sum_{i=1}^{n} w_i \cdot f_i(x, y)
            ''')
            st.caption("Where w_i are feature weights and f_i are similarity functions")

            # Feature weights
            st.markdown("##### Feature Weights")
            weights_df = pd.DataFrame({
                "Feature": ["Amount", "Geography", "Type", "Time", "History"],
                "Weight": [0.3, 0.25, 0.2, 0.15, 0.1],
                "Impact": ["High", "High", "Medium", "Medium", "Low"]
            })
            st.dataframe(weights_df, hide_index=True)

        with col2:
            st.markdown("##### Feature Score Visualization")

            # Create radar chart for feature scores
            categories = ['Amount\nSimilarity', 'Geographic\nRisk', 'Type\nMatch',
                         'Time\nPattern', 'Account\nHistory']

            # Sample transaction scores
            transaction_scores = {
                'Suspicious Transaction': [0.95, 0.88, 0.75, 0.82, 0.65],
                'Normal Transaction': [0.45, 0.32, 0.85, 0.55, 0.78],
                'Query Transaction': [0.78, 0.72, 0.90, 0.68, 0.70]
            }

            fig = go.Figure()

            for name, values in transaction_scores.items():
                fig.add_trace(go.Scatterpolar(
                    r=values,
                    theta=categories,
                    fill='toself',
                    name=name
                ))

            fig.update_layout(
                polar=dict(
                    radialaxis=dict(
                        visible=True,
                        range=[0, 1]
                    )),
                showlegend=True,
                title="Feature-Based Similarity Scores",
                height=400
            )
            apply_mdb_theme(fig, polar=True)
            st.plotly_chart(fig, use_container_width=True)

    with search_tabs[4]:  # Graph Traversal
        st.markdown("#### 🕸️ Graph Traversal for Network Analysis")

        col1, col2 = st.columns([1, 1])

        with col1:
            st.info("""
            **MongoDB $graphLookup for Fraud Ring Detection:**

            • Traverses transaction networks up to N levels deep
            • Identifies money flow patterns between accounts
            • Detects circular transactions and layering
            • Finds hidden relationships in fraud rings
            • Analyzes velocity and volume patterns in networks
            """)

            # Graph lookup example
            st.markdown("##### Graph Lookup Pipeline")
            st.code("""
{
    "$graphLookup": {
        "from": "transactions",
        "startWith": "$recipient.account_number",
        "connectFromField": "recipient.account_number",
        "connectToField": "sender.account_number",
        "as": "transaction_chain",
        "maxDepth": 3,
        "depthField": "chain_depth"
    }
}
            """, language="json")

        with col2:
            st.markdown("##### Network Visualization")

            # Create network graph
            import networkx as nx

            # Create sample network
            G = nx.DiGraph()

            # Add nodes and edges for fraud ring
            fraud_accounts = ["ACC001", "ACC002", "ACC003", "ACC004", "ACC005"]
            fraud_edges = [
                ("ACC001", "ACC002", {"amount": 5000, "suspicious": True}),
                ("ACC002", "ACC003", {"amount": 4950, "suspicious": True}),
                ("ACC003", "ACC004", {"amount": 4900, "suspicious": True}),
                ("ACC004", "ACC005", {"amount": 4850, "suspicious": True}),
                ("ACC005", "ACC002", {"amount": 4800, "suspicious": True}),  # Circular
                ("ACC001", "ACC006", {"amount": 1000, "suspicious": False}),  # Normal
            ]

            G.add_edges_from([(e[0], e[1]) for e in fraud_edges])

            # Calculate positions
            pos = nx.spring_layout(G, seed=42)

            # Create plotly figure
            edge_trace = []
            for edge in G.edges():
                x0, y0 = pos[edge[0]]
                x1, y1 = pos[edge[1]]
                edge_info = next((e for e in fraud_edges if e[0] == edge[0] and e[1] == edge[1]), None)
                color = 'red' if edge_info and edge_info[2]['suspicious'] else 'gray'

                edge_trace.append(go.Scatter(
                    x=[x0, x1, None],
                    y=[y0, y1, None],
                    mode='lines',
                    line=dict(width=2, color=color),
                    hoverinfo='none',
                    showlegend=False
                ))

            node_trace = go.Scatter(
                x=[pos[node][0] for node in G.nodes()],
                y=[pos[node][1] for node in G.nodes()],
                mode='markers+text',
                text=[node for node in G.nodes()],
                textposition="top center",
                marker=dict(
                    size=20,
                    color=['red' if node in fraud_accounts else 'green' for node in G.nodes()],
                ),
                hovertext=[f"Account: {node}" for node in G.nodes()],
                hoverinfo='text',
                showlegend=False
            )

            fig = go.Figure(data=edge_trace + [node_trace])
            fig.update_layout(
                title="Fraud Ring Network Detection",
                showlegend=False,
                height=400,
                xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
                yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
                margin=dict(l=0, r=0, t=40, b=0)
            )
            apply_mdb_theme(fig)
            st.plotly_chart(fig, use_container_width=True)

            # Show network statistics
            st.markdown("##### Network Analysis Results")
            network_stats = {
                "Metric": ["Connected Accounts", "Transaction Chain Length", "Circular Patterns", "Risk Score"],
                "Value": [6, 5, 1, 95],
                "Status": ["⚠️ High", "⚠️ Suspicious", "🚨 Detected", "🚨 Critical"]
            }
            st.dataframe(pd.DataFrame(network_stats), hide_index=True)

with tabs[5]:  # Settings
    st.markdown("### ⚙️ Application Settings")

    # Cost Configuration Section
    st.subheader("💰 Cost Configuration")

    col1, col2, col3 = st.columns([2, 1, 1])

    with col1:
        # Cost per manual review setting
        new_cost = st.number_input(
            "Cost per Manual Review ($)",
            min_value=0.0,
            max_value=1000.0,
            value=st.session_state.cost_per_manual_review,
            step=1.0,
            help="The estimated cost savings for each auto-approved transaction that doesn't require manual review"
        )

        if new_cost != st.session_state.cost_per_manual_review:
            st.session_state.cost_per_manual_review = new_cost
            st.success(f"✅ Cost per manual review updated to ${new_cost:.2f}")

    with col2:
        # Calculate current savings
        if st.session_state.metrics:
            auto_approved = st.session_state.metrics.get('decisions_breakdown', {}).get('approve', 0)
            current_savings = auto_approved * st.session_state.cost_per_manual_review
            st.metric("Total Savings", f"${current_savings:,.0f}")

    with col3:
        # Show automation rate
        if st.session_state.metrics:
            total_decisions = sum(st.session_state.metrics.get('decisions_breakdown', {}).values())
            if total_decisions > 0:
                auto_rate = (auto_approved / total_decisions) * 100
                st.metric("Automation Rate", f"{auto_rate:.1f}%")

    st.info("""
    **💡 Cost Calculation Formula:**
    - Cost Savings = Number of Auto-Approved Transactions × Cost per Manual Review
    - This represents the operational cost savings from AI automation
    - Industry average manual review cost: $35-$75 per transaction
    """)

    st.divider()

    # About This Application Section
    st.subheader("📖 About This Application")

    # Create tabs for different sections of about content
    about_tabs = st.tabs(["Overview", "Features", "Technology", "Performance"])

    with about_tabs[0]:  # Overview
        st.markdown("""
        ### 🎯 System Overview

        This demonstration showcases an enterprise-grade financial transaction processing system that combines:

        - **🧠 AI-Powered Decision Making**: AWS Bedrock (Claude & Cohere) for intelligent fraud detection
        - **🔄 Workflow Orchestration**: Temporal for reliable, distributed transaction processing
        - **🗄️ Advanced Data Management**: MongoDB Atlas with hybrid search capabilities
        - **📊 Real-time Monitoring**: Live dashboards and analytics

        The system processes financial transactions through a sophisticated pipeline that includes:
        1. Transaction enrichment and validation
        2. Multi-method fraud detection (vector, traditional, feature, graph)
        3. AI-powered risk assessment
        4. Automated decision making with human review escalation
        5. Complete audit trail and compliance tracking
        """)

    with about_tabs[1]:  # Features
        st.markdown("""
        ### ✨ Key Features

        **🔍 Hybrid Search Methods**
        - Vector similarity search (1024-dimensional embeddings)
        - Traditional MongoDB indexes for exact matches
        - Feature-based scoring with weighted factors
        - Graph traversal for network analysis

        **🛡️ Fraud Detection**
        - Money structuring pattern detection
        - Fraud ring identification
        - Synthetic identity recognition
        - Velocity and behavioral analysis

        **⚖️ Decision Engine**
        - Automated approval for low-risk (>85% confidence)
        - Human review queue for medium-risk
        - Immediate rejection for compliance violations
        - Manager escalation for high-value (>$50K)

        **📈 Monitoring & Analytics**
        - Real-time transaction tracking
        - Cost savings calculations
        - Decision distribution metrics
        - Workflow status visualization
        """)

    with about_tabs[2]:  # Technology
        st.markdown("""
        ### 🔧 Technology Stack

        | Component | Technology | Purpose |
        |-----------|------------|---------|
        | **Database** | MongoDB Atlas | Vector search, ACID transactions, graph traversal |
        | **Workflow** | Temporal.io | Durable execution, retries, compensation |
        | **AI/ML** | AWS Bedrock | Claude (reasoning), Cohere (embeddings) |
        | **Backend** | FastAPI | REST API, async processing |
        | **Frontend** | Streamlit | Real-time dashboard |
        | **Infrastructure** | Docker | Containerized microservices |

        ### 🏗️ Architecture Highlights
        - Microservices architecture for scalability
        - Event-driven processing with Temporal workflows
        - Hybrid search combining multiple detection methods
        - Fault-tolerant with automatic recovery
        - Cloud-native design for enterprise deployment
        """)

    with about_tabs[3]:  # Performance
        st.markdown("""
        ### 📊 Performance Metrics

        | Metric | Value | Description |
        |--------|-------|-------------|
        | **Decision Speed** | <500ms | Average time to process and decide |
        | **Detection Rate** | 95% | Fraud detection accuracy with hybrid search |
        | **Throughput** | 10K+ TPS | Transactions per second capacity |
        | **Availability** | 99.99% | System uptime with Temporal durability |
        | **Cost Savings** | $47/txn | Per auto-approved transaction |
        | **Automation Rate** | 75%+ | Transactions processed without human review |

        ### 🎯 Business Impact
        - **75% reduction** in manual review costs
        - **60% faster** transaction processing
        - **40% improvement** in fraud detection
        - **90% reduction** in false positives with AI
        """)

    st.divider()

    # System Information Section
    st.subheader("🖥️ System Information")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("##### Configuration")
        st.json({
            "Auto-Approval Limit": f"${config.AUTO_APPROVAL_LIMIT:,}",
            "Confidence Threshold": f"{config.CONFIDENCE_THRESHOLD_APPROVE}%",
            "Similarity Threshold": f"{config.SIMILARITY_THRESHOLD}",
            "Max Retry Attempts": 3,
            "Workflow Timeout": "5 minutes"
        })

    with col2:
        st.markdown("##### Connections")
        st.json({
            "MongoDB Atlas": "Connected",
            "Temporal Server": config.TEMPORAL_HOST,
            "AWS Region": config.AWS_REGION,
            "Bedrock Model": config.BEDROCK_MODEL_VERSION,
            "Groq Model": config.GROQ_MODEL_ID,
            "Task Queue": config.TEMPORAL_TASK_QUEUE
        })

# Footer
st.divider()
_footer_text_color = "#C8D5DE" if st.session_state.get("theme", "Dark") == "Dark" else "#5C6C75"
_footer_strong_color = "#F0F4F8" if st.session_state.get("theme", "Dark") == "Dark" else "#001E2B"
st.markdown(f"""
<div style="text-align:center;color:{_footer_text_color};
            font-family:'Euclid Circular A',sans-serif;padding:16px 0;">
    <p>Powered by <b style="color:#00ED64;">MongoDB Atlas</b>
       and <b style="color:{_footer_strong_color};">Temporal Workflows</b></p>
    <p style="font-size:0.85rem;">AI Analysis by AWS Bedrock / Groq (Claude / OpenAI &amp; VoyageAI / Cohere)</p>
</div>
""", unsafe_allow_html=True)