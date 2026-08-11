"""Number and text formatting helpers for the UI."""

import html as _html


def format_eur(amount: int | float | None) -> str:
    """Format euro amounts: 1_500_000 -> 'EUR 1.5M', 45_000 -> 'EUR 45K'."""
    if amount is None or amount == 0:
        return "N/A"
    amount = int(amount)
    if amount >= 1_000_000:
        return f"EUR {amount / 1_000_000:.1f}M"
    if amount >= 1_000:
        return f"EUR {amount / 1_000:.0f}K"
    return f"EUR {amount:,}"


def format_eur_short(amount: int | float | None) -> str:
    """Shorter format without EUR prefix: '1.5M', '45K'."""
    if amount is None or amount == 0:
        return "N/A"
    amount = int(amount)
    if amount >= 1_000_000:
        return f"{amount / 1_000_000:.1f}M"
    if amount >= 1_000:
        return f"{amount / 1_000:.0f}K"
    return f"{amount:,}"


def format_pct(value: float) -> str:
    """Format percentage: 0.156 -> '15.6%', 23.4 -> '23.4%'."""
    if value > 1:
        return f"{value:.1f}%"
    return f"{value * 100:.1f}%"


def trend_arrow(direction: str) -> str:
    """Map trend direction to arrow symbol."""
    arrows = {
        "increasing": "↑",
        "decreasing": "↓",
        "stable": "→",
    }
    return arrows.get(direction, "→")


def severity_color(amount: int | float | None) -> str:
    """Return CSS color based on fine severity."""
    if amount is None:
        return "#6B7280"
    if amount >= 1_000_000:
        return "#DC2626"
    if amount >= 100_000:
        return "#D97706"
    return "#059669"


def escape(text: str | None) -> str:
    """HTML-escape text to prevent XSS."""
    if text is None:
        return ""
    return _html.escape(str(text))
