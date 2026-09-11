# eth-gas-tracker

I built this to stop burning money when submitting automated turns to on-chain games (Primordial, Dark Forest clones, autonomous worlds). It queries receipt logs, calculates actual gas burnt per function call, stores historical runs in a local SQLite file, and tells you what hours of the day have the cheapest base fee for scheduling bot txs.

## Install

```bash
git clone https://github.com/alexvance/eth-gas-tracker.git
cd eth-gas-tracker
pip install -e .
```

## Configuration

Set an RPC URL (supports Ethereum L1, Arbitrum, Base, Optimism, Redstone):

```bash
export ETH_RPC_URL="https://mainnet.base.org"
```

By default data is saved to `~/.eth_gas_tracker.db`. You can override this with `--db /path/to/file.db`.

## Commands

### 1. Log a transaction

Fetch receipt, unpack gas used, effective gas price, and method ID from tx input:

```bash
gas-tracker track 0x4f82a9... --label "harvest_crops"
```

### 2. View action breakdown

Prints a breakdown of min, avg, max gas units and USD costs across tracked labels:

```bash
gas-tracker report
```

### 3. Find cheap execution windows

Analyzes your stored historical blocks and suggests optimal UTC hours to trigger bot loops:

```bash
gas-tracker windows --action "harvest_crops" --threshold 25
```

### 4. Watch pending gas

Polls base fees every few seconds in terminal:

```bash
gas-tracker watch --interval 4
```

<!-- checked: 2026-09-11 -->
