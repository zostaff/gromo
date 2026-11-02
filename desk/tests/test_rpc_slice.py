import base64

from desk.sources import solana_rpc as R

TOK = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + R._B58.index(ch)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return b"\0" * (len(s) - len(s.lstrip("1"))) + raw


def test_b58_roundtrip():
    assert R.b58(b58decode(TOK)) == TOK
    assert R.b58(bytes(32)) == "1" * 32


def test_decode_slice_with_delegate():
    owner, dele = b58decode(TOK), b58decode("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
    raw = owner + (123456).to_bytes(8, "little") + (1).to_bytes(4, "little") + dele + b"\1" + bytes(12) \
        + (777).to_bytes(8, "little")
    assert len(raw) == 97
    info = R.decode_slice(base64.b64encode(raw).decode())
    assert info == {"owner": TOK, "tokenAmount": {"amount": "123456"},
                    "delegate": "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P", "delegatedAmount": {"amount": "777"}}


def test_decode_slice_no_delegate():
    raw = b58decode(TOK) + (5).to_bytes(8, "little") + bytes(57)
    info = R.decode_slice(base64.b64encode(raw).decode())
    assert "delegate" not in info and info["tokenAmount"]["amount"] == "5"
