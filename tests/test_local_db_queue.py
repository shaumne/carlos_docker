import os
import shutil
import tempfile
from local_db_queue import LocalDbQueue


def test_crud_and_idempotency():
    tmp = tempfile.mkdtemp()
    try:
        q = LocalDbQueue(data_dir=tmp)
        # Add two updates, same row/col last one should replace on same id if provided
        q.add_cell_update(2, 'Order Placed?', 'ORDER_PLACED')
        q.add_cell_update(2, 'Order Placed?', 'ORDER_PLACED')  # duplicate acceptable
        counts = q.get_pending_count()
        assert counts['updates'] >= 1

        # Archive idempotency per row
        q.add_archive_operation(5, {"Coin": "BTC"}, ["Take Profit"]) 
        q.add_archive_operation(5, {"Coin": "BTC"}, ["Take Profit"])  # ignored
        counts = q.get_pending_count()
        assert counts['archives'] == 1

        # Get batch and complete
        batch = q.get_batch_for_processing(max_batch_size=50)
        assert batch['updates'] or batch['archives']
        all_ids = [u['id'] for u in batch['updates']] + [a['id'] for a in batch['archives']]
        q.mark_batch_completed(all_ids)
        counts = q.get_pending_count()
        assert counts['updates'] == 0
        assert counts['archives'] == 0
    finally:
        shutil.rmtree(tmp)

