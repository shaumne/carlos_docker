import types
from trade_executor import GoogleSheetTradeManager
import os


class DummyAPI:
    def __init__(self):
        self.state = {}

    def send_request(self, method, params=None):
        if method == "private/cancel-order":
            oid = params.get("order_id")
            self.state[oid] = "CANCELED"
            return {"code": 0, "result": {}}
        if method in ("private/get-order-history", "private/get-trades"):
            return {"code": 0, "result": {"data": []}}
        return {"code": 0, "result": {}}

    def get_order_status(self, order_id):
        return self.state.get(order_id, "FILLED")


def test_cancel_opposite_on_fill(monkeypatch):
    monkeypatch.setenv("UNIT_TEST_MODE", "1")
    mgr = GoogleSheetTradeManager()
    # Monkeypatch external dependencies not relevant for this unit test
    mgr.exchange_api = DummyAPI()
    mgr.worksheet = types.SimpleNamespace(row_values=lambda *_: ["Coin"], batch_update=lambda *a, **k: True)
    mgr.archive_worksheet = types.SimpleNamespace(get_all_records=lambda: [], get_all_values=lambda: [["H"]], update=lambda *a, **k: True, row_values=lambda *_: ["", "BTC"])

    symbol = "BTC_USDT"
    mgr.active_positions[symbol] = {
        'row_index': 2,
        'tp_order_id': 'tp1',
        'sl_order_id': 'sl1',
        'status': 'POSITION_ACTIVE'
    }

    # Assume TP filled, SL should be cancelled
    ok = mgr.cancel_opposite_order(symbol, 'tp1')
    assert ok
    assert mgr.exchange_api.get_order_status('sl1') == 'CANCELED'

