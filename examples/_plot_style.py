"""Shared plotting helpers for the example notebooks."""
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick

COLORS = {
    "observed": "#2b6cb0", "inferred": "#dd8452", "skipped": "#a0aec0",
    "excluded": "#a0aec0", "missing": "#e53e3e",
    "pos": "#2f855a", "neg": "#c53030", "accent": "#2b6cb0", "grey": "#718096",
}
BUCKET_COLORS = ["#2b6cb0", "#dd8452", "#38a169", "#805ad5", "#d69e2e", "#e53e3e", "#319795", "#718096"]


def setup():
    plt.rcParams.update({
        "figure.figsize": (10, 5), "figure.dpi": 110, "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "grid.alpha": 0.25, "axes.titleweight": "bold", "axes.titlesize": 13,
        "axes.titlelocation": "left", "font.size": 10.5, "legend.frameon": False,
    })


def _fmt(x, unit, suffix, decimals=None):
    v = x / unit
    d = decimals if decimals is not None else (0 if abs(v) >= 10 or v == 0 else 1)
    return f"{'-' if v < 0 else ''}${abs(v):,.{d}f}{suffix}"


def usd(ax, axis="y", unit=1e6, suffix="M"):
    fmt = mtick.FuncFormatter(lambda x, _: _fmt(x, unit, suffix))
    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(fmt)


def pct(ax, axis="x", decimals=0):
    (ax.xaxis if axis == "x" else ax.yaxis).set_major_formatter(mtick.PercentFormatter(1.0, decimals=decimals))


def money(x, unit=None, suffix=None):
    """Human-readable dollars; picks bn/M/k automatically unless unit is given."""
    if unit is None:
        unit, suffix = next(((u, s) for u, s in ((1e9, "bn"), (1e6, "M"), (1e3, "k")) if abs(x) >= u), (1, ""))
    return _fmt(x, unit, suffix, decimals=1)
