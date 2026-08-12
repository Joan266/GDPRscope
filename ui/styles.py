"""
GDPRScope UI — Design tokens and CSS.
Single source of truth for all visual styling.
Warm neutral palette, serif headings, monospace numbers.
"""


def inject_css() -> str:
    """Return the full CSS block for st.markdown(unsafe_allow_html=True)."""
    return f"<style>{CSS}</style>"


# ---- Palette ----
# Inspired by GDPRFine.com + legal editorial design
CREAM = "#FAF7F2"
CARD_BG = "#FFFFFF"
HEADER_NAVY = "#1B2A4A"
TEXT_PRIMARY = "#1A1A2E"
TEXT_SECONDARY = "#6B7280"
BORDER_WARM = "#E5E1DB"
ACCENT_BLUE = "#2563EB"
ACCENT_TEAL = "#0D9488"
SEVERITY_LOW = "#059669"
SEVERITY_MED = "#D97706"
SEVERITY_HIGH = "#DC2626"
BADGE_GREEN_BG = "#ECFDF5"
BADGE_GREEN_FG = "#065F46"
BADGE_AMBER_BG = "#FFFBEB"
BADGE_AMBER_FG = "#92400E"
BADGE_RED_BG = "#FEF2F2"
BADGE_RED_FG = "#991B1B"

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Lora:wght@500;600;700&family=DM+Sans:wght@400;500;600&family=JetBrains+Mono:wght@400;500;600&display=swap');

:root {
    --bg-cream: #FAF7F2;
    --bg-card: #FFFFFF;
    --header-navy: #1B2A4A;
    --text-primary: #1A1A2E;
    --text-secondary: #6B7280;
    --border-warm: #E5E1DB;
    --accent-blue: #2563EB;
    --accent-teal: #0D9488;
    --severity-low: #059669;
    --severity-med: #D97706;
    --severity-high: #DC2626;
}

.stApp {
    font-family: 'DM Sans', sans-serif;
    background-color: var(--bg-cream);
    color: var(--text-primary);
}

/* Headings — serif for authority */
h1, h2, h3, .jm-heading {
    font-family: 'Lora', serif !important;
    color: var(--header-navy);
}

/* Monospace numbers */
.jm-number {
    font-family: 'JetBrains Mono', monospace;
    font-weight: 600;
}

.jm-big-number {
    font-family: 'JetBrains Mono', monospace;
    font-size: 2rem;
    font-weight: 700;
    line-height: 1.1;
}

.jm-label {
    font-size: 0.82rem;
    color: var(--text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    margin-bottom: 2px;
}

/* Cards */
.jm-card {
    background: var(--bg-card);
    border: 1px solid var(--border-warm);
    border-radius: 8px;
    padding: 14px 18px;
    margin-bottom: 10px;
}

.jm-card-hover:hover {
    border-color: var(--accent-blue);
    box-shadow: 0 2px 8px rgba(37, 99, 235, 0.08);
}

/* Range bar — Zillow-style */
.jm-range-container {
    position: relative;
    height: 36px;
    margin: 16px 0 8px;
}

.jm-range-track {
    position: absolute;
    top: 14px;
    left: 0;
    right: 0;
    height: 4px;
    background: var(--border-warm);
    border-radius: 2px;
}

.jm-range-band {
    position: absolute;
    top: 8px;
    height: 16px;
    border-radius: 4px;
    opacity: 0.85;
}

.jm-range-marker {
    position: absolute;
    top: 4px;
    width: 3px;
    height: 24px;
    border-radius: 2px;
    transform: translateX(-1px);
}

.jm-range-label {
    position: absolute;
    top: 34px;
    transform: translateX(-50%);
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.75rem;
    font-weight: 500;
    white-space: nowrap;
}

/* Badges */
.jm-badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 12px;
    font-size: 0.8rem;
    font-weight: 500;
}

.jm-badge-green {
    background: #ECFDF5;
    color: #065F46;
}

.jm-badge-amber {
    background: #FFFBEB;
    color: #92400E;
}

.jm-badge-red {
    background: #FEF2F2;
    color: #991B1B;
}

/* Factor tags */
.jm-factor {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 12px;
    border-radius: 6px;
    font-size: 0.85rem;
    margin: 3px;
}

.jm-factor-mitigating {
    background: #ECFDF5;
    color: #065F46;
    border: 1px solid #A7F3D0;
}

.jm-factor-aggravating {
    background: #FEF2F2;
    color: #991B1B;
    border: 1px solid #FECACA;
}

/* Disclaimer */
.jm-disclaimer {
    background: #FFFBEB;
    border-left: 3px solid var(--severity-med);
    padding: 10px 14px;
    font-size: 0.82rem;
    color: var(--text-secondary);
    margin-top: 16px;
    border-radius: 0 6px 6px 0;
}

/* ---- GDPRScope compact header ---- */
.gs-header {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 12px 32px;
    padding: 10px 0 12px;
    border-bottom: 1px solid var(--border-warm);
    margin-bottom: 20px;
}

.gs-brand {
    display: flex;
    align-items: baseline;
    gap: 14px;
}

.gs-logo {
    font-family: 'Lora', serif;
    font-size: 1.5rem;
    font-weight: 700;
    color: var(--header-navy);
    letter-spacing: -0.02em;
}

.gs-subtitle {
    font-size: 0.85rem;
    color: var(--text-secondary);
    font-weight: 400;
}

.gs-stats {
    display: flex;
    align-items: baseline;
    gap: 6px;
    font-size: 0.85rem;
    color: var(--text-secondary);
}

.gs-stat strong {
    font-family: 'JetBrains Mono', monospace;
    font-weight: 600;
    color: var(--text-primary);
    font-size: 0.9rem;
}

.gs-stat-sep {
    color: var(--border-warm);
    margin: 0 2px;
}

/* ---- Section spacing ---- */
[data-testid="stTabs"] {
    margin-top: 4px;
}

/* Space between tab content and charts */
[data-testid="stVerticalBlock"] > [data-testid="stVerticalBlock"] {
    gap: 1rem;
}

/* Chart containers — breathing room */
[data-testid="stVegaLiteChart"],
[data-testid="stArrowVegaLiteChart"] {
    background: var(--bg-card);
    border: 1px solid var(--border-warm);
    border-radius: 8px;
    padding: 16px;
    margin-top: 8px;
    margin-bottom: 8px;
}

/* Sparkline container */
.jm-sparkline {
    display: inline-block;
    vertical-align: middle;
}

/* Comparison bar */
.jm-comp-bar {
    height: 8px;
    border-radius: 4px;
    background: var(--accent-blue);
    opacity: 0.7;
    transition: width 0.3s ease;
}

/* Hide Streamlit branding */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header[data-testid="stHeader"] {background: transparent;}
"""
