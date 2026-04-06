from __future__ import annotations

import pytest

from app.shopify.executor import _raise_if_user_errors, _to_shopify_gid


def test_to_shopify_gid_numeric():
    assert _to_shopify_gid("ProductVariant", "8757919776958") == "gid://shopify/ProductVariant/8757919776958"


def test_to_shopify_gid_already_gid():
    g = "gid://shopify/ProductVariant/1"
    assert _to_shopify_gid("ProductVariant", g) == g


def test_raise_if_user_errors_empty():
    _raise_if_user_errors({"userErrors": []})
    _raise_if_user_errors({"productVariant": {"id": "x"}})


def test_raise_if_user_errors_populated():
    with pytest.raises(RuntimeError, match="Variant does not exist"):
        _raise_if_user_errors({"userErrors": [{"message": "Variant does not exist"}]})
