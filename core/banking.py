"""IBAN helpers. Pure functions, no Django: safe to use from autopilot/ rules too."""
from __future__ import annotations

import re

_IT_CIN_ODD = [1, 0, 5, 7, 9, 13, 15, 17, 19, 21, 2, 4, 18, 20, 11, 3, 6, 8, 12, 14, 16, 10, 22, 25, 24, 23]
_IBAN_LENGTHS = {"IT": 27, "NL": 18, "TR": 26, "DE": 22, "FR": 27, "ES": 24, "BE": 16, "AE": 23, "GB": 22}


def normalize_iban(iban: str | None) -> str:
    """Strip spaces, upper-case. Compare IBANs only in this form."""
    return re.sub(r"\s+", "", iban or "").upper()


def format_iban(iban: str | None) -> str:
    """'IT60X0542811101000000123456' -> 'IT60 X054 2811 1010 0000 0123 456'."""
    iban = normalize_iban(iban)
    return " ".join(iban[i : i + 4] for i in range(0, len(iban), 4))


def mask_iban(iban: str | None) -> str:
    """Show only the last 4 characters (approvals list)."""
    iban = normalize_iban(iban)
    return f"•••• {iban[-4:]}" if iban else ""


def _mod97(s: str) -> int:
    return int("".join(str(int(c, 36)) for c in s)) % 97


def iban_is_valid(iban: str | None) -> bool:
    iban = normalize_iban(iban)
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", iban):
        return False
    expected_len = _IBAN_LENGTHS.get(iban[:2])
    if expected_len and len(iban) != expected_len:
        return False
    return _mod97(iban[4:] + iban[:4]) == 1


def make_iban(country: str, bban: str) -> str:
    """Build a checksum-valid IBAN from a country code and BBAN (fake details are fine)."""
    bban = normalize_iban(bban)
    check = 98 - _mod97(bban + country.upper() + "00")
    return f"{country.upper()}{check:02d}{bban}"


def _it_cin(abi: str, cab: str, account: str) -> str:
    total = 0
    for i, c in enumerate(abi + cab + account):
        v = int(c) if c.isdigit() else ord(c) - ord("A")
        total += _IT_CIN_ODD[v] if i % 2 == 0 else v
    return chr(ord("A") + total % 26)


def make_it_iban(abi: str, cab: str, account: str | int) -> str:
    """Italian IBAN with a correct CIN check letter."""
    abi, cab, account = abi.zfill(5), cab.zfill(5), str(account).zfill(12).upper()
    return make_iban("IT", _it_cin(abi, cab, account) + abi + cab + account)


# --------------------------------------------------------------------------
# Italian VAT number (Partita IVA)
# --------------------------------------------------------------------------
def _it_vat_check_digit(first10: str) -> int:
    total = 0
    for i, c in enumerate(first10):
        d = int(c)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - total % 10) % 10


def make_it_vat(first10: str | int) -> str:
    """'IT' + 11 digits with a valid check digit."""
    first10 = str(first10).zfill(10)
    return f"IT{first10}{_it_vat_check_digit(first10)}"


def it_vat_is_valid(vat: str | None) -> bool:
    m = re.fullmatch(r"(?:IT)?(\d{11})", re.sub(r"[\s.\-]", "", vat or "").upper())
    return bool(m) and _it_vat_check_digit(m.group(1)[:10]) == int(m.group(1)[10])
