#!/usr/bin/env python3
import os
import sys
import json
import pandas as pd
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from local_db_queue import LocalDbQueue


def migrate(data_dir: str = "local_data"):
    os.makedirs(data_dir, exist_ok=True)
    db = LocalDbQueue(data_dir=data_dir)

    # pending_updates.xlsx -> pending_ops
    pending_xlsx = os.path.join(data_dir, "pending_updates.xlsx")
    if os.path.exists(pending_xlsx):
        try:
            df = pd.read_excel(pending_xlsx)
            migrated = 0
            for _, row in df.iterrows():
                try:
                    op_id = row.get('id') or None
                    db.add_cell_update(
                        row_index=int(row.get('row_index')),
                        column=str(row.get('column')),
                        value=row.get('value'),
                        update_type=str(row.get('type', 'cell_update')),
                        op_id=op_id,
                    )
                    migrated += 1
                except Exception:
                    continue
            print(f"Migrated {migrated} pending cell updates to SQLite")
        except Exception as e:
            print(f"Failed to migrate pending_updates.xlsx: {e}")

    # local_archive.xlsx -> no direct migration into Archive sheet; keep as is.
    # Optionally store a copy marker in DB meta for reference
    archive_xlsx = os.path.join(data_dir, "local_archive.xlsx")
    if os.path.exists(archive_xlsx):
        try:
            db.set_meta("local_archive_migrated_at", datetime.utcnow().isoformat())
            print("Recorded local archive presence in DB meta")
        except Exception as e:
            print(f"Failed to record archive meta: {e}")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "local_data"
    migrate(target)

