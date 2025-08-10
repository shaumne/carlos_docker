import types
from trade_executor import GoogleSheetTradeManager


def test_process_cell_updates_batch(monkeypatch):
    monkeypatch.setenv("UNIT_TEST_MODE", "1")
    mgr = GoogleSheetTradeManager()
    # Fake headers mapping
    mgr.worksheet.row_values = lambda idx: [
        'A','B','C','D','Buy Signal','F','G','H','I','J','K','L','M','N','O','P','Q','R','S','T','U','V','W','X','Y','Z','AA','AB','AC','AD','AE','AF','AG','AH','AI','AJ'
    ]
    called = {}
    def fake_update_cells(cells, value_input_option=None):
        called['n'] = len(cells)
        return True
    mgr.worksheet.update_cells = fake_update_cells

    updates = [
        {'id': '1', 'row_index': 2, 'column': 'Buy Signal', 'value': 'BUY'},
        {'id': '2', 'row_index': 2, 'column': 'AI', 'value': 'ignored'},  # Not present; will fail mapping
    ]

    # Only valid columns counted
    ok = mgr._process_cell_updates_batch([updates[0]])
    assert ok
    assert called.get('n', 0) == 1

