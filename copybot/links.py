"""FOMO URLs for the copy bot: re-exported from desk/links.py (the only place they are built)."""
from desk.links import CHAIN_ID, CHAIN_LABEL, coin_url, grokhood_url, profile_url, ref, signup_url  # noqa: F401

CHAIN_BY_ID = {v: k for k, v in CHAIN_ID.items()}


def chain_of(chain_id) -> str | None:
    """FOMO chainId -> desk chain name, None if unknown."""
    try:
        return CHAIN_BY_ID.get(int(chain_id))
    except (TypeError, ValueError):
        return None

# FOMO's own app API host (read-only calls in fomo_api.py). Kept here: no fomo.family string elsewhere.
FOMO_API = "https://prod-api.fomo.family"
