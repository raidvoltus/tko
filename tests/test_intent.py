from pathlib import Path
from tko.execution.intent import IntentStore, OrderIntentStatus, generate_client_order_id

def test_client_id_stable_and_unique(tmp_path: Path):
    a = generate_client_order_id("buy", "BTC/IDR")
    b = generate_client_order_id("buy", "BTC/IDR")
    assert a != b
    assert a.startswith("TKO-")
    assert len(a) <= 48

def test_intent_persisted_before_submit(tmp_path: Path):
    store = IntentStore(tmp_path / "intents.json")
    intent = store.create(symbol="BTC/IDR", side="buy", quote_amount=100000, last_price=1e9, reason="test")
    assert intent.status == OrderIntentStatus.PERSISTED
    assert intent.client_order_id
    store2 = IntentStore(tmp_path / "intents.json")
    loaded = store2.by_client_id(intent.client_order_id)
    assert loaded is not None
    assert loaded.client_order_id == intent.client_order_id

def test_client_id_unchanged_on_update(tmp_path: Path):
    store = IntentStore(tmp_path / "intents.json")
    intent = store.create(symbol="BTC/IDR", side="sell", base_amount=0.01)
    cid = intent.client_order_id
    intent.status = OrderIntentStatus.UNKNOWN
    store.update(intent)
    again = store.by_client_id(cid)
    assert again is not None
    assert again.client_order_id == cid
    assert again.status == OrderIntentStatus.UNKNOWN

def test_blocking_intent(tmp_path: Path):
    store = IntentStore(tmp_path / "intents.json")
    intent = store.create(symbol="BTC/IDR", side="buy", quote_amount=1)
    intent.status = OrderIntentStatus.SUBMITTING
    store.update(intent)
    assert store.has_blocking_intent("BTC/IDR", "buy")
