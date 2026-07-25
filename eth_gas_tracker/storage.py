import sqlite3
from pathlib import Path
from typing import Optional, List, Dict, Any

DEFAULT_DB_PATH = Path.home() / ".eth_gas_tracker" / "data.db"


class GasStorage:
    """Local SQLite store for tracked contracts, gas receipts, and timing data."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def _init_db(self):
        conn = self._get_conn()
        with conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS contracts (
                    address TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    added_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS receipts (
                    tx_hash TEXT PRIMARY KEY,
                    contract_address TEXT NOT NULL,
                    action_name TEXT NOT NULL,
                    block_number INTEGER NOT NULL,
                    gas_used INTEGER NOT NULL,
                    effective_gas_price INTEGER NOT NULL,
                    l1_fee INTEGER DEFAULT 0,
                    timestamp INTEGER NOT NULL,
                    status INTEGER NOT NULL,
                    FOREIGN KEY (contract_address) REFERENCES contracts(address)
                );

                CREATE TABLE IF NOT EXISTS gas_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chain_id INTEGER NOT NULL,
                    block_number INTEGER NOT NULL,
                    base_fee INTEGER NOT NULL,
                    priority_fee_fast INTEGER NOT NULL,
                    priority_fee_safe INTEGER NOT NULL,
                    timestamp INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_receipts_contract ON receipts(contract_address, action_name);
                CREATE INDEX IF NOT EXISTS idx_receipts_ts ON receipts(timestamp);
                CREATE INDEX IF NOT EXISTS idx_snapshots_chain_ts ON gas_snapshots(chain_id, timestamp);
                """
            )

    def register_contract(self, address: str, name: str, chain_id: int, timestamp: int):
        conn = self._get_conn()
        addr = address.lower()
        with conn:
            conn.execute(
                """
                INSERT INTO contracts (address, name, chain_id, added_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(address) DO UPDATE SET name = excluded.name
                """,
                (addr, name, chain_id, timestamp),
            )

    def delete_contract(self, address: str):
        conn = self._get_conn()
        addr = address.lower()
        with conn:
            conn.execute("DELETE FROM receipts WHERE contract_address = ?", (addr,))
            conn.execute("DELETE FROM contracts WHERE address = ?", (addr,))

    # Legacy alias from earlier cli branch
    def save_tx(self, receipt_data: Dict[str, Any]):
        self.record_receipt(receipt_data)

    def record_receipt(self, receipt_data: Dict[str, Any]):
        conn = self._get_conn()
        # print(f"DEBUG: saving tx {receipt_data.get('tx_hash')}")
        with conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO receipts (
                    tx_hash, contract_address, action_name, block_number,
                    gas_used, effective_gas_price, l1_fee, timestamp, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_data["tx_hash"].lower(),
                    receipt_data["contract_address"].lower(),
                    receipt_data.get("action_name", "unknown"),
                    receipt_data["block_number"],
                    receipt_data["gas_used"],
                    receipt_data["effective_gas_price"],
                    receipt_data.get("l1_fee", 0),
                    receipt_data["timestamp"],
                    receipt_data.get("status", 1),
                ),
            )

    def record_gas_snapshot(self, chain_id: int, block_number: int, base_fee: int,
                            priority_fast: int, priority_safe: int, timestamp: int):
        conn = self._get_conn()
        with conn:
            conn.execute(
                """
                INSERT INTO gas_snapshots (chain_id, block_number, base_fee, priority_fee_fast, priority_fee_safe, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (chain_id, block_number, base_fee, priority_fast, priority_safe, timestamp),
            )

    def get_contract(self, address: str) -> Optional[Dict[str, Any]]:
        conn = self._get_conn()
        cur = conn.execute(
            "SELECT address, name, chain_id, added_at FROM contracts WHERE address = ?",
            (address.lower(),),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def list_contracts(self) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        cur = conn.execute("SELECT address, name, chain_id, added_at FROM contracts ORDER BY added_at DESC")
        return [dict(r) for r in cur.fetchall()]

    def get_recent_receipts(self, contract_address: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        if contract_address:
            cur = conn.execute(
                """
                SELECT r.*, c.name as contract_name
                FROM receipts r
                JOIN contracts c ON r.contract_address = c.address
                WHERE r.contract_address = ?
                ORDER BY r.timestamp DESC LIMIT ?
                """,
                (contract_address.lower(), limit),
            )
        else:
            cur = conn.execute(
                """
                SELECT r.*, c.name as contract_name
                FROM receipts r
                JOIN contracts c ON r.contract_address = c.address
                ORDER BY r.timestamp DESC LIMIT ?
                """,
                (limit,),
            )
        return [dict(r) for r in cur.fetchall()]

    # FIXME: add a retention config instead of hardcoded 14 days prune
    def prune_snapshots(self, chain_id: int, keep_seconds: int = 14 * 86400) -> int:
        conn = self._get_conn()
        import time
        cutoff = int(time.time()) - keep_seconds
        with conn:
            cur = conn.execute(
                "DELETE FROM gas_snapshots WHERE chain_id = ? AND timestamp < ?",
                (chain_id, cutoff),
            )
            return cur.rowcount

    def get_snapshots_since(self, chain_id: int, since_ts: int) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        cur = conn.execute(
            """
            SELECT block_number, base_fee, priority_fee_fast, priority_fee_safe, timestamp
            FROM gas_snapshots
            WHERE chain_id = ? AND timestamp >= ?
            ORDER BY timestamp ASC
            """,
            (chain_id, since_ts),
        )
        return [dict(r) for r in cur.fetchall()]

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None
