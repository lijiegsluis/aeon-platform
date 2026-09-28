"""Path and runtime configuration for the local research platform."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"

# --- market parameters (shared by the DCF engine, scenarios and the Excel) ----
# Curated local-currency WACC overrides for markets we've hand-tuned (frontier-market
# cost of capital, calibrated against real filings). Anything not listed here falls
# back to a formula-driven estimate — see country_risk.implied_wacc().
WACC_BY_COUNTRY = {
    "Kenya": 0.165, "Nigeria": 0.19, "South Africa": 0.145, "Ghana": 0.22,
    "Egypt": 0.24, "Morocco": 0.095, "Senegal": 0.105, "United Kingdom": 0.14,
}
# Frontier bank cost of equity — materially above the WACC proxy. Same override/
# fallback split as WACC_BY_COUNTRY.
BANK_COE_BY_COUNTRY = {
    "Kenya": 0.215, "Nigeria": 0.255, "South Africa": 0.155, "Ghana": 0.25,
    "Egypt": 0.265, "Morocco": 0.11, "Senegal": 0.12, "United Kingdom": 0.10,
}
SUSTAINABLE_ROE_CAP = 0.20  # a top-tier bank sustaining >20% ROE long-term is rare

RAW_DOCS_DIR = DATA_DIR / "raw_docs"
PROCESSED_DIR = DATA_DIR / "processed"
OUTPUT_DIR = PROJECT_ROOT / "output"
TEMPLATE_DIR = PROJECT_ROOT / "aeon_nimbus" / "templates"
DEFAULT_DATABASE_PATH = DATA_DIR / "aeon_nimbus_research.sqlite"
DEFAULT_OUTPUT_PATH = OUTPUT_DIR / "aeon_nimbus_research_platform.html"


@dataclass(frozen=True)
class RuntimeConfig:
    """Simple dependency-free config object used by CLI and modules."""

    project_root: Path = PROJECT_ROOT
    data_dir: Path = DATA_DIR
    raw_docs_dir: Path = RAW_DOCS_DIR
    processed_dir: Path = PROCESSED_DIR
    output_dir: Path = OUTPUT_DIR
    template_dir: Path = TEMPLATE_DIR
    database_path: Path = DEFAULT_DATABASE_PATH
    output_path: Path = DEFAULT_OUTPUT_PATH


def ensure_directories(config: RuntimeConfig | None = None) -> RuntimeConfig:
    """Create the expected local folders and return the active config."""

    cfg = config or RuntimeConfig()
    for path in (cfg.data_dir, cfg.raw_docs_dir, cfg.processed_dir, cfg.output_dir):
        path.mkdir(parents=True, exist_ok=True)
    return cfg


def model_path(slug: str) -> "Path":
    d = Path(os.environ.get("OUTPUT_DIR", PROJECT_ROOT / "output")) / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{slug}_model.xlsx"
