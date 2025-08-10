import os
import sqlite3
import threading
import json
from datetime import datetime
from typing import Dict, List, Tuple, Any


class LocalDbQueue:
    """
    Durable local queue and state store backed by SQLite.

    Tables:
      - pending_ops(id TEXT PK, type TEXT, row_index INTEGER, column TEXT, value TEXT,
                    retries INTEGER, created_at TEXT, last_attempt TEXT)
      - active_positions(symbol TEXT PK, buy_order_id TEXT, tp_order_id TEXT, sl_order_id TEXT,
                         quantity REAL, status TEXT, last_update TEXT,
                         price REAL, row_index INTEGER, take_profit REAL, stop_loss REAL,
                         archived INTEGER)
      - meta(key TEXT PK, value TEXT)
    """

    def __init__(self, data_dir: str = "local_data") -> None:
        os.makedirs(data_dir, exist_ok=True)
        self.db_path = os.path.join(data_dir, "pending.db")
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        with self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_ops (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    row_index INTEGER,
                    column TEXT,
                    value TEXT,
                    retries INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now')),
                    last_attempt TEXT
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS active_positions (
                    symbol TEXT PRIMARY KEY,
                    buy_order_id TEXT,
                    tp_order_id TEXT,
                    sl_order_id TEXT,
                    quantity REAL,
                    status TEXT,
                    last_update TEXT,
                    price REAL,
                    row_index INTEGER,
                    take_profit REAL,
                    stop_loss REAL,
                    archived INTEGER DEFAULT 0
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )
            # Helpful index for scanning
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_pending_type ON pending_ops(type, created_at)"
            )

    # ------------- Pending operations API -------------
    def add_cell_update(self, row_index: int, column: str, value: Any, update_type: str = "cell_update", op_id: str = None) -> None:
        payload_value = value if isinstance(value, str) else json.dumps(value) if isinstance(value, (dict, list)) else str(value)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO pending_ops(id, type, row_index, column, value) VALUES (?, ?, ?, ?, ?)",
                (op_id or self._gen_id(), update_type, row_index, column, payload_value),
            )

    def add_archive_operation(self, row_index: int, row_data: Dict[str, Any], columns_to_clear: List[str] = None, op_id: str = None) -> None:
        # Idempotency: do not enqueue duplicate archive for same row
        archive_value = json.dumps({"row_data": row_data, "columns_to_clear": columns_to_clear or []})
        with self._lock, self._conn:
            exists = self._conn.execute(
                "SELECT 1 FROM pending_ops WHERE type='archive' AND row_index=? LIMIT 1",
                (row_index,),
            ).fetchone()
            if exists:
                return
            self._conn.execute(
                "INSERT INTO pending_ops(id, type, row_index, column, value) VALUES (?, 'archive', ?, 'row_data', ?)",
                (op_id or self._gen_id(), row_index, archive_value),
            )

    def add_clear_operations(self, row_index: int, columns: List[str], op_id: str = None) -> None:
        payload = json.dumps({"columns": columns})
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO pending_ops(id, type, row_index, column, value) VALUES (?, 'clear_row', ?, 'columns', ?)",
                (op_id or self._gen_id(), row_index, payload),
            )

    def get_pending_count(self) -> Dict[str, int]:
        with self._lock:
            cur = self._conn.cursor()
            counts = {}
            for t in ("cell_update", "archive", "clear_row"):
                cur.execute("SELECT COUNT(*) AS c FROM pending_ops WHERE type=?", (t,))
                counts_key = {
                    "cell_update": "updates",
                    "archive": "archives",
                    "clear_row": "clears",
                }[t]
                counts[counts_key] = int(cur.fetchone()[0])
            return counts

    def get_batch_for_processing(self, max_batch_size: int = 20) -> Dict[str, List[Dict[str, Any]]]:
        with self._lock:
            cur = self._conn.cursor()
            batch = {"updates": [], "archives": [], "clears": []}

            cur.execute(
                "SELECT * FROM pending_ops WHERE type='cell_update' ORDER BY created_at LIMIT ?",
                (max_batch_size,),
            )
            for r in cur.fetchall():
                batch["updates"].append({
                    "id": r["id"],
                    "type": r["type"],
                    "row_index": r["row_index"],
                    "column": r["column"],
                    "value": r["value"],
                })

            cur.execute(
                "SELECT * FROM pending_ops WHERE type='archive' ORDER BY created_at LIMIT ?",
                (max_batch_size,),
            )
            for r in cur.fetchall():
                payload = json.loads(r["value"]) if r["value"] else {}
                batch["archives"].append({
                    "id": r["id"],
                    "type": r["type"],
                    "row_index": r["row_index"],
                    "row_data": payload.get("row_data", {}),
                    "columns_to_clear": payload.get("columns_to_clear", []),
                })

            cur.execute(
                "SELECT * FROM pending_ops WHERE type='clear_row' ORDER BY created_at LIMIT ?",
                (max_batch_size,),
            )
            for r in cur.fetchall():
                payload = json.loads(r["value"]) if r["value"] else {}
                batch["clears"].append({
                    "id": r["id"],
                    "type": r["type"],
                    "row_index": r["row_index"],
                    "columns": payload.get("columns", []),
                })

            return batch

    def mark_batch_completed(self, completed_ids: List[str]) -> None:
        if not completed_ids:
            return
        with self._lock, self._conn:
            self._conn.executemany(
                "DELETE FROM pending_ops WHERE id=?",
                [(op_id,) for op_id in completed_ids],
            )

    def mark_batch_failed(self, failed_ids: List[str], max_retries: int = 3) -> None:
        if not failed_ids:
            return
        now = datetime.utcnow().isoformat()
        with self._lock, self._conn:
            self._conn.executemany(
                "UPDATE pending_ops SET retries = COALESCE(retries,0) + 1, last_attempt = ? WHERE id=?",
                [(now, op_id) for op_id in failed_ids],
            )
            # Drop ops exceeding retries
            self._conn.execute(
                "DELETE FROM pending_ops WHERE retries >= ?",
                (max_retries,),
            )

    # ------------- Active positions API -------------
    def upsert_active_position(self, symbol: str, data: Dict[str, Any]) -> None:
        data = dict(data)
        data.setdefault("status", "")
        data.setdefault("archived", 0)
        data.setdefault("last_update", datetime.utcnow().isoformat())
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO active_positions(symbol, buy_order_id, tp_order_id, sl_order_id, quantity, status, last_update, price, row_index, take_profit, stop_loss, archived)
                VALUES(:symbol, :buy_order_id, :tp_order_id, :sl_order_id, :quantity, :status, :last_update, :price, :row_index, :take_profit, :stop_loss, :archived)
                ON CONFLICT(symbol) DO UPDATE SET
                    buy_order_id=excluded.buy_order_id,
                    tp_order_id=excluded.tp_order_id,
                    sl_order_id=excluded.sl_order_id,
                    quantity=excluded.quantity,
                    status=excluded.status,
                    last_update=excluded.last_update,
                    price=excluded.price,
                    row_index=excluded.row_index,
                    take_profit=excluded.take_profit,
                    stop_loss=excluded.stop_loss,
                    archived=excluded.archived
                """,
                {
                    "symbol": symbol,
                    "buy_order_id": data.get("order_id") or data.get("buy_order_id"),
                    "tp_order_id": data.get("tp_order_id"),
                    "sl_order_id": data.get("sl_order_id"),
                    "quantity": data.get("quantity"),
                    "status": data.get("status"),
                    "price": data.get("price"),
                    "row_index": data.get("row_index"),
                    "take_profit": data.get("take_profit"),
                    "stop_loss": data.get("stop_loss"),
                    "archived": int(bool(data.get("archived", 0))),
                },
            )

    def get_all_active_positions(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM active_positions").fetchall()
            out: Dict[str, Dict[str, Any]] = {}
            for r in rows:
                d = dict(r)
                d["archived"] = bool(d.get("archived", 0))
                out[d["symbol"]] = d
            return out

    def delete_active_position(self, symbol: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM active_positions WHERE symbol=?", (symbol,))

    # ------------- Meta API -------------
    def set_meta(self, key: str, value: Any) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )

    def get_meta(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            if not row:
                return default
            try:
                return json.loads(row[0])
            except Exception:
                return row[0]

    # ------------- Utils -------------
    def _gen_id(self) -> str:
        # Avoid importing uuid for deterministic small IDs
        return datetime.utcnow().strftime("%Y%m%d%H%M%S%f")

