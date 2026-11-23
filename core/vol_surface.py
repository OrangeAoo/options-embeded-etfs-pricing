"""Implied-vol surfaces used to price option legs.

Stored as a grid of (tenor_days, moneyness = K / S_ref, iv), matching a Bloomberg
OVDV export once you add a moneyness column.

Interpolation:
  * strikes — linear in log-moneyness, flat outside the grid
  * tenors  — linear in total variance (iv^2 * T), flat iv outside the grid

Spot-vol dynamics for scenario shocks:
  * sticky_strike — a given strike keeps its vol (moneyness vs ref_spot)
  * sticky_delta  — the smile moves with spot (moneyness vs shocked spot)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Union

import numpy as np
import pandas as pd

VOL_MODES = ("sticky_strike", "sticky_delta")


@dataclass
class VolSurface:
    tenors_days: np.ndarray       # shape (n_tenors,)
    log_moneyness: np.ndarray     # shape (n_strikes,)
    iv_grid: np.ndarray           # shape (n_tenors, n_strikes)
    ref_spot: float
    name: str = ""

    @classmethod
    def from_frame(cls, df: pd.DataFrame, ref_spot: float, name: str = "") -> "VolSurface":
        """df columns: tenor_days, moneyness (K/S_ref), iv (decimal)."""
        need = {"tenor_days", "moneyness", "iv"}
        missing = need - set(df.columns)
        if missing:
            raise ValueError(f"surface {name!r} missing columns {missing}")

        grid = df.pivot_table(index="tenor_days", columns="moneyness", values="iv").sort_index()
        grid = grid.sort_index(axis=1).interpolate(axis=1, limit_direction="both")
        return cls(
            tenors_days=grid.index.to_numpy(float),
            log_moneyness=np.log(grid.columns.to_numpy(float)),
            iv_grid=grid.to_numpy(float),
            ref_spot=float(ref_spot),
            name=name,
        )

    @classmethod
    def from_csv(cls, path: Union[str, Path], ref_spot: float) -> "VolSurface":
        path = Path(path)
        return cls.from_frame(pd.read_csv(path, comment="#"), ref_spot, name=path.stem)

    @classmethod
    def synthetic(
        cls,
        ref_spot: float,
        atm: float = 0.16,
        skew: float = -0.35,
        curvature: float = 0.6,
        term_slope: float = 0.015,
        name: str = "synthetic",
    ) -> "VolSurface":
        """Parametric smile for demos/tests: iv = atm + term*sqrt(T) + skew*x + curv*x^2."""
        tenors = np.array([1, 7, 14, 30, 60, 91, 182, 365], float)
        log_m = np.log(np.array([0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15, 1.2, 1.3]))
        time_years = tenors[:, None] / 365.0
        iv = (
            atm
            + term_slope * np.sqrt(time_years)
            + skew * log_m[None, :]
            + curvature * log_m[None, :] ** 2
        )
        return cls(tenors, log_m, np.clip(iv, 0.05, 1.5), float(ref_spot), name)

    def to_frame(self) -> pd.DataFrame:
        rows = [
            (tenor, float(np.exp(log_m)), self.iv_grid[i, j])
            for i, tenor in enumerate(self.tenors_days)
            for j, log_m in enumerate(self.log_moneyness)
        ]
        return pd.DataFrame(rows, columns=["tenor_days", "moneyness", "iv"])

    def iv(self, strike, time_years, spot=None, mode: str = "sticky_strike") -> np.ndarray:
        """Vectorised implied vol for strikes / maturities."""
        if mode not in VOL_MODES:
            raise ValueError(f"mode must be one of {VOL_MODES}")

        strike = np.atleast_1d(np.asarray(strike, float))
        time_years = np.atleast_1d(np.asarray(time_years, float))
        strike, time_years = np.broadcast_arrays(strike, time_years)

        if mode == "sticky_strike" or spot is None:
            anchor = self.ref_spot
        else:
            anchor = spot
        anchor = np.broadcast_to(np.asarray(anchor, float), strike.shape)
        log_m = np.log(strike / anchor)

        # IV at each grid tenor for the requested moneyness.
        per_tenor = np.vstack([
            np.interp(log_m, self.log_moneyness, row) for row in self.iv_grid
        ])

        tenors = self.tenors_days
        if len(tenors) == 1:
            return per_tenor[0].reshape(log_m.shape)

        # Interpolate in total variance between neighbouring tenors.
        target_days = np.clip(time_years.ravel() * 365.0, tenors[0], tenors[-1])
        right = np.clip(np.searchsorted(tenors, target_days, side="left"), 1, len(tenors) - 1)
        cols = np.arange(target_days.size)
        t0, t1 = tenors[right - 1], tenors[right]
        var0 = per_tenor[right - 1, cols] ** 2 * t0
        var1 = per_tenor[right, cols] ** 2 * t1
        total_var = var0 + (var1 - var0) * (target_days - t0) / (t1 - t0)
        return np.sqrt(np.maximum(total_var, 1e-12) / target_days).reshape(log_m.shape)


class FlatVol:
    """Constant vol for every strike and tenor. Useful for sanity checks."""

    def __init__(self, sigma: float):
        self.sigma = float(sigma)
        self.ref_spot = np.nan

    def iv(self, strike, time_years, spot=None, mode: str = "sticky_strike") -> np.ndarray:
        strike = np.atleast_1d(np.asarray(strike, float))
        shape = np.broadcast(strike, np.asarray(time_years, float)).shape
        return np.full(shape, self.sigma)
