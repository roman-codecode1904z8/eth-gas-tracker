from dataclasses import dataclass
import math
from typing import Dict, List, Optional, Tuple


@dataclass
class ActionMetric:
    selector: str
    name: str
    sample_count: int
    min_gas: int
    max_gas: int
    avg_gas: float
    p50_gas: int
    p95_gas: int
    total_eth_cost: float


@dataclass
class TimingSignal:
    action_selector: str
    is_congested: bool
    current_base_fee_gwei: float
    rolling_avg_base_fee_gwei: float
    recommended_priority_fee_gwei: float
    projected_cost_usd: float


class GasAnalyzer:
    """Calculates gas burn distribution and selector aggregates for contract actions."""

    def __init__(self, selector_map: Optional[Dict[str, str]] = None):
        self.selector_map = selector_map or {}

    def extract_selector(self, calldata: str) -> str:
        if not calldata or calldata == "0x":
            return "0x00000000"
        clean = calldata[2:] if calldata.startswith("0x") else calldata
        if len(clean) < 8:
            return "0x" + clean.ljust(8, "0")
        return "0x" + clean[:8].lower()

    def compute_percentiles(self, values: List[int], percentiles: List[int]) -> Dict[int, int]:
        if not values:
            return {p: 0 for p in percentiles}
        sorted_vals = sorted(values)
        k = len(sorted_vals)
        res = {}
        for p in percentiles:
            if p <= 0:
                res[p] = sorted_vals[0]
            elif p >= 100:
                res[p] = sorted_vals[-1]
            else:
                idx = math.ceil((p / 100.0) * k) - 1
                res[p] = sorted_vals[max(0, min(idx, k - 1))]
        return res

    def analyze_actions(self, tx_records: List[dict], eth_price_usd: float = 0.0) -> List[ActionMetric]:
        buckets: Dict[str, List[Tuple[int, int]]] = {}

        for tx in tx_records:
            data = tx.get("input_data") or tx.get("input") or "0x"
            sel = self.extract_selector(data)
            gas_used = int(tx.get("gas_used", 0))
            # sometimes node returns gasPrice instead of effectiveGasPrice on legacy
            gas_price = int(tx.get("effective_gas_price") or tx.get("gas_price", 0))

            # print(f"DEBUG: sel={sel} gas_used={gas_used}")
            if sel not in buckets:
                buckets[sel] = []
            buckets[sel].append((gas_used, gas_price))

        results = []
        for sel, entries in buckets.items():
            gas_values = [g for g, _ in entries]
            sample_count = len(gas_values)
            if sample_count == 0:
                continue

            pcts = self.compute_percentiles(gas_values, [50, 95])
            min_g = min(gas_values)
            max_g = max(gas_values)
            avg_g = sum(gas_values) / sample_count

            total_wei = sum(g * p for g, p in entries)
            total_eth = total_wei / 1e18

            name = self.selector_map.get(sel, "unknown")
            results.append(
                ActionMetric(
                    selector=sel,
                    name=name,
                    sample_count=sample_count,
                    min_gas=min_g,
                    max_gas=max_g,
                    avg_gas=avg_g,
                    p50_gas=pcts[50],
                    p95_gas=pcts[95],
                    total_eth_cost=total_eth,
                )
            )

        return sorted(results, key=lambda x: x.sample_count, reverse=True)

    # TODO: handle L2 calldata compression overhead (rollup data gas) for arbitrum/optimism
    def evaluate_timing(
        self,
        selector: str,
        current_base_fee_wei: int,
        recent_blocks_base_fee: List[int],
        recent_priority_fees: List[int],
        avg_gas_limit: int = 150000,
        eth_usd: float = 0.0,
    ) -> TimingSignal:
        if not recent_blocks_base_fee:
            rolling_avg = current_base_fee_wei
        else:
            rolling_avg = sum(recent_blocks_base_fee) / len(recent_blocks_base_fee)

        # 12.5% threshold matches standard EIP-1559 max block swing
        is_congested = current_base_fee_wei > (rolling_avg * 1.125)

        # pick 60th percentile for priority fee to beat queue without overpaying
        if recent_priority_fees:
            p_map = self.compute_percentiles(recent_priority_fees, [60])
            rec_tip = p_map[60]
        else:
            rec_tip = 1_500_000_000  # 1.5 gwei default

        total_fee_per_gas_wei = current_base_fee_wei + rec_tip
        total_cost_eth = (total_fee_per_gas_wei * avg_gas_limit) / 1e18
        cost_usd = total_cost_eth * eth_usd

        return TimingSignal(
            action_selector=selector,
            is_congested=is_congested,
            current_base_fee_gwei=current_base_fee_wei / 1e9,
            rolling_avg_base_fee_gwei=rolling_avg / 1e9,
            recommended_priority_fee_gwei=rec_tip / 1e9,
            projected_cost_usd=round(cost_usd, 4),
        )
