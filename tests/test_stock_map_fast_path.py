from unittest.mock import patch

import api.stock_map as stock_map_mod


def test_reverse_stock_map_cached_only_does_not_trigger_cold_load():
    """Cold cache returns {} and never triggers a slow AkShare load."""
    with (
        patch.object(stock_map_mod, "_cn_stock_map", None),
        patch.object(stock_map_mod, "_cn_stock_reverse_map", None),
        patch.object(
            stock_map_mod,
            "_load_cn_stock_map",
            side_effect=AssertionError("slow load should not run"),
        ),
    ):
        assert stock_map_mod._get_reverse_stock_map_cached_only() == {}


def test_reverse_stock_map_cached_only_uses_existing_cache():
    """Warm cache returns the cached code->name map without reloading."""
    forward = {"贵州茅台": "600519.SH", "宁德时代": "300750.SZ"}
    reverse = {"600519.SH": "贵州茅台", "300750.SZ": "宁德时代"}
    with (
        patch.object(stock_map_mod, "_cn_stock_map", forward),
        patch.object(stock_map_mod, "_cn_stock_reverse_map", reverse),
        patch.object(
            stock_map_mod,
            "_load_cn_stock_map",
            side_effect=AssertionError("slow load should not run"),
        ),
    ):
        assert stock_map_mod._get_reverse_stock_map_cached_only() == reverse
