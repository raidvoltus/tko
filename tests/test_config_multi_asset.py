from tko.core.config import Settings


def test_quote_asset_list_priority():
    s = Settings(quote_asset="IDR", quote_assets="USDT,IDR,USDC")
    lst = s.quote_asset_list()
    assert lst[0] == "IDR"
    assert "USDT" in lst


def test_tradeable_bases_include_eth():
    s = Settings()
    bases = s.tradeable_base_list()
    assert "BTC" in bases
    assert "ETH" in bases


def test_min_balance_stable_vs_idr():
    s = Settings(min_quote_balance=50000, min_quote_balance_usdt=5)
    assert s.min_balance_for_quote("IDR") == 50000
    assert s.min_balance_for_quote("USDT") == 5
