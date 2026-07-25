import sqlite3
import pytest
from eth_gas_tracker.storage import TrackerStorage


@pytest.fixture
def storage(tmp_path):
    db_path = tmp_path / "test_gas.db"
    store = TrackerStorage(str(db_path))
    store.init_schema()
    return store


def test_schema_initialization(storage):
    with storage._connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tx_receipts'")
        assert cur.fetchone() is not None


def test_insert_and_query_receipts(storage):
    records = [
        {
            "tx_hash": "0x1",
            "block_number": 1000,
            "contract_address": "0xgame",
            "from_address": "0xbot1",
            "gas_used": 45000,
            "effective_gas_price": 20000000000,
            "status": 1,
            "input_data": "0xa9059cbb00000000",
            "timestamp": 1700000000,
        },
        {
            "tx_hash": "0x2",
            "block_number": 1001,
            "contract_address": "0xgame",
            "from_address": "0xbot2",
            "gas_used": 52000,
            "effective_gas_price": 25000000000,
            "status": 1,
            "input_data": "0x2e1a7d4d00000000",
            "timestamp": 1700000012,
        },
    ]
    storage.save_receipts(records)

    # query by contract
    fetched = storage.get_transactions(contract_address="0xgame")
    assert len(fetched) == 2

    # filter by block range
    filtered = storage.get_transactions(contract_address="0xgame", min_block=1001)
    assert len(filtered) == 1
    assert filtered[0]["tx_hash"] == "0x2"


def test_deduplication_on_conflict(storage):
    record = {
        "tx_hash": "0xdup",
        "block_number": 1050,
        "contract_address": "0xgame",
        "from_address": "0xbot1",
        "gas_used": 40000,
        "effective_gas_price": 20000000000,
        "status": 1,
        "input_data": "0x",
        "timestamp": 1700000500,
    }
    storage.save_receipts([record])
    # duplicate insert shouldn't crash
    storage.save_receipts([record])
    
    all_txs = storage.get_transactions(contract_address="0xgame")
    assert len(all_txs) == 1
