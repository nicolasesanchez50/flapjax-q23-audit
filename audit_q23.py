#!/usr/bin/env python3
"""FlapJax Receipt Relay Q23 — independent audit of the 10-12 buyback receipt.

Window: treasury 0x7C34E9e21eE28A49Ff0b84B61774119E6633359f, from the 10-11 buy
(tx 0x82d01172...d2801eb, block 127067713) to the 10-12 buy
(tx 0x9afdc96e...d5cbf1, block 127261246).

Zero dependencies, plain JSON-RPC against any public BSC node.
Methods: eth_getLogs (FLAPJAX transfers FROM the treasury), eth_getTransactionByHash,
eth_getTransactionReceipt, eth_getBalance, eth_call (balanceOf).

Completeness proof: the two boundary buys carry treasury nonces 72 and 87, so the
window can contain at most nonces 73..86 (14 txs). We enumerate 14 and their nonces
are contiguous, so no treasury-sent tx is missing. The BNB balance equation then
closes to the wei, which independently confirms no unaccounted value moved.
"""
import json, sys, time, urllib.request

RPC = "https://rpc.48.club"          # public node, 5000-block eth_getLogs cap
RPC_ARCHIVE = "https://bsc-mainnet.public.blastapi.io"  # for historical balance reads
TREASURY = "0x7C34E9e21eE28A49Ff0b84B61774119E6633359f"
TOKEN    = "0x90c8889f428F9Ebb77BB8f15CAD3a50a9aC680df"
BUY11_TX, BUY11_BLOCK = "0x82d01172ae26f1c86287643e123af1675ca5c18b984b7740c1a0c1647d2801eb", 127067713
BUY12_TX, BUY12_BLOCK = "0x9afdc96e3ca876b5b617a26ba5105012ed8c33e805c77b1616bf48c198d5cbf1", 127261246
GAS_GRANT_TX = "0xc2193a974bf92cb1f908dbfa537c4a21b347a6498f2a46c61c8b48e88e5e9cdd"  # nonce 76, 0.002 BNB to escrow wallet
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
UA = {"Content-Type": "application/json",
      "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}

def rpc(url, method, params, tries=6):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    for a in range(tries):
        try:
            req = urllib.request.Request(url, data=body, headers=UA)
            with urllib.request.urlopen(req, timeout=45) as r:
                j = json.load(r)
            if "result" in j and j["result"] is not None:
                return j["result"]
            raise RuntimeError(str(j.get("error"))[:150])
        except Exception:
            if a == tries - 1:
                raise
            time.sleep(3 * (a + 1))

def main():
    tr_topic = "0x" + "0" * 24 + TREASURY[2:].lower()

    # 1. boundary buys -> nonces that bound the window
    n11 = int(rpc(RPC, "eth_getTransactionByHash", [BUY11_TX])["nonce"], 16)
    n12 = int(rpc(RPC, "eth_getTransactionByHash", [BUY12_TX])["nonce"], 16)
    print(f"10-11 buy: block {BUY11_BLOCK}, treasury nonce {n11}")
    print(f"10-12 buy: block {BUY12_BLOCK}, treasury nonce {n12}")

    # 2. FLAPJAX transfers FROM the treasury inside the window
    logs, lo = [], BUY11_BLOCK
    while lo <= BUY12_BLOCK:
        hi = min(lo + 4999, BUY12_BLOCK)
        logs += rpc(RPC, "eth_getLogs", [{"address": TOKEN, "topics": [TRANSFER, tr_topic],
                                          "fromBlock": hex(lo), "toBlock": hex(hi)}])
        lo = hi + 1
    fj_tx = {l["transactionHash"]: int(l["data"], 16) for l in logs}

    # 3. every tx: the FJ payouts plus the BNB gas grant
    rows = []
    for h in list(fj_tx) + [GAS_GRANT_TX]:
        tx = rpc(RPC, "eth_getTransactionByHash", [h])
        rc = rpc(RPC, "eth_getTransactionReceipt", [h])
        rows.append({
            "tx": h, "block": int(tx["blockNumber"], 16), "nonce": int(tx["nonce"], 16),
            "to": tx["to"], "value_wei": int(tx["value"], 16),
            "fee_wei": int(rc["gasUsed"], 16) * int(tx["gasPrice"], 16),
            "gas_used": int(rc["gasUsed"], 16), "gas_price": int(tx["gasPrice"], 16),
            "status": rc["status"], "fj_moved": fj_tx.get(h),
            "fj_recipient": ("0x" + [l for l in logs if l["transactionHash"] == h][0]["topics"][2][-40:])
                              if h in fj_tx else None})
    rows.sort(key=lambda r: (r["block"], r["nonce"]))

    nonces = [r["nonce"] for r in rows]
    contiguous = nonces == list(range(n11 + 1, n12))
    print(f"\n{len(rows)} txs, nonces {nonces[0]}..{nonces[-1]}, contiguous with the boundaries: {contiguous}")

    # 4. boundary balances
    bal_sel = "0x70a08231" + "0" * 24 + TREASURY[2:].lower()
    b0 = int(rpc(RPC_ARCHIVE, "eth_getBalance", [TREASURY, hex(BUY11_BLOCK)]), 16)
    b1 = int(rpc(RPC_ARCHIVE, "eth_getBalance", [TREASURY, hex(BUY12_BLOCK - 1)]), 16)
    f0 = int(rpc(RPC_ARCHIVE, "eth_call", [{"to": TOKEN, "data": bal_sel}, hex(BUY11_BLOCK)]), 16)
    f1 = int(rpc(RPC_ARCHIVE, "eth_call", [{"to": TOKEN, "data": bal_sel}, hex(BUY12_BLOCK - 1)]), 16)

    vals = sum(r["value_wei"] for r in rows)
    fees = sum(r["fee_wei"] for r in rows)
    fj_out = sum(r["fj_moved"] for r in rows if r["fj_moved"])
    print(f"\nbalance just after 10-11 buy (blk {BUY11_BLOCK}): {b0} wei")
    print(f"  minus values+fees ({vals}+{fees}):         {b0 - vals - fees} wei")
    print(f"balance just before 10-12 buy (blk {BUY12_BLOCK-1}): {b1} wei")
    print(f"  gap: {b1 - (b0 - vals - fees)} wei  (0 = closes exactly, no BNB received)")
    print(f"FJ: {f0} -> {f1}, sent {fj_out}, gap {f1 - (f0 - fj_out)}")

    json.dump({"rows": rows, "bnb_after_buy11": b0, "bnb_before_buy12": b1,
               "fj_after_buy11": f0, "fj_before_buy12": f1,
               "sum_values": vals, "sum_fees": fees, "sum_fj_out": fj_out,
               "contiguous_nonces": contiguous}, open("q23_audit_raw.json", "w"), indent=1)
    print("\nraw output -> q23_audit_raw.json")

if __name__ == "__main__":
    main()
