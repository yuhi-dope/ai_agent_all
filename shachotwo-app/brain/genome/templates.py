"""Template registry — loads JSON files from data/ directory."""
import json
import logging
from pathlib import Path

from brain.genome.models import GenomeTemplate

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, GenomeTemplate] = {}
DATA_DIR = Path(__file__).parent / "data"


def load_templates() -> None:
    """Load all JSON templates from data/ directory into registry."""
    global _REGISTRY
    _REGISTRY.clear()

    if not DATA_DIR.exists():
        logger.warning(f"Template data directory not found: {DATA_DIR}")
        return

    for path in DATA_DIR.glob("*.json"):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            template = GenomeTemplate(**data)
            _REGISTRY[template.id] = template
            logger.info(f"Loaded template: {template.id} ({template.name}, {template.total_items} items)")
        except Exception as e:
            logger.error(f"Failed to load template {path.name}: {e}")


def get_template(template_id: str) -> GenomeTemplate | None:
    if not _REGISTRY:
        load_templates()
    return _REGISTRY.get(template_id)


def list_templates() -> list[GenomeTemplate]:
    if not _REGISTRY:
        load_templates()
    return list(_REGISTRY.values())


def get_template_for_industry(industry: str) -> GenomeTemplate | None:
    """Match template by industry (partial match)."""
    if not _REGISTRY:
        load_templates()
    industry_lower = industry.lower()
    for t in _REGISTRY.values():
        if industry_lower in t.industry.lower() or t.industry.lower() in industry_lower:
            return t
    return None
