from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.models import SHARE_AMOUNT, SHARE_EQUAL, SHARE_PERCENT
from app.services.splits import allocate, split_evenly

D = Decimal


def test_equal_split_adds_up_exactly():
    parts = split_evenly(D("100"), 3)
    assert sum(parts) == D("100")
    assert max(parts) - min(parts) <= D("0.01")


def test_fixed_then_equal():
    # ₹1000 among 4 with one fixed at ₹400 gives ₹400 + 3 × ₹200 (the README example)
    shares = [(SHARE_AMOUNT, D("400"))] + [(SHARE_EQUAL, None)] * 3
    assert allocate(D("1000"), shares) == [D("400"), D("200"), D("200"), D("200")]


def test_percent_and_equal():
    result = allocate(D("200"), [(SHARE_PERCENT, D("50")), (SHARE_EQUAL, None), (SHARE_EQUAL, None)])
    assert result == [D("100"), D("50"), D("50")]
    assert sum(result) == D("200")


def test_fixed_more_than_total_rejected():
    with pytest.raises(HTTPException):
        allocate(D("100"), [(SHARE_AMOUNT, D("150")), (SHARE_EQUAL, None)])


def test_percent_over_100_rejected():
    with pytest.raises(HTTPException):
        allocate(D("100"), [(SHARE_PERCENT, D("60")), (SHARE_PERCENT, D("50"))])
