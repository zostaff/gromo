"""Desk hot wallet (Solana) for auto trading. The key comes only from env WALLET_PRIVATE_KEY:
a base58 secret key (64 bytes, Phantom export) or a JSON byte array (solana-keygen file).
Never logged, never returned by the API. Fund it with only what the desk may lose.
"""
from __future__ import annotations

import base64
import json
import os

_kp = None


def _load():
    global _kp
    if _kp is not None:
        return _kp
    raw = (os.environ.get("WALLET_PRIVATE_KEY") or "").strip()
    if not raw:
        return None
    from solders.keypair import Keypair
    try:
        _kp = Keypair.from_bytes(bytes(json.loads(raw))) if raw.startswith("[") else Keypair.from_base58_string(raw)
    except Exception:  # noqa: BLE001 - a bad key must not crash the desk, and must not be echoed
        _kp = None
        raise ValueError("WALLET_PRIVATE_KEY is not a valid Solana secret key") from None
    return _kp


def available() -> bool:
    try:
        return _load() is not None
    except ValueError:
        return False


def pubkey() -> str | None:
    try:
        kp = _load()
    except ValueError:
        return None
    return str(kp.pubkey()) if kp else None


def sign(tx_b64: str) -> str:
    """Sign a base64 versioned transaction with the desk key (only our signer slot). -> base64."""
    from solders.message import to_bytes_versioned
    from solders.transaction import VersionedTransaction
    kp = _load()
    if kp is None:
        raise ValueError("no wallet")
    tx = VersionedTransaction.from_bytes(base64.b64decode(tx_b64))
    msg = tx.message
    n = msg.header.num_required_signatures
    signers = list(msg.account_keys[:n])
    if kp.pubkey() not in signers:
        raise ValueError("the transaction does not ask for the desk wallet's signature")
    sigs = list(tx.signatures)
    sigs[signers.index(kp.pubkey())] = kp.sign_message(to_bytes_versioned(msg))
    return base64.b64encode(bytes(VersionedTransaction.populate(msg, sigs))).decode()
