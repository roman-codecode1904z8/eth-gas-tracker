import pytest
from eth_gas_tracker.rpc import (
    decode_hex_int,
    parse_rpc_response,
    parse_receipt_record,
    RPCError,
)


def test_decode_hex_int_valid():
    assert decode_hex_int("0x0") == 0
    assert decode_hex_int("0x10") == 16
    assert decode_hex_int("0x5208") == 21000
    assert decode_hex_int("0x05208") == 21000
    assert decode_hex_int(21000) == 21000


def test_decode_hex_int_none_or_empty():
    assert decode_hex_int(None) == 0
    assert decode_hex_int("") == 0
    assert decode_hex_int("0x") == 0


def test_parse_rpc_response_success():
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": "0x5208"
    }
    assert parse_rpc_response(payload) == "0x5208"


def test_parse_rpc_response_raises_on_error_payload():
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {
            "code": -32000,
            "message": "execution reverted"
        }
    }
    with pytest.raises(RPCError) as exc_info:
        parse_rpc_response(payload)
    assert "execution reverted" in str(exc_info.value)
    assert exc_info.value.code == -32000


def test_parse_receipt_record_structure():
    raw = {
        "transactionHash": "0xabc123",
        "blockNumber": "0x100",
        "gasUsed": "0x7530",  # 30000
        "effectiveGasPrice": "0x3b9aca00",  # 1 gwei
        "status": "0x1",
        "from": "0xSender",
        "to": "0xGameContract",
    }
    parsed = parse_receipt_record(raw)
    assert parsed["tx_hash"] == "0xabc123"
    assert parsed["block_number"] == 256
    assert parsed["gas_used"] == 30000
    assert parsed["effective_gas_price"] == 1000000000
    assert parsed["status"] == 1
    assert parsed["from_address"] == "0xsender"
