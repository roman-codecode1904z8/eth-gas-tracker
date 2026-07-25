import random
import time
from typing import Any, Optional
import httpx


class RPCError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"RPC error {code}: {message}")


class RPCClient:
    """JSON-RPC wrapper with exponential backoff for rate-limited endpoints."""

    def __init__(self, rpc_url: str, timeout: float = 15.0, max_retries: int = 5, batch_size: int = 50):
        self.rpc_url = rpc_url
        self.timeout = timeout
        self.max_retries = max_retries
        self.batch_chunk_size = batch_size
        self._req_id = 0
        # keep-alive session across calls
        self.client = httpx.Client(timeout=self.timeout)

    def close(self):
        self.client.close()

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def call(self, method: str, params: Optional[list] = None) -> Any:
        if params is None:
            params = []

        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": self._next_id(),
        }

        delay = 0.4
        for attempt in range(self.max_retries):
            try:
                resp = self.client.post(self.rpc_url, json=payload)
                if resp.status_code == 429 or resp.status_code == 503:
                    sleep_time = delay + random.uniform(0.1, 0.5)
                    time.sleep(sleep_time)
                    delay *= 2.0
                    continue

                resp.raise_for_status()
                data = resp.json()

                if "error" in data:
                    err = data["error"]
                    raise RPCError(err.get("code", -1), err.get("message", "Unknown error"), err.get("data"))

                return data.get("result")

            except (httpx.TransportError, httpx.TimeoutException) as exc:
                if attempt == self.max_retries - 1:
                    raise exc
                time.sleep(delay)
                delay *= 1.8

        raise RuntimeError(f"Failed RPC call {method} after {self.max_retries} retries")

    def batch_call(self, calls: list[tuple[str, list]]) -> list[Any]:
        if not calls:
            return []

        results = [None] * len(calls)
        # chunk batch to avoid payload limits on free nodes (ankr/drpc)
        for chunk_start in range(0, len(calls), self.batch_chunk_size):
            chunk = calls[chunk_start:chunk_start + self.batch_chunk_size]
            payload = []
            id_map = {}

            for idx, (method, params) in enumerate(chunk):
                req_id = self._next_id()
                id_map[req_id] = chunk_start + idx
                payload.append({
                    "jsonrpc": "2.0",
                    "method": method,
                    "params": params,
                    "id": req_id,
                })

            # print(f"DEBUG batch request len: {len(payload)}")
            delay = 0.5
            success = False
            for attempt in range(self.max_retries):
                try:
                    resp = self.client.post(self.rpc_url, json=payload)
                    if resp.status_code in (429, 503):
                        time.sleep(delay + random.uniform(0.1, 0.3))
                        delay *= 2.0
                        continue

                    resp.raise_for_status()
                    batch_resp = resp.json()

                    # some nodes return a single error object instead of a list on error
                    if isinstance(batch_resp, dict) and "error" in batch_resp:
                        raise RPCError(
                            batch_resp["error"].get("code", -1),
                            batch_resp["error"].get("message", "Batch failed"),
                        )

                    for item in batch_resp:
                        orig_idx = id_map.get(item.get("id"))
                        if orig_idx is not None:
                            if "error" in item:
                                results[orig_idx] = None
                            else:
                                results[orig_idx] = item.get("result")

                    success = True
                    break
                except (httpx.TransportError, httpx.TimeoutException):
                    if attempt == self.max_retries - 1:
                        raise
                    time.sleep(delay)
                    delay *= 1.8

            if not success:
                raise RuntimeError(f"Batch RPC call failed after {self.max_retries} attempts")

        return results

    def get_block_by_number(self, block_num: int | str, full_txs: bool = False) -> Optional[dict]:
        tag = hex(block_num) if isinstance(block_num, int) else block_num
        return self.call("eth_getBlockByNumber", [tag, full_txs])

    def get_latest_block_number(self) -> int:
        res = self.call("eth_blockNumber")
        return int(res, 16)

    def get_receipt(self, tx_hash: str) -> Optional[dict]:
        return self.call("eth_getTransactionReceipt", [tx_hash])

    def get_receipts_batch(self, tx_hashes: list[str]) -> list[Optional[dict]]:
        calls = [("eth_getTransactionReceipt", [tx_hash]) for tx_hash in tx_hashes]
        return self.batch_call(calls)

    def get_base_fee(self, block_tag: str = "latest") -> int:
        block = self.call("eth_getBlockByNumber", [block_tag, False])
        if not block or "baseFeePerGas" not in block or block["baseFeePerGas"] is None:
            # pre-london or zero-baseFee L2
            return 0
        return int(block["baseFeePerGas"], 16)

    def get_fee_history(self, block_count: int, newest_block: str = "latest", reward_percentiles: Optional[list[float]] = None) -> dict:
        pct = reward_percentiles if reward_percentiles is not None else [25.0, 50.0, 75.0]
        raw = self.call("eth_feeHistory", [hex(block_count), newest_block, pct])
        if not raw:
            return {"oldest_block": 0, "base_fees": [], "gas_used_ratios": [], "rewards": []}

        # hex conversions
        oldest = int(raw.get("oldestBlock", "0x0"), 16)
        base_fees = [int(x, 16) for x in raw.get("baseFeePerGas", []) if x is not None]
        gas_ratios = raw.get("gasUsedRatio", [])
        
        # rewards is a list of lists of hex strings
        rewards = []
        for block_rewards in raw.get("reward", []):
            rewards.append([int(r, 16) for r in block_rewards])

        # TODO: handle L1 calldata fee on Arbitrum/OP (needs l1Fee or rollup fee parse)
        return {
            "oldest_block": oldest,
            "base_fees": base_fees,
            "gas_used_ratios": gas_ratios,
            "rewards": rewards,
        }
