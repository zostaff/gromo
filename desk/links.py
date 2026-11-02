"""The ONLY place FOMO URLs are built. Every button, push, page and API answer uses these.

The referral code comes from FOMO_REF (your own FOMO referral code). Empty = plain links,
no referral. Never hardcode someone else's code here.
"""
import logging
import os
from urllib.parse import quote

log = logging.getLogger("links")

CHAIN_ID = {"solana": 1399811149, "robinhood": 4663, "base": 8453, "bnb": 56}
CHAIN_LABEL = {"solana": "Solana", "robinhood": "Robinhood Chain", "base": "Base", "bnb": "BNB Chain"}
FOMO = "https://fomo.family"


def ref() -> str:
    return os.environ.get("FOMO_REF", "").strip()


def signup_url() -> str:
    r = ref()
    return f"{FOMO}/r/{quote(r)}" if r else FOMO


def coin_url(address: str, chain: str = "solana") -> str:
    if chain not in CHAIN_ID:
        log.warning("coin_url: unknown chain %r for %s", chain, address)
        return signup_url()
    url = f"{FOMO}/coin?address={quote(address)}&chainId={CHAIN_ID[chain]}"
    r = ref()
    if r:
        url += f"&r={quote(r)}"
    return url + "&source=share_link"


def profile_url(handle: str) -> str:
    h = quote(handle.lstrip("@"))
    url = f"{FOMO}/profile/{h}?source=share_link"
    r = ref()
    return url + (f"&r={quote(r)}" if r else "")


def grokhood_url(address: str) -> str:
    """GROKHOOD token page (Robinhood Chain coins only)."""
    return f"https://grokhood.tech/token/{quote(address)}"
