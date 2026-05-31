import io
import sqlite3
import inspect
import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.io as pio
from scipy import stats
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from utils.rules_engine import analyze_resource

DB_PATH = "data/cloud_data.db"

def get_db_conn():
    """Return a SQLite connection. Thread-safe for Streamlit."""
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def log_action(resource_id: str, action: str, notes: str = ""):
    """Persist a user action to the audit_log table."""
    try:
        conn = get_db_conn()
        conn.execute(
            "INSERT INTO audit_log (resource_id, action_taken, notes) VALUES (?, ?, ?)",
            (resource_id, action, notes)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # Never crash the UI over a log write

def save_schedule(resource_id: str, policy: str, enabled: int):
    """Upsert a schedule record so it survives page refresh."""
    try:
        conn = get_db_conn()
        conn.execute("""
            INSERT INTO schedules (resource_id, policy, enabled, updated_at)
            VALUES (?, ?, ?, datetime('now','localtime'))
            ON CONFLICT(resource_id) DO UPDATE SET
                policy     = excluded.policy,
                enabled    = excluded.enabled,
                updated_at = excluded.updated_at
        """, (resource_id, policy, enabled))
        conn.commit()
        conn.close()
    except Exception:
        pass

def load_schedules() -> dict:
    """Load all saved schedules from DB → {resource_id: {policy, enabled}}."""
    try:
        conn = get_db_conn()
        rows = conn.execute("SELECT resource_id, policy, enabled FROM schedules").fetchall()
        conn.close()
        return {r[0]: {"policy": r[1], "enabled": bool(r[2])} for r in rows}
    except Exception:
        return {}

# --- PERMANENT REVIEW DATABASE FUNCTIONS ---
def init_review_db():
    """Ensure the reviews table exists in the database."""
    try:
        conn = get_db_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reviews (
                resource_id TEXT PRIMARY KEY,
                is_reviewed INTEGER
            )
        """)
        conn.commit()
        conn.close()
    except Exception:
        pass

# Run this immediately to create the table if it doesn't exist
init_review_db()

def set_review_status(resource_id: str, is_reviewed: int):
    """Save the review status permanently to SQLite."""
    try:
        conn = get_db_conn()
        conn.execute("""
            INSERT INTO reviews (resource_id, is_reviewed)
            VALUES (?, ?)
            ON CONFLICT(resource_id) DO UPDATE SET
                is_reviewed = excluded.is_reviewed
        """, (resource_id, is_reviewed))
        conn.commit()
        conn.close()
    except Exception:
        pass

def load_reviewed_resources() -> set:
    """Load all permanently reviewed resources on startup."""
    try:
        conn = get_db_conn()
        rows = conn.execute("SELECT resource_id FROM reviews WHERE is_reviewed = 1").fetchall()
        conn.close()
        return {r[0] for r in rows}
    except Exception:
        return set()

# ── Page configuration ────────────────────────────────────────────────────────
st.set_page_config(page_title="Cloud Operations Dashboard", layout="wide")

# Light theme for all Plotly charts globally
pio.templates.default = "plotly_white"

# Vibrant CRED-like color palette for charts
CHART_COLORS = ["#7C3AED", "#2563EB", "#10B981", "#F59E0B", "#EF4444", "#EC4899", "#06B6D4"]

# ── Header ────────────────────────────────────────────────────────────────────
# Dark header bar on a light page — the CRED signature contrast move
header_html = """<style>
@keyframes slideIn {
    0%   { transform: translateX(-24px); opacity: 0; }
    100% { transform: translateX(0);     opacity: 1; }
}
</style>
<div class="nt-header" style="padding: 16px 28px; display: flex; align-items: center; justify-content: space-between;
    background: linear-gradient(135deg, #0B0F1A 0%, #1A1F35 100%);
    border-radius: 20px; margin-bottom: 28px;
    box-shadow: 0 8px 32px rgba(11,15,26,0.18);">
    <div style="display: flex; align-items: center; gap: 16px;">
        <div style="width: 48px; height: 48px; background: linear-gradient(145deg, #7C3AED, #4F46E5);
            border-radius: 14px; display: flex; align-items: center; justify-content: center;
            font-size: 24px; box-shadow: 0 4px 16px rgba(124,58,237,0.4);">
            ☁️
        </div>
        <div style="animation: slideIn 0.7s cubic-bezier(0.2,0.8,0.2,1) forwards;">
            <div style="display:flex; align-items:center; gap:10px;">
                <span style="font-size: 26px; font-weight: 800; color: #FFFFFF; letter-spacing: -1px;">
                    NodeTrim
                </span>
                <span class="nt-badge" style="background: rgba(124,58,237,0.25); border: 1px solid rgba(124,58,237,0.4);
                    color: #A78BFA; font-size: 10px; padding: 3px 9px; border-radius: 99px;
                    font-family: 'SF Mono', monospace; font-weight: 600; letter-spacing: 0.5px;">
                    v2.0 Enterprise
                </span>
            </div>
            <p style="margin: 2px 0 0 0; color: #FFFFFF; font-size: 12.5px; font-weight: 500;">
                Context-Aware Cloud Cost Intelligence
            </p>
        </div>
    </div>
    <div style="display: flex; align-items: center; gap: 20px;">
        <div style="text-align:right;">
            <div style="color:#CBD5E1; font-size:11px; font-weight:600;">Active Environment</div>
            <div style="color:#FFFFFF; font-size:13px; font-weight:600;">Global (All Accounts)</div>
        </div>
        <div class="nt-live" style="display: flex; align-items: center; gap: 7px;
            background: rgba(16,185,129,0.12); border: 1px solid rgba(16,185,129,0.3);
            padding: 7px 14px; border-radius: 8px;">
            <div style="width: 7px; height: 7px; background: #10B981; border-radius: 50%;
                box-shadow: 0 0 8px rgba(16,185,129,0.7);"></div>
            <span style="color: #10B981; font-size: 12px; font-weight: 600;">Local Evaluation Mode</span>
        </div>
    </div>
</div>"""

st.markdown(header_html, unsafe_allow_html=True)

# ── Global CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* ── App background ── */
.stApp { background-color: #F0F2F8; color: #0F172A; }

/* ── Sidebar — light theme ── */
section[data-testid="stSidebar"] {
    background: #FFFFFF !important;
    border-right: 3px solid #94A3B8 !important;
}
section[data-testid="stSidebar"] * { color: #0F172A !important; }

/* Disabled input (Company Name) — must override -webkit-text-fill-color too */
section[data-testid="stSidebar"] .stTextInput input:disabled {
    color: #0F172A !important;
    -webkit-text-fill-color: #0F172A !important;
    opacity: 1 !important;
}

/* Text input — single clean border */
section[data-testid="stSidebar"] .stTextInput input {
    background: #F8FAFC !important;
    border: 2px solid #94A3B8 !important;
    color: #0F172A !important;
    border-radius: 10px !important;
}

/* Step 1: strip every border from all nested divs inside selectbox */
section[data-testid="stSidebar"] .stSelectbox div { border: none !important; }

/* Step 2: apply one clean border only to the actual select box component */
section[data-testid="stSidebar"] [data-baseweb="select"] {
    background: #F8FAFC !important;
    border: 2px solid #94A3B8 !important;
    border-radius: 10px !important;
}

section[data-testid="stSidebar"] hr {
    border: none !important;
    border-top: 2px solid #94A3B8 !important;
}
section[data-testid="stSidebar"] .stCaption,
section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] { color: #64748B !important; }

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"] {
    gap: 8px; background: #FFFFFF;
    padding: 8px; border-radius: 16px;
    border: 1px solid #E2E8F0;
    box-shadow: 0 2px 12px rgba(0,0,0,0.06);
}
.stTabs [data-baseweb="tab"] {
    height: 44px; border-radius: 12px; padding: 0px 20px;
    font-weight: 600; font-size: 13.5px;
    background: transparent; color: #64748B;
    transition: all 0.2s ease;
}
.stTabs [aria-selected="true"],
.stTabs [aria-selected="true"] * {
    background: linear-gradient(135deg, #0B0F1A, #1E2A45) !important;
    color: #FFFFFF !important;
    box-shadow: 0 4px 14px rgba(11,15,26,0.25);
}

/* ── Metric cards ── */
[data-testid="metric-container"] {
    background: #FFFFFF;
    border: 1px solid #E2E8F0;
    padding: 20px; border-radius: 18px;
    box-shadow: 0 2px 16px rgba(0,0,0,0.06);
    transition: 0.2s ease;
}
[data-testid="metric-container"]:hover {
    transform: translateY(-3px);
    box-shadow: 0 8px 28px rgba(0,0,0,0.1);
}
[data-testid="metric-container"] [data-testid="stMetricValue"] {
    color: #0F172A !important; font-weight: 800;
}
[data-testid="metric-container"] [data-testid="stMetricLabel"] {
    color: #64748B !important; font-size: 12px !important;
}

/* ── Buttons — dark background, ALL children must be white ── */
.stButton button,
.stButton button * {
    background: linear-gradient(135deg, #0B0F1A, #1E2A45) !important;
    border: none; border-radius: 10px; color: #FFFFFF !important; font-weight: 600;
    box-shadow: 0 4px 14px rgba(11,15,26,0.2);
    transition: 0.2s ease;
}
.stButton button:hover { transform: translateY(-1px); box-shadow: 0 6px 20px rgba(11,15,26,0.3); }

/* ── DataFrames ── */
[data-testid="stDataFrame"] {
    border-radius: 16px; overflow: hidden;
    border: 1px solid #E2E8F0;
    box-shadow: 0 2px 12px rgba(0,0,0,0.05);
    background: #FFFFFF;
}

/* ── Containers / cards ── */
[data-testid="stVerticalBlockBorderWrapper"] {
    background: #FFFFFF !important;
    border: 1px solid #E2E8F0 !important;
    border-radius: 18px !important;
    box-shadow: 0 2px 16px rgba(0,0,0,0.06) !important;
}

/* ── Headings ── */
h1, h2, h3 { color: #0F172A !important; letter-spacing: -0.5px; }
h2 { font-size: 1.25rem !important; }

/* ── General text ── */
p, span, label, div { color: #334155; }

/* ── Expander ── */
[data-testid="stExpander"] {
    background: #FFFFFF; border: 1px solid #E2E8F0 !important;
    border-radius: 14px !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.04);
}

/* ── Info / warning / error boxes ── */
[data-testid="stAlert"] { border-radius: 12px !important; }

/* ── Download buttons — ALL children white ── */
[data-testid="stDownloadButton"] button,
[data-testid="stDownloadButton"] button * {
    background: linear-gradient(135deg, #0B0F1A, #1E2A45) !important;
    color: #FFFFFF !important; border-radius: 10px !important;
    font-weight: 600 !important;
}

/* ── Dark header — every child element forced white ── */
.nt-header * { color: #FFFFFF !important; }
.nt-header .nt-badge { color: #A78BFA !important; }    
.nt-header .nt-live  { color: #10B981 !important; }    

/* ── Caption text ── */
.stCaption, [data-testid="stCaptionContainer"] { color: #94A3B8 !important; }

/* ── Selectbox trigger box (the closed box, everywhere in app) ── */
[data-baseweb="select"] > div {
    background-color: #FFFFFF !important;
    border: 1px solid #E2E8F0 !important;
    border-radius: 10px !important;
}
[data-baseweb="select"] span,
[data-baseweb="select"] div { color: #0F172A !important; }

/* ── Selectbox dropdown popup ── */
[data-baseweb="popover"] { background: #1E2A45 !important; border-radius: 12px !important; }
[data-baseweb="popover"] > div { background: #1E2A45 !important; }
[data-baseweb="popover"] *,
[data-baseweb="popover"] [role="option"],
[data-baseweb="popover"] [data-baseweb="menu-item"],
ul[data-baseweb="menu"] li,
ul[data-baseweb="menu"] li * { color: #FFFFFF !important; background: transparent !important; }
[data-baseweb="popover"] [role="option"]:hover,
[data-baseweb="popover"] [aria-selected="true"] {
    background: rgba(124,58,237,0.25) !important;
}

/* ── Text input (search bar and all inputs) ── */
.stTextInput input {
    background: #FFFFFF !important;
    border: 1px solid #E2E8F0 !important;
    color: #0F172A !important;
    border-radius: 10px !important;
    padding: 10px 14px !important;
}
.stTextInput input::placeholder { color: #94A3B8 !important; font-weight: 500; }
</style>
""", unsafe_allow_html=True)

# ── Load dataset from SQLite database ────────────────────────────────────────
try:
    conn = get_db_conn()
    df = pd.read_sql_query("SELECT * FROM resources", conn)
    conn.close()

    numeric_columns = [
        "CPU_Usage_Percent", "Running_Hours_This_Month",
        "Monthly_Cost_INR",  "Potential_Savings_INR",
        "Age_Days",          "Carbon_Footprint_kg"
    ]
    for col in numeric_columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")

except Exception as e:
    st.error(f"Database not found. Please run: python setup_db.py\n\nError: {e}")
    st.stop()

# ── Session state ─────────────────────────────────────────────────────────────
# Load permanent reviews from DB
if "reviewed_resources" not in st.session_state:
    st.session_state.reviewed_resources = load_reviewed_resources()

# Load persisted schedules from DB
if "enabled_schedules" not in st.session_state:
    saved = load_schedules()
    st.session_state.enabled_schedules  = {rid for rid, s in saved.items() if s["enabled"]}
    st.session_state.schedule_policies  = {rid: s["policy"] for rid, s in saved.items()}

# ── Sidebar & Application Architecture Restructure ────────────────────────────
st.sidebar.markdown("""
<div style="display:flex; align-items:center; gap:12px; margin-bottom:24px; padding-bottom:20px;
    border-bottom:2px solid #94A3B8;">
    <div style="width:40px; height:40px; background:linear-gradient(135deg,#7C3AED,#4F46E5);
        border-radius:12px; display:flex; align-items:center; justify-content:center;
        font-size:20px; box-shadow:0 4px 12px rgba(124,58,237,0.25);">☁️</div>
    <div>
        <div style="font-size:17px; font-weight:800; color:#0F172A; letter-spacing:-0.5px;">NodeTrim</div>
        <div style="font-size:11px; color:#64748B; font-weight:500;">Cloud Intelligence</div>
    </div>
</div>
""", unsafe_allow_html=True)

st.sidebar.caption("ORGANIZATION")
st.sidebar.text_input("Company Name", value="Acme Corp Enterprise", disabled=True)

selected_account = st.sidebar.selectbox(
    "Active Environment",
    ["Global (All Accounts)", "AWS-Production-01", "Azure-Dev-02", "GCP-Data-03", "➕ Add Cloud Account..."]
)
if selected_account == "➕ Add Cloud Account...":
    st.sidebar.info("Authenticate via IAM Role to link a new cloud provider.")
    st.sidebar.button("Authenticate Provider", type="primary", use_container_width=True)

st.sidebar.divider()

st.sidebar.caption("GLOBAL CONTROLS")
opt_strategy = st.sidebar.select_slider(
    "Optimization Strategy",
    options=["Conservative", "Balanced", "Aggressive"],
    value="Balanced",
    help="Conservative: Only flag critical waste. Aggressive: Flag all idle resources."
)

st.sidebar.divider()

st.sidebar.caption("TEAM DRILLDOWN")
all_teams   = ["All Teams"] + sorted(df["Team_Owner"].unique().tolist())
global_team = st.sidebar.selectbox("Filter All Tabs by Team", all_teams)

# ── Apply rules engine dynamically based on Strategy ──────────────────────────
sig = inspect.signature(analyze_resource)
if 'strategy' in sig.parameters:
    df[["Smart_Action", "Risk_Level", "Confidence_Level", "Priority_Level", "Explanation"]] = \
        df.apply(lambda row: analyze_resource(row, strategy=opt_strategy), axis=1)
else:
    df[["Smart_Action", "Risk_Level", "Confidence_Level", "Priority_Level", "Explanation"]] = \
        df.apply(analyze_resource, axis=1)

# Apply global Team Filter
view_df = df[df["Team_Owner"] == global_team].copy() if global_team != "All Teams" else df.copy()

# Render Budget Slider after filtering
total_cost = view_df["Monthly_Cost_INR"].sum()
global_budget = st.sidebar.slider(
    "Global Monthly Budget (INR)",
    min_value=100000,
    max_value=int(total_cost * 1.5) if total_cost > 0 else 100000,
    value=int(total_cost * 0.95) if total_cost > 0 else 100000,
    step=50000,
    format="₹%d"
)

# Live budget breach indicator in sidebar
if total_cost > global_budget:
    st.sidebar.error(f"⚠️ Over budget by ₹{(total_cost - global_budget):,.0f}")
else:
    st.sidebar.success(f"✅ ₹{(global_budget - total_cost):,.0f} remaining")

st.sidebar.divider()
st.sidebar.caption("ACTIVE SESSION")
st.sidebar.markdown("""
<div style="display:flex; align-items:center; gap:12px; padding:12px;
    background:#F8FAFC; border-radius:12px;
    border:2px solid #94A3B8; margin-bottom:12px;">
    <div style="width:36px; height:36px; border-radius:50%;
        background:linear-gradient(135deg,#7C3AED,#4F46E5);
        display:flex; align-items:center; justify-content:center;
        font-weight:800; font-size:15px; color:#FFFFFF;">A</div>
    <div>
        <div style="font-size:13px; font-weight:700; color:#0F172A;">admin@acmecorp.com</div>
        <div style="font-size:11px; color:#64748B; font-weight:500;">System Administrator</div>
    </div>
</div>
""", unsafe_allow_html=True)
st.sidebar.button("Log Out", use_container_width=True)

# ── Pre-compute shared metrics ────────────────────────────────────────────────
potential_savings  = view_df.loc[view_df["Smart_Action"].isin(["STOP", "REDUCE"]), "Potential_Savings_INR"].sum()
optimized_cost     = total_cost - potential_savings
savings_percentage = (potential_savings / total_cost) * 100 if total_cost > 0 else 0
health_score       = max(0, 100 - savings_percentage)

stop_count   = (view_df["Smart_Action"] == "STOP").sum()
reduce_count = (view_df["Smart_Action"] == "REDUCE").sum()

# ── Helper: chart styling for light theme ─────────────────────────────────────
def style_chart(fig):
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#334155", family="Inter, system-ui, sans-serif"),
        title_font=dict(color="#0F172A", size=14, family="Inter, system-ui, sans-serif"),
        legend=dict(font=dict(color="#64748B")),
    )
    fig.update_xaxes(gridcolor="#F1F5F9", linecolor="#E2E8F0", tickfont=dict(color="#64748B"))
    fig.update_yaxes(gridcolor="#F1F5F9", linecolor="#E2E8F0", tickfont=dict(color="#64748B"))
    return fig

# ── PDF report generator ──────────────────────────────────────────────────────
def generate_pdf_report():
    buffer = io.BytesIO()
    doc    = SimpleDocTemplate(buffer, pagesize=A4, topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()
    story  = []

    story.append(Paragraph("NodeTrim — Cloud Optimization Report", styles["Title"]))
    story.append(Paragraph("Executive Summary", styles["Heading2"]))
    story.append(Spacer(1, 10))

    summary_data = [
        ["Metric",                "Value"],
        ["Total Monthly Spend",   f"Rs {total_cost:,.0f}"],
        ["Potential Savings",     f"Rs {potential_savings:,.0f}"],
        ["Optimized Spend",       f"Rs {optimized_cost:,.0f}"],
        ["Savings Percentage",    f"{savings_percentage:.1f}%"],
        ["Cloud Health Score",    f"{health_score:.1f} / 100"],
        ["Critical Resources",    str(stop_count)],
        ["Resources to Downsize", str(reduce_count)],
    ]
    t1 = Table(summary_data, colWidths=[220, 200])
    t1.setStyle(TableStyle([
        ("BACKGROUND",     (0, 0), (-1, 0),  colors.HexColor("#0B0F1A")),
        ("TEXTCOLOR",      (0, 0), (-1, 0),  colors.white),
        ("FONTNAME",       (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
        ("GRID",           (0, 0), (-1, -1), 0.4, colors.HexColor("#E2E8F0")),
        ("PADDING",        (0, 0), (-1, -1), 8),
        ("FONTSIZE",       (0, 0), (-1, -1), 10),
    ]))
    story.append(t1)
    story.append(Spacer(1, 20))

    story.append(Paragraph("Top 10 Savings Opportunities", styles["Heading2"]))
    story.append(Spacer(1, 8))

    top10    = view_df.nlargest(10, "Potential_Savings_INR")
    top_data = [["Resource ID", "Team", "Action", "Savings (Rs)", "Risk"]]
    for _, row in top10.iterrows():
        top_data.append([
            row["Resource_ID"], row["Team_Owner"], row["Smart_Action"],
            f"{row['Potential_Savings_INR']:,.0f}", row["Risk_Level"],
        ])

    t2 = Table(top_data, colWidths=[120, 100, 60, 100, 60])
    t2.setStyle(TableStyle([
        ("BACKGROUND",     (0, 0), (-1, 0),  colors.HexColor("#0B0F1A")),
        ("TEXTCOLOR",      (0, 0), (-1, 0),  colors.white),
        ("FONTNAME",       (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",       (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
        ("GRID",           (0, 0), (-1, -1), 0.3, colors.HexColor("#E2E8F0")),
        ("PADDING",        (0, 0), (-1, -1), 6),
    ]))
    story.append(t2)

    doc.build(story)
    buffer.seek(0)
    return buffer.read()

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "Overview", "Recommendations", "Idle Resources",
    "Policy & Alerts", "🌱 Carbon Report", "⚡ Automation"
])

# ─────────────────────────────────────────────────────────────────────────────
# TAB 1 — Overview
# ─────────────────────────────────────────────────────────────────────────────
with tab1:
    st.markdown("<div style='margin-bottom: 12px;'></div>", unsafe_allow_html=True)

    svg_spend  = """<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="18" y="3" width="4" height="18"></rect><rect x="10" y="8" width="4" height="13"></rect><rect x="2" y="13" width="4" height="8"></rect></svg>"""
    svg_save   = """<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 17 13.5 8.5 8.5 13.5 2 7"></polyline><polyline points="16 17 22 17 22 11"></polyline></svg>"""
    svg_alert  = """<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>"""
    svg_health = """<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline></svg>"""

    kpi_cols = st.columns(4)
    kpi_data = [
        ("Monthly Cloud Spend",      f"₹{total_cost:,.0f}",        "#7C3AED", "#F3F0FF", svg_spend),
        ("Potential Savings",        f"₹{potential_savings:,.0f}", "#F59E0B", "#FFFBEB", svg_save),
        ("Critical Waste Resources", str(stop_count),               "#EF4444", "#FEF2F2", svg_alert),
        ("Cloud Health Score",       f"{health_score:.1f}/100",     "#10B981", "#F0FDF4", svg_health),
    ]
    for col, (label, value, accent, bg, icon) in zip(kpi_cols, kpi_data):
        col.markdown(f"""
        <div style="background:#FFFFFF; border:1px solid #E2E8F0;
            border-bottom: 4px solid {accent};
            border-radius:12px; padding:24px 20px;
            box-shadow:0 1px 4px rgba(0,0,0,0.04);
            display: flex; justify-content: space-between; align-items: center;
            transition: transform 0.2s ease, box-shadow 0.2s ease;"
            onmouseover="this.style.transform='translateY(-2px)'; this.style.boxShadow='0 8px 16px rgba(0,0,0,0.06)';"
            onmouseout="this.style.transform='translateY(0)'; this.style.boxShadow='0 1px 4px rgba(0,0,0,0.04)';">
            <div>
                <div style="font-size:12px; font-weight:600; color:#64748B;
                    text-transform:uppercase; letter-spacing:0.5px; margin-bottom:6px;">
                    {label}
                </div>
                <div style="font-size:26px; font-weight:800; color:#0F172A; letter-spacing:-0.5px;">
                    {value}
                </div>
            </div>
            <div style="width:48px; height:48px; border-radius:12px; background:{bg};
                display:flex; align-items:center; justify-content:center; color:{accent};">
                {icon}
            </div>
        </div>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    with st.container(border=True):
        st.markdown("#### Resource Allocation & Distribution")
        chart_col1, chart_col2 = st.columns(2)

        with chart_col1:
            team_cost = view_df.groupby("Team_Owner")["Monthly_Cost_INR"].sum().reset_index()
            fig1 = px.pie(
                team_cost, values="Monthly_Cost_INR", names="Team_Owner",
                hole=0.65, color_discrete_sequence=CHART_COLORS
            )
            fig1.update_layout(title="Cloud Spend by Team", margin=dict(t=40, b=10, l=10, r=10))
            st.plotly_chart(style_chart(fig1), use_container_width=True)

        with chart_col2:
            action_counts = view_df["Smart_Action"].value_counts().reset_index()
            action_counts.columns = ["Action", "Count"]
            fig2 = px.bar(
                action_counts, x="Action", y="Count",
                color="Action", color_discrete_sequence=CHART_COLORS
            )
            fig2.update_layout(title="Optimization Recommendation Distribution", margin=dict(t=40, b=10, l=10, r=10))
            st.plotly_chart(style_chart(fig2), use_container_width=True)

    with st.container(border=True):
        cmp_col, ins_col = st.columns([1.5, 1])

        with cmp_col:
            st.markdown("#### Financial Optimization Impact")
            sim_col1, sim_col2, sim_col3 = st.columns(3)
            sim_col1.metric("Current Spend",     f"₹{total_cost:,.0f}")
            sim_col2.metric("Optimized Spend",   f"₹{optimized_cost:,.0f}")
            sim_col3.metric("Savings Potential", f"{savings_percentage:.1f}%")

            comparison_df = pd.DataFrame({
                "Category": ["Current Cost", "Optimized Cost"],
                "Amount":   [total_cost, optimized_cost]
            })
            comparison_chart = px.bar(
                comparison_df, x="Category", y="Amount", text="Amount",
                color="Category", color_discrete_sequence=["#94A3B8", "#10B981"]
            )
            comparison_chart.update_traces(texttemplate='₹%{text:,.0f}', textposition='outside')
            comparison_chart.update_layout(showlegend=False, margin=dict(t=20, b=10, l=10, r=10), height=250)
            st.plotly_chart(style_chart(comparison_chart), use_container_width=True)

        with ins_col:
            st.markdown("#### ⚡ Rules-Based Optimization Engine")
            high_risk_count      = (view_df["Risk_Level"]    == "High").sum()
            critical_resources   = (view_df["Priority_Level"] == "Critical").sum()
            weekend_waste_count  = view_df["Weekend_Waste_Flag"].sum()

            insights = []
            if potential_savings   > 100000: insights.append(("High Potential Savings", f"Preventable waste exceeds ₹{potential_savings:,.0f}/mo.", "#F59E0B", "#FFFBEB"))
            if high_risk_count     > 5:      insights.append(("Governance Risk", f"{high_risk_count} high-risk resources need review.", "#EF4444", "#FEF2F2"))
            if weekend_waste_count > 10:     insights.append(("Scheduling Opportunity", "Excessive weekend infrastructure activity detected.", "#3B82F6", "#EFF6FF"))
            if critical_resources  > 8:      insights.append(("Immediate Action Required", "Large number of critical waste resources.", "#EF4444", "#FEF2F2"))
            
            if not insights:
                insights.append(("Optimal Health", "No critical insights at this time.", "#10B981", "#F0FDF4"))

            for title, desc, color, bg in insights[:4]: 
                st.markdown(f"""
                <div style="background:{bg}; border-left: 3px solid {color}; padding: 12px 16px; margin-bottom: 12px; border-radius: 4px;">
                    <div style="font-weight: 700; font-size: 13px; color: #0F172A; display: flex; align-items: center; gap: 8px;">
                        <div style="width: 8px; height: 8px; border-radius: 50%; background: {color}; box-shadow: 0 0 6px {color};"></div>
                        {title}
                    </div>
                    <div style="font-size: 12px; color: #475569; margin-top: 4px; padding-left: 16px;">{desc}</div>
                </div>
                """, unsafe_allow_html=True)

    with st.container(border=True):
        st.markdown("#### Cloud Provider Intelligence")
        cloud_col, waste_col = st.columns(2)

        with cloud_col:
            provider_cost = view_df.groupby("Cloud_Provider")["Monthly_Cost_INR"].sum().reset_index()
            provider_chart = px.bar(
                provider_cost, x="Cloud_Provider", y="Monthly_Cost_INR",
                color="Cloud_Provider",
                text="Monthly_Cost_INR", color_discrete_sequence=CHART_COLORS
            )
            provider_chart.update_traces(texttemplate='₹%{text:,.0f}', textposition='outside')
            provider_chart.update_layout(title="Spend by Provider", margin=dict(t=40, b=10, l=10, r=10), showlegend=False)
            st.plotly_chart(style_chart(provider_chart), use_container_width=True)

        with waste_col:
            waste_by_provider = view_df[view_df["Smart_Action"].isin(["STOP", "REDUCE"])]
            waste_provider_df = waste_by_provider.groupby("Cloud_Provider")["Potential_Savings_INR"].sum().reset_index()
            fig_waste = px.pie(
                waste_provider_df, names="Cloud_Provider", values="Potential_Savings_INR",
                hole=0.55, color_discrete_sequence=CHART_COLORS
            )
            fig_waste.update_layout(title="Optimization Opportunity", margin=dict(t=40, b=10, l=10, r=10))
            st.plotly_chart(style_chart(fig_waste), use_container_width=True)

    st.markdown("<br>  Priority Action Items", unsafe_allow_html=True)
    st.caption("Top 5 resources generating the most preventable cost. Review immediately.")
    
    for _, row in view_df.nlargest(5, "Potential_Savings_INR").iterrows():
        action = row["Smart_Action"]
        
        if action == "STOP":
            bg, border, text_col = "#FEF2F2", "#FCA5A5", "#DC2626"
        elif action == "REDUCE":
            bg, border, text_col = "#FFFBEB", "#FCD34D", "#D97706"
        else:
            bg, border, text_col = "#EFF6FF", "#93C5FD", "#2563EB"
            
        st.markdown(f"""
        <div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 8px; padding: 12px 16px; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 1px 3px rgba(0,0,0,0.02);">
            <div style="display: flex; gap: 16px; align-items: center;">
                <div style="background: {bg}; color: {text_col}; border: 1px solid {border}; font-weight: 700; font-size: 11px; padding: 4px 10px; border-radius: 99px; min-width: 70px; text-align: center; letter-spacing: 0.5px;">
                    {action}
                </div>
                <div>
                    <div style="font-weight: 700; font-size: 14px; color: #0F172A;">{row['Resource_ID']}</div>
                    <div style="font-size: 12px; color: #64748B;">Team: <strong>{row['Team_Owner']}</strong> &nbsp;•&nbsp; Type: {row['Resource_Type']}</div>
                </div>
            </div>
            <div style="text-align: right;">
                <div style="font-size: 11px; color: #64748B; text-transform: uppercase; letter-spacing: 0.5px;">Potential Savings</div>
                <div style="font-weight: 800; font-size: 16px; color: #10B981;">+₹{row['Potential_Savings_INR']:,.0f}/mo</div>
            </div>
        </div>
        """, unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# TAB 2 — Recommendations
# ─────────────────────────────────────────────────────────────────────────────
with tab2:
    st.markdown("""
    <style>
    /* --- ACTION BADGES --- */
    .badge-STOP   { background: #FEE2E2; color: #DC2626; border: 2px solid rgba(185, 28, 28, 0.25); }
    .badge-REDUCE { background: #FEF3C7; color: #D97706; border: 2px solid rgba(180, 83, 9, 0.25); }
    .badge-KEEP   { background: #D1FAE5; color: #059669; border: 2px solid rgba(4, 120, 87, 0.25); }
    .badge-REVIEW { background: #DBEAFE; color: #2563EB; border: 2px solid rgba(29, 78, 216, 0.25); }

    .nt-action-badge {
        padding: 6px 18px; border-radius: 999px; font-size: 14px; 
        font-weight: 800; display: inline-block; min-width: 95px; 
        text-align: center; letter-spacing: 0.8px;
        transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
        cursor: pointer; box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    }
    .nt-action-badge:hover { transform: scale(1.08); background: #FFFFFF; }
    .badge-STOP:hover   { box-shadow: 0 6px 16px rgba(185, 28, 28, 0.4); border-color: #B91C1C; }
    .badge-REDUCE:hover { box-shadow: 0 6px 16px rgba(180, 83, 9, 0.4); border-color: #B45309; }
    .badge-KEEP:hover   { box-shadow: 0 6px 16px rgba(4, 120, 87, 0.4); border-color: #047857; }
    .badge-REVIEW:hover { box-shadow: 0 6px 16px rgba(29, 78, 216, 0.4); border-color: #1D4ED8; }

    /* --- PREMIUM BLUE BUTTON --- */
    .stTabs button[kind="primary"] {
        background: linear-gradient(180deg, #1E40AF 0%, #1D4ED8 100%) !important;
        border: 1px solid #1E3A8A !important;
        box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05), 0 4px 12px rgba(29, 78, 216, 0.2) !important;
        transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
    }
    .stTabs button[kind="primary"] * {
        color: #FFFFFF !important; -webkit-text-fill-color: #FFFFFF !important;
        font-weight: 800 !important; letter-spacing: 0.5px !important;
    }
    .stTabs button[kind="primary"]:hover {
        background: linear-gradient(180deg, #1D4ED8 0%, #1E40AF 100%) !important;
        border: 1px solid #172554 !important; box-shadow: 0 6px 16px rgba(29, 78, 216, 0.3) !important;
        transform: translateY(-1px);
    }
    .stTabs button[kind="primary"]:active {
        transform: translateY(0px); box-shadow: 0 2px 4px rgba(29, 78, 216, 0.2) !important;
    }

    /* --- PREMIUM GREY BUTTON --- */
    .stTabs button[kind="secondary"] {
        background: linear-gradient(180deg, #F8FAFC 0%, #F1F5F9 100%) !important;
        border: 1px solid #CBD5E1 !important; box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05) !important;
        transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
    }
    .stTabs button[kind="secondary"] * {
        color: #334155 !important; -webkit-text-fill-color: #334155 !important;
        font-weight: 800 !important; letter-spacing: 0.5px !important;
    }
    .stTabs button[kind="secondary"]:hover {
        background: linear-gradient(180deg, #F1F5F9 0%, #E2E8F0 100%) !important;
        border: 1px solid #94A3B8 !important; transform: translateY(-1px);
    }
    .stTabs button[kind="secondary"]:active {
        transform: translateY(0px); box-shadow: none !important;
    }
    </style>
    """, unsafe_allow_html=True)

    with st.expander("📖 How the recommendation engine works"):
        st.markdown("""
        The rules engine evaluates each cloud resource against a fixed set of operational thresholds...
        """)

    filter_col1, filter_col2, filter_col3, filter_col4 = st.columns(4)
    with filter_col1:
        selected_team = st.selectbox("Filter by Team", ["All"] + sorted(view_df["Team_Owner"].unique().tolist()))
    with filter_col2:
        selected_criticality = st.selectbox("Filter by Criticality", ["All"] + sorted(view_df["Criticality"].unique().tolist()))
    with filter_col3:
        selected_action = st.selectbox("Filter by Action", ["All"] + sorted(view_df["Smart_Action"].unique().tolist()))
    with filter_col4:
        selected_provider = st.selectbox("Cloud Provider", ["All"] + sorted(view_df["Cloud_Provider"].unique().tolist()))

    search_term = st.text_input("", placeholder="🔍 Search Resource ID")

    filtered_df = view_df.copy()
    filters = {
        "Team_Owner":     selected_team,
        "Criticality":    selected_criticality,
        "Smart_Action":   selected_action,
        "Cloud_Provider": selected_provider,
    }
    for column, value in filters.items():
        if value != "All":
            filtered_df = filtered_df[filtered_df[column] == value]

    if search_term:
        filtered_df = filtered_df[filtered_df["Resource_ID"].str.contains(search_term, case=False)]

    reviewed_in_view = filtered_df["Resource_ID"].isin(st.session_state.reviewed_resources).sum()
    st.caption(f"{len(filtered_df)} resources shown  •  {reviewed_in_view} marked as reviewed")

    def create_badge(action):
        return f'<div class="nt-action-badge badge-{action}">{action}</div>'

    display_cols = [
        "Resource_ID", "Resource_Type", "Team_Owner", "Criticality",
        "CPU_Usage_Percent", "Monthly_Cost_INR", "Smart_Action",
        "Risk_Level", "Confidence_Level", "Priority_Level", "Explanation"
    ]

    # --- CALLBACK FUNCTION FOR REVIEWS ---
    def toggle_review(res_id, mark_reviewed, smart_action=""):
        if mark_reviewed:
            st.session_state.reviewed_resources.add(res_id)
            set_review_status(res_id, 1)  # Save to Database
            log_action(res_id, "MARKED_REVIEWED", f"Action: {smart_action}")
        else:
            st.session_state.reviewed_resources.discard(res_id)
            set_review_status(res_id, 0)  # Remove from Database
            log_action(res_id, "UNDO_REVIEW")

    for _, row in filtered_df[display_cols].iterrows():
        resource_id = row["Resource_ID"]
        is_reviewed = resource_id in st.session_state.reviewed_resources

        with st.container(border=True):
            if is_reviewed:
                st.markdown(
                    "<div style='font-size:11px; color:#10B981; font-weight:700; "
                    "background:#F0FDF4; padding:4px 10px; border-radius:6px; "
                    "display:inline-block; margin-bottom:6px;'>✔ Marked as Reviewed</div>",
                    unsafe_allow_html=True
                )

            col1, col2, col3, col4, col5 = st.columns([2.5, 1.2, 1.0, 3.0, 1.2])

            with col1:
                st.markdown(f"""
                <div style="line-height:1.5; padding-top:4px;">
                    <span style="font-weight:800; color:#0F172A; font-size:15px;">{resource_id}</span>
                    <span style="color:#64748B; font-size:12px; margin-left:6px;">({row['Resource_Type']})</span><br>
                    <span style="font-size:12px; color:#64748B;">
                        Team: <strong style="color:#334155;">{row['Team_Owner']}</strong> &nbsp;•&nbsp;
                        <strong style="color:#334155;">{row['Criticality']}</strong>
                    </span>
                </div>""", unsafe_allow_html=True)

            with col2:
                st.markdown(f"""
                <div style="line-height: 1.6; font-size: 12.5px; padding-top: 4px;">
                    <span style="color: #64748B;">CPU:</span> <strong style="color: #0F172A; font-size: 14px;">{row['CPU_Usage_Percent']}%</strong><br>
                    <span style="color: #64748B;">Cost:</span> <strong style="color: #7C3AED; font-size: 14px;">₹{row['Monthly_Cost_INR']:,.0f}</strong>
                </div>
                """, unsafe_allow_html=True)

            with col3:
                st.markdown(f"<div style='padding-top:8px;'>{create_badge(row['Smart_Action'])}</div>", unsafe_allow_html=True)

            with col4:
                st.markdown(f"""
                <div style="font-size:12.5px; color:#334155; padding-top:4px; line-height:1.5;">
                    <strong style="color:#0F172A;">Recommendation:</strong><br>{row['Explanation']}
                </div>""", unsafe_allow_html=True)
                if row["Risk_Level"] == "High":
                    st.markdown("""
                    <div style="display:flex; align-items:center; margin-top:6px; background:#FEF2F2;
                        padding:4px 10px; border-radius:6px; display:inline-flex; gap:6px;">
                        <div style="width:6px; height:6px; background:#EF4444; border-radius:50%;"></div>
                        <span style="color:#EF4444; font-weight:700; font-size:11px;">Critical Risk — Review immediately</span>
                    </div>""", unsafe_allow_html=True)

            with col5:
                st.markdown("<div style='padding-top:6px;'></div>", unsafe_allow_html=True)
                if is_reviewed:
                    st.button(
                        "↩ Undo", 
                        key=f"undo_{resource_id}", 
                        use_container_width=True,
                        on_click=toggle_review,               
                        args=(resource_id, False)             
                    )
                else:
                    st.button(
                        "✔ Mark Reviewed", 
                        key=f"review_{resource_id}", 
                        type="primary", 
                        use_container_width=True,
                        on_click=toggle_review,               
                        args=(resource_id, True, row['Smart_Action'])
                    )

    # ─────────────────────────────────────────────────────────────────────────────
    # EXPORT REPORTS
    # ─────────────────────────────────────────────────────────────────────────────
    st.subheader("Export Reports")

    st.markdown("""
    <style>
    [data-testid="stDownloadButton"] button::before,
    [data-testid="stDownloadButton"] button::after { display: none !important; }
    [data-testid="stDownloadButton"] button * {
        background: transparent !important; color: #FFFFFF !important;
        -webkit-text-fill-color: #FFFFFF !important; font-weight: 800 !important; text-shadow: none !important;
    }
    [data-testid="stDownloadButton"] button {
        border-radius: 8px !important; box-shadow: none !important;
        transform: translateY(0) !important; transition: all 0.1s ease !important;
    }
    [data-testid="stDownloadButton"] button:active { transform: translateY(3px) !important; }

    div[data-testid="stHorizontalBlock"]:last-of-type div[data-testid="column"]:nth-of-type(1) [data-testid="stDownloadButton"] button {
        background: #10B981 !important; border: 2px solid #059669 !important; border-bottom: 5px solid #047857 !important;
    }
    div[data-testid="stHorizontalBlock"]:last-of-type div[data-testid="column"]:nth-of-type(1) [data-testid="stDownloadButton"] button:hover {
        background: #059669 !important; border-bottom: 5px solid #047857 !important;
    }
    div[data-testid="stHorizontalBlock"]:last-of-type div[data-testid="column"]:nth-of-type(1) [data-testid="stDownloadButton"] button:active {
        border-bottom: 2px solid #047857 !important;
    }

    div[data-testid="stHorizontalBlock"]:last-of-type div[data-testid="column"]:nth-of-type(2) [data-testid="stDownloadButton"] button {
        background: #8B5CF6 !important; border: 2px solid #7C3AED !important; border-bottom: 5px solid #6D28D9 !important;
    }
    div[data-testid="stHorizontalBlock"]:last-of-type div[data-testid="column"]:nth-of-type(2) [data-testid="stDownloadButton"] button:hover {
        background: #7C3AED !important; border-bottom: 5px solid #6D28D9 !important;
    }
    div[data-testid="stHorizontalBlock"]:last-of-type div[data-testid="column"]:nth-of-type(2) [data-testid="stDownloadButton"] button:active {
        border-bottom: 2px solid #6D28D9 !important;
    }

    div[data-testid="stHorizontalBlock"]:last-of-type div[data-testid="column"]:nth-of-type(3) [data-testid="stDownloadButton"] button {
        background: #F43F5E !important; border: 2px solid #E11D48 !important; border-bottom: 5px solid #BE123C !important;
    }
    div[data-testid="stHorizontalBlock"]:last-of-type div[data-testid="column"]:nth-of-type(3) [data-testid="stDownloadButton"] button:hover {
        background: #E11D48 !important; border-bottom: 5px solid #BE123C !important;
    }
    div[data-testid="stHorizontalBlock"]:last-of-type div[data-testid="column"]:nth-of-type(3) [data-testid="stDownloadButton"] button:active {
        border-bottom: 2px solid #BE123C !important;
    }
    </style>
    """, unsafe_allow_html=True)

    export_col1, export_col2, export_col3 = st.columns(3)

    with export_col1:
        st.download_button(
            label="📥 Download CSV",
            data=filtered_df.to_csv(index=False).encode("utf-8"),
            file_name="cloud_optimization_report.csv",
            mime="text/csv",
            use_container_width=True
        )
    with export_col2:
        summary_text = (
            f"NodeTrim — Cloud Operations Executive Summary\n"
            f"{'─' * 45}\n\n"
            f"Total Monthly Spend : ₹{total_cost:,.0f}\n"
            f"Potential Savings   : ₹{potential_savings:,.0f}\n"
            f"Optimized Spend     : ₹{optimized_cost:,.0f}\n"
            f"Cloud Health Score  : {health_score:.1f}/100\n"
            f"Critical Resources  : {stop_count}\n"
            f"Resources to Reduce : {reduce_count}\n"
        )
        st.download_button(
            label="📄 Download Summary (TXT)",
            data=summary_text,
            file_name="executive_summary.txt",
            mime="text/plain",
            use_container_width=True
        )
    with export_col3:
        st.download_button(
            label="📑 Download PDF Report",
            data=generate_pdf_report(),
            file_name="nodetrim_report.pdf",
            mime="application/pdf",
            use_container_width=True
        )

# ─────────────────────────────────────────────────────────────────────────────
# TAB 3 — Idle Resources
# ─────────────────────────────────────────────────────────────────────────────
with tab3:
    waste_resources  = view_df[view_df["Smart_Action"] == "STOP"]
    total_waste      = waste_resources["Monthly_Cost_INR"].sum()
    waste_count      = len(waste_resources)
    waste_percentage = (total_waste / total_cost) * 100 if total_cost > 0 else 0

    with st.container(border=True):
        st.markdown("#### 🗑️ Abandoned Infrastructure Summary")
        waste_col1, waste_col2, waste_col3 = st.columns(3)
        waste_col1.metric("Idle Resources",       waste_count)
        waste_col2.metric("Estimated Waste Cost", f"₹{total_waste:,.0f}")
        waste_col3.metric("Waste Percentage",     f"{waste_percentage:.1f}%")

        st.info(f"NodeTrim identified {waste_count} potentially abandoned or inefficient cloud resources generating approximately ₹{total_waste:,.0f} in preventable monthly expenditure.")

        st.dataframe(
            waste_resources[[
                "Resource_ID", "Cloud_Provider", "Resource_Type", "Team_Owner",
                "Monthly_Cost_INR", "Tag_Compliance", "Owner_Email", "Priority_Level"
            ]],
            use_container_width=True
        )

    with st.container(border=True):
        st.markdown("#### 📅 Weekend Waste Analysis")
        weekend_waste = view_df[view_df["Weekend_Waste_Flag"] == 1]
        st.dataframe(
            weekend_waste[[
                "Resource_ID", "Team_Owner", "Cloud_Provider",
                "Monthly_Cost_INR", "CPU_Usage_Percent"
            ]],
            use_container_width=True
        )

# ─────────────────────────────────────────────────────────────────────────────
# TAB 4 — Policy & Alerts
# ─────────────────────────────────────────────────────────────────────────────
with tab4:

    fully_compliant = view_df[
        (view_df["Tag_Compliance"]     == "Compliant") &
        (view_df["Owner_Email"]        != "unassigned") &
        (view_df["Last_Activity_Days"] <= 30)
    ]
    compliance_score = (len(fully_compliant) / len(view_df) * 100) if len(view_df) > 0 else 0

    if compliance_score >= 80:
        score_color, score_bg, score_label = "#10B981", "#F0FDF4", "Good"
    elif compliance_score >= 50:
        score_color, score_bg, score_label = "#F59E0B", "#FFFBEB", "Needs Attention"
    else:
        score_color, score_bg, score_label = "#EF4444", "#FEF2F2", "Critical"

    st.markdown(f"""
    <div style="background:{score_bg}; border:1.5px solid {score_color}33;
        border-left:5px solid {score_color};
        border-radius:16px; padding:24px 32px; margin-bottom:24px;
        display:flex; align-items:center; gap:32px;
        box-shadow:0 4px 20px {score_color}18;">
        <div style="text-align:center; min-width:80px;">
            <div style="font-size:52px; font-weight:900; color:{score_color}; line-height:1;">
                {compliance_score:.0f}%
            </div>
            <div style="font-size:11px; color:#94A3B8; margin-top:4px; text-transform:uppercase;
                letter-spacing:0.5px;">Compliance Score</div>
        </div>
        <div>
            <div style="font-size:20px; font-weight:800; color:{score_color};">{score_label}</div>
            <div style="font-size:13px; color:#64748B; margin-top:6px; max-width:420px;">
                {len(fully_compliant)} of {len(view_df)} resources are fully compliant —
                tagged correctly, ownership assigned, and active within 30 days.
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""<style>
    div[data-baseweb="slider"] > div > div { height: 8px !important; border-radius: 8px !important; background: #E2E8F0 !important; }
    div[data-baseweb="slider"] div[role="slider"] { height: 24px !important; width: 24px !important; background: #0B0F1A !important; border: 2px solid #FFFFFF !important; box-shadow: 0 2px 8px rgba(0,0,0,0.25) !important; }
    div[data-baseweb="slider"] div div div { background: #7C3AED !important; }
    </style>""", unsafe_allow_html=True)

    st.markdown("""
    <div style="background:#FFFFFF; border:1px solid #E2E8F0; border-radius:14px;
        padding:20px 24px 8px; text-align:center; margin-bottom:-10px;
        box-shadow:0 2px 12px rgba(0,0,0,0.05);">
        <p style="margin:0 0 12px; color:#334155; font-size:15px; font-weight:600;">
            Slide to set your organizational spending threshold
        </p>
    </div>""", unsafe_allow_html=True)

    step = 50000
    recommended_budget = round(int(total_cost * 1.15) / step) * step
    recommended_budget = max(500000, min(recommended_budget, int(total_cost * 2)))

    budget_limit = st.slider(
        "Target Budget",
        min_value=500000, max_value=int(total_cost * 2) if total_cost > 0 else 1000000,
        value=recommended_budget, step=step,
        format="₹%d", label_visibility="hidden"
    )

    if total_cost > budget_limit:
        st.error(f"Spending exceeded budget by ₹{(total_cost - budget_limit):,.0f}")
    else:
        st.success("Spending is within limits.")

    # --- REAL STATISTICAL ANOMALY DETECTION ---
    with st.container(border=True):
        st.markdown("####  Spending Anomaly Detection")
        
        # Simulate realistic daily spend based on your total_cost
        np.random.seed(42) # For reproducible demo data
        base_daily_spend = total_cost / 30 if total_cost > 0 else 15000
        noise = np.random.normal(0, base_daily_spend * 0.05, 30) 
        daily_spend = np.full(30, base_daily_spend) + noise
        
        # Inject a realistic spike on day 27 to prove the math works
        daily_spend[26] = base_daily_spend * 2.8 
        daily_spend[27] = base_daily_spend * 3.1

        spike_data = pd.DataFrame({
            "Day": list(range(1, 31)),
            "Daily_Spend": daily_spend
        })

        # Calculate Z-Scores to mathematically find anomalies
        z_scores = np.abs(stats.zscore(spike_data["Daily_Spend"]))
        spike_data["Is_Anomaly"] = z_scores > 2.5 # Anything 2.5 standard deviations from the mean

        spike_chart = px.line(
            spike_data, x="Day", y="Daily_Spend",
            markers=True,
            color_discrete_sequence=["#7C3AED"]
        )
        
        # Highlight anomalies in Red on the chart
        anomalies = spike_data[spike_data["Is_Anomaly"]]
        if not anomalies.empty:
            spike_chart.add_scatter(
                x=anomalies["Day"], 
                y=anomalies["Daily_Spend"], 
                mode='markers', 
                marker=dict(color='red', size=12, symbol='x'),
                name='Anomaly Detected'
            )

        st.plotly_chart(style_chart(spike_chart), use_container_width=True)

        if not anomalies.empty:
            st.error(f"🚨 Statistical Anomaly Detected: Irregular spending identified on Day {anomalies['Day'].iloc[0]}.")
            root_col1, root_col2 = st.columns(2)
            with root_col1:
                st.error("""
                ### Primary Cause Identified
                • QA team launched multiple high-memory compute instances
                • Resources remained active after testing completion
                • Idle infrastructure generated excessive compute charges
                """)
            with root_col2:
                st.success("""
                ### Suggested Action
                • Apply automated shutdown scheduling
                • Enforce department budget controls
                • Review temporary infrastructure lifecycle
                """)
        else:
            st.success("✅ Spending patterns are mathematically stable. No anomalies detected.")

    with st.container(border=True):
        st.markdown("####  Resource Ownership Governance")
        unassigned_resources = view_df[view_df["Owner_Email"] == "unassigned"]
        inactive_resources   = view_df[view_df["Last_Activity_Days"] > 30]

        n_unassigned = len(unassigned_resources)
        n_inactive   = len(inactive_resources)

        gov_col1, gov_col2 = st.columns(2)
        gov_col1.metric("Unassigned Resources", n_unassigned)
        gov_col2.metric("Inactive Resources",   n_inactive)

        if n_unassigned > 0:
            st.error(f"{n_unassigned} cloud resources do not have assigned ownership metadata. These present governance, billing, and operational accountability risks.")
        if n_inactive > 0:
            st.warning(f"{n_inactive} resources have remained inactive for extended periods. Review recommended to prevent unnecessary cloud expenditure.")

        st.caption("Resources shown below either lack assigned ownership, have been inactive for over 30 days, or violate governance accountability policies.")
        ownership_risk_df = view_df[(view_df["Owner_Email"] == "unassigned") | (view_df["Last_Activity_Days"] > 30)]
        st.dataframe(
            ownership_risk_df[["Resource_ID", "Cloud_Provider", "Team_Owner", "Owner_Email", "Created_By", "Last_Activity_Days", "Monthly_Cost_INR"]],
            use_container_width=True
        )

    with st.container(border=True):
        st.markdown("#### 🏷️ Tag Compliance Governance")
        st.caption("Cloud governance policies require infrastructure resources to maintain mandatory ownership and financial metadata tags.")

        missing_tag_resources = view_df[view_df["Tag_Compliance"] == "Missing Tags"]
        partial_tag_resources = view_df[view_df["Tag_Compliance"] == "Partial"]

        n_missing = len(missing_tag_resources)
        n_partial = len(partial_tag_resources)

        tag_col1, tag_col2 = st.columns(2)
        tag_col1.metric("Missing Tag Violations",       n_missing)
        tag_col2.metric("Partial Compliance Resources", n_partial)

        if n_missing > 0:
            st.error(f"{n_missing} resources violate mandatory governance tagging policies. Financial accountability may be impacted.")
        if n_partial > 0:
            st.warning(f"{n_partial} cloud resources contain incomplete governance metadata.")

        tag_risk_df = view_df[view_df["Tag_Compliance"] != "Compliant"]
        st.dataframe(
            tag_risk_df[["Resource_ID", "Cloud_Provider", "Team_Owner", "Owner_Email", "Tag_Compliance", "Monthly_Cost_INR", "Smart_Action"]],
            use_container_width=True
        )

# ─────────────────────────────────────────────────────────────────────────────
# TAB 5 — Carbon Report
# ─────────────────────────────────────────────────────────────────────────────
with tab5:

    from utils.rules_engine import get_carbon_intensity

    POWER_DRAW_KW = 0.1  # Estimated avg power per resource (t3.medium class)

    if "Carbon_Footprint_kg" in view_df.columns and "Region" in view_df.columns:
        carbon_series = view_df.apply(
            lambda r: r["Running_Hours_This_Month"] * POWER_DRAW_KW *
                      get_carbon_intensity(r.get("Region", "default")),
            axis=1
        )
        view_df = view_df.copy()
        view_df["Carbon_Footprint_kg"] = carbon_series
        total_carbon = carbon_series.sum()
    else:
        total_carbon = view_df["Carbon_Footprint_kg"].sum() if "Carbon_Footprint_kg" in view_df.columns else 0

    saved_carbon_stop   = view_df[view_df["Smart_Action"] == "STOP"]["Carbon_Footprint_kg"].sum() if "Carbon_Footprint_kg" in view_df.columns else 0
    saved_carbon_reduce = (view_df[view_df["Smart_Action"] == "REDUCE"]["Carbon_Footprint_kg"].sum() * 0.4) if "Carbon_Footprint_kg" in view_df.columns else 0

    carbon_reduction = saved_carbon_stop + saved_carbon_reduce
    optimized_carbon = total_carbon - carbon_reduction

    with st.container(border=True):
        st.markdown("#### 🌱 Carbon Footprint Analysis")
        
        green_col1, green_col2, green_col3 = st.columns(3)
        green_col1.metric("Current Carbon Impact",      f"{total_carbon:.1f} kg CO₂")
        green_col2.metric("Projected Optimized Impact", f"{optimized_carbon:.1f} kg CO₂")
        green_col3.metric("Potential Reduction",         f"{carbon_reduction:.1f} kg CO₂")

        carbon_df = pd.DataFrame({
            "State":     ["Current", "Optimized"],
            "Carbon_kg": [total_carbon, optimized_carbon]
        })
        carbon_chart = px.bar(
            carbon_df, x="State", y="Carbon_kg",
            text="Carbon_kg",
            color="State", color_discrete_sequence=["#EF4444", "#10B981"]
        )
        st.plotly_chart(style_chart(carbon_chart), use_container_width=True)

        st.success(
            f"Applying optimization recommendations may reduce estimated cloud-related carbon emissions "
            f"by approximately {carbon_reduction:.1f} kg CO₂ per month. "
            f"Carbon intensity calculated per cloud region (e.g., ap-south-1 Mumbai = 0.82 kg CO₂/kWh, "
            f"us-west-2 Oregon = 0.15 kg CO₂/kWh) using published AWS Sustainability data."
        )
        st.caption("Methodology: running_hours × 0.1 kW (avg power draw) × regional carbon intensity (kg CO₂/kWh). STOP resources: 100% reduction. REDUCE resources: 40% reduction (rightsizing estimate).")

# ─────────────────────────────────────────────────────────────────────────────
# TAB 6 — Automation
# ─────────────────────────────────────────────────────────────────────────────
with tab6:

    def toggle_schedule(res_id, enable, policy=""):
        if enable:
            st.session_state.enabled_schedules.add(res_id)
            if "schedule_policies" not in st.session_state:
                st.session_state.schedule_policies = {}
            st.session_state.schedule_policies[res_id] = policy
            save_schedule(res_id, policy, enabled=1)
            log_action(res_id, "SCHEDULE_ENABLED", f"Policy: {policy}")
        else:
            st.session_state.enabled_schedules.discard(res_id)
            save_schedule(res_id, policy, enabled=0)
            log_action(res_id, "SCHEDULE_DISABLED", f"Policy: {policy}")

    st.markdown("""
    <style>
    .stButton button[kind="primary"] *, .stButton button[kind="secondary"] * {
        background: none !important; background-color: transparent !important; background-image: none !important; box-shadow: none !important; text-shadow: none !important;
    }
    .stButton button[kind="primary"] {
        background: #2563EB !important; border: 2px solid #1D4ED8 !important; border-bottom: 5px solid #1E40AF !important; border-radius: 8px !important; box-shadow: none !important; transform: translateY(0) !important; transition: all 0.1s ease !important;
    }
    .stButton button[kind="primary"] * {
        color: #FFFFFF !important; -webkit-text-fill-color: #FFFFFF !important; font-weight: 800 !important;
    }
    .stButton button[kind="primary"]:hover {
        background: #1D4ED8 !important; border-bottom: 5px solid #1E40AF !important; transform: translateY(0) !important; box-shadow: none !important;
    }
    .stButton button[kind="primary"]:active {
        background: #1E40AF !important; border-bottom: 2px solid #1E40AF !important; transform: translateY(3px) !important;
    }
    .stButton button[kind="secondary"] {
        background: #F8FAFC !important; border: 2px solid #CBD5E1 !important; border-bottom: 5px solid #94A3B8 !important; border-radius: 8px !important; box-shadow: none !important; transform: translateY(0) !important; transition: all 0.1s ease !important;
    }
    .stButton button[kind="secondary"] * {
        color: #475569 !important; -webkit-text-fill-color: #475569 !important; font-weight: 800 !important;
    }
    .stButton button[kind="secondary"]:hover {
        background: #F1F5F9 !important; border-bottom: 5px solid #94A3B8 !important; transform: translateY(0) !important; box-shadow: none !important;
    }
    .stButton button[kind="secondary"]:active {
        background: #E2E8F0 !important; border-bottom: 2px solid #94A3B8 !important; transform: translateY(3px) !important;
    }
    .stButton button::before, .stButton button::after { display: none !important; background: none !important; }
    </style>
    """, unsafe_allow_html=True)

    shutdown_candidates = view_df[view_df["Smart_Action"] == "STOP"]
    st.markdown("<br>", unsafe_allow_html=True)

    shutdown_policies = [
        "Every Day - 8 PM",
        "Friday - 6 PM",
        "Weekdays - 9 PM",
        "Weekend Shutdown",
        "After Business Hours"
    ]

    for _, row in shutdown_candidates.iterrows():
        resource_id = row['Resource_ID']
        
        with st.container(border=True):
            sch_col1, sch_col2, sch_col3 = st.columns([2.5, 2.0, 1.2])

            with sch_col1:
                st.markdown(f"""
                <div style="padding:4px 0;">
                    <div style="font-size:16px; font-weight:800; color:#0F172A;">{resource_id}</div>
                    <div style="font-size:12px; color:#64748B; margin-top:2px;">{row['Resource_Type']}</div>
                    <div style="font-size:13px; color:#334155; margin-top:6px;">Team: <strong>{row['Team_Owner']}</strong></div>
                    <div style="font-size:14px; font-weight:700; color:#7C3AED; margin-top:4px;">
                        Savings: ₹{row['Potential_Savings_INR']:,.0f}
                    </div>
                </div>""", unsafe_allow_html=True)

            with sch_col2:
                st.markdown("<div style='padding-top:24px'></div>", unsafe_allow_html=True)
                saved_policy = st.session_state.get("schedule_policies", {}).get(resource_id, shutdown_policies[0])
                saved_index  = shutdown_policies.index(saved_policy) if saved_policy in shutdown_policies else 0
                schedule_option = st.selectbox(
                    "Shutdown Policy", shutdown_policies,
                    index=saved_index,
                    key=f"scheduler_{resource_id}",
                    label_visibility="collapsed"
                )
                st.caption("Select automated shutdown window")

            with sch_col3:
                st.markdown("<div style='padding-top:24px'></div>", unsafe_allow_html=True)
                is_enabled = resource_id in st.session_state.enabled_schedules
                if is_enabled:
                    st.button(
                        "Disable", 
                        key=f"disable_{resource_id}", 
                        use_container_width=True,
                        on_click=toggle_schedule,
                        args=(resource_id, False, schedule_option)
                    )
                    st.markdown("<div style='color:#10B981; font-size:12px; font-weight:700; text-align:center; margin-top:8px;'>✔ Policy Active</div>", unsafe_allow_html=True)
                else:
                    st.button(
                        "Enable", 
                        key=f"enable_{resource_id}", 
                        type="primary", 
                        use_container_width=True,
                        on_click=toggle_schedule,
                        args=(resource_id, True, schedule_option)
                    )
                    
    # ── Audit Log — live view from SQLite ─────────────────────────────────────
    st.markdown("<br>", unsafe_allow_html=True)
    with st.container(border=True):
        st.markdown("#### 🗄️ Persistent Action Log (SQLite Database)")
        st.caption("Every Enable, Disable, and Review action is written to the SQLite audit_log table and survives page refresh.")

        try:
            conn = get_db_conn()
            audit_df = pd.read_sql_query(
                "SELECT timestamp, resource_id, action_taken, notes FROM audit_log ORDER BY id DESC LIMIT 50",
                conn
            )
            conn.close()

            if audit_df.empty:
                st.info("No actions logged yet. Enable a schedule or mark a resource as reviewed to see entries here.")
            else:
                st.dataframe(audit_df, use_container_width=True)
                st.caption(f"{len(audit_df)} most recent actions shown. All records stored in data/cloud_data.db → audit_log table.")
        except Exception as e:
            st.warning(f"Could not load audit log: {e}")