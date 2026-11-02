"""FOMO fee model for PAPER PnL. Per side. Gas is 0 (FOMO pays it on Solana).

Tiers (FOMO Solana, as of 2026-11; re-check in the FOMO app): $5-$47.50 -> 2%,
$47.50-$190 -> flat $0.95, above -> 0.50%. desk.fees_ref_discount applies if you signed up
through a referral link.
"""


def side_fee(ticket_usd: float, ref_discount: float = 0.0) -> float:
    t = max(0.0, float(ticket_usd))
    if t <= 0:
        return 0.0
    if t < 47.50:
        fee = t * 0.02
    elif t <= 190:
        fee = 0.95
    else:
        fee = t * 0.005
    return round(fee * (1 - ref_discount), 4)


def round_trip(entry_usd: float, exit_usd: float, ref_discount: float = 0.0) -> float:
    return round(side_fee(entry_usd, ref_discount) + side_fee(exit_usd, ref_discount), 4)
