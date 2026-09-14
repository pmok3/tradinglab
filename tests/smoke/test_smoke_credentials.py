"""Focused acceptance gate for integrated Schwab browser authorization."""
from .test_smoke_full import check_d0b_schwab_credentials_sign_in


def test_credentials_schwab_sign_in(app):
    check_d0b_schwab_credentials_sign_in(app)
