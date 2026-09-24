"""Sequential execution, durable transaction intent, explicit receipt accounting."""
from dataclasses import asdict
import time

from eth_account import Account
from web3 import Web3
from web3.exceptions import ContractLogicError, TransactionNotFound

from .chain import (Chain, Pool, TOKEN_ABI, V2_ABI, V3_ABI, V2_ROUTER, V3_ROUTER,
                    V2_FACTORY, V3_FACTORY, WBNB, USDT, ETH, address, route_path)
from .routes import conversion_specs
from .storage import Store
from .strategy import D, minimum_out


class UncertainTransaction(RuntimeError):
    pass


class LiveTrader:
    def __init__(self, chain: Chain, key: str, store: Store, gas_gwei: D, log, max_fee=D("0.005")):
        if not gas_gwei.is_finite() or not 0 < gas_gwei <= 1000:
            raise ValueError("GAS GWEI должен быть от 0 до 1000")
        self.chain, self.store, self.log = chain, store, log
        self.account = Account.from_key(key)
        self.owner = self.account.address
        self.gas_price = int(gas_gwei * 10**9)
        self.max_fee = int(max_fee * 10**18)
        self.operation = None
        self.chain.check()

    def begin(self, description):
        if self.store.data.get("operation"):
            raise UncertainTransaction("Есть незавершённая LIVE-операция. Нужна сверка транзакций и балансов")
        self.operation = {"wallet": self.owner, "description": description,
                          "started": int(time.time()), "transactions": []}
        self.store.data["operation"] = self.operation
        self.store.save()

    def finish(self):
        if self.operation is None:
            raise RuntimeError("Нет активной операции")
        previous = self.store.data.copy()
        self.store.data["history"] = (self.store.data.get("history", []) + [self.operation])[-100:]
        self.store.data.pop("operation", None)
        try:
            self.store.save()
        except Exception:
            # Remain locked in this process even if fsync/replace outcome is unknown.
            self.store.data = previous
            raise
        self.operation = None

    def send(self, function, label, value=0):
        if self.operation is None:
            raise RuntimeError("Отправка вне записанной операции запрещена")
        self.chain.check()
        w3 = self.chain.w3
        nonce = w3.eth.get_transaction_count(self.owner, "pending")
        latest_nonce = w3.eth.get_transaction_count(self.owner, "latest")
        if nonce != latest_nonce:
            raise UncertainTransaction("У кошелька уже есть pending-транзакция. Дождитесь её подтверждения")
        tx_base = {"from": self.owner, "value": value, "gasPrice": self.gas_price,
                   "nonce": nonce, "chainId": 56}
        gas = (function.estimate_gas(tx_base) * 120 + 99) // 100
        if gas * self.gas_price > self.max_fee:
            raise ValueError("Расчётная комиссия превышает лимит 0.005 BNB на транзакцию")
        if w3.eth.get_balance(self.owner) < value + gas * self.gas_price:
            raise ValueError("Недостаточно BNB для суммы и газа")
        tx = function.build_transaction({**tx_base, "gas": gas})
        signed = self.account.sign_transaction(tx)
        local_hash = Web3.to_hex(Web3.keccak(signed.raw_transaction))
        record = {"hash": local_hash, "label": label, "nonce": nonce, "status": "pending"}
        self.operation["transactions"].append(record)
        self.store.save()  # Hash is durable BEFORE broadcast, even if the RPC reply is lost.
        self.log(f"{label}: {local_hash}")
        try:
            remote_hash = Web3.to_hex(w3.eth.send_raw_transaction(signed.raw_transaction))
            if remote_hash != local_hash:
                raise UncertainTransaction("RPC вернул другой hash")
            receipt = w3.eth.wait_for_transaction_receipt(local_hash, timeout=120, poll_latency=0.2)
        except Exception:
            raise UncertainTransaction(f"Статус неизвестен: {local_hash}. Повторная отправка заблокирована") from None
        record["status"] = "confirmed" if receipt["status"] == 1 else "reverted"
        record["block"] = receipt["blockNumber"]
        self.store.save()
        if receipt["status"] != 1:
            raise RuntimeError(f"Транзакция отклонена в блокчейне: {local_hash}")
        self.log(f"Подтверждено: {label}, блок {receipt['blockNumber']}")
        return receipt

    def approve(self, token, spender, amount):
        allowance = self.chain.call(token, TOKEN_ABI, "allowance", self.owner, spender)
        if allowance >= amount:
            return
        contract = self.chain.contract(token, TOKEN_ABI)
        if allowance:
            self.send(contract.functions.approve(spender, 0), "APPROVE RESET")
        self.send(contract.functions.approve(spender, amount), "APPROVE EXACT AMOUNT")

    def verify_router(self, pool):
        if pool.router == "V2":
            router, abi, factory, wrapped = V2_ROUTER, V2_ABI, V2_FACTORY, "WETH"
        else:
            router, abi, factory, wrapped = V3_ROUTER, V3_ABI, V3_FACTORY, "WETH9"
        if not self.chain.w3.eth.get_code(address(router)):
            raise ValueError("Router отсутствует в сети")
        if self.chain.call(router, abi, "factory").lower() != factory.lower():
            raise ValueError("Factory router не совпадает")
        if self.chain.call(router, abi, wrapped).lower() != WBNB.lower():
            raise ValueError("WBNB router не совпадает")
        return address(router), abi

    def swap(self, pool: Pool, amount: int, buy: bool, tolerance: D, *, signal_minimum=None):
        if signal_minimum is not None and (not buy or not isinstance(signal_minimum, int)
                                          or not 0 < signal_minimum < 2**256):
            raise ValueError("Некорректный BUY minOut снимка")
        pool = self.chain.verify_pool(pool.address, pool.token)
        router, abi = self.verify_router(pool)
        src, dest = (pool.quote, pool.token) if buy else (pool.token, pool.quote)
        if self.chain.balance(src, self.owner) < amount:
            raise ValueError("Недостаточно базового актива / токенов; используйте Converter")
        initial_min = minimum_out(self.chain.quote(pool, amount, buy), tolerance)
        if signal_minimum is not None:
            initial_min = max(initial_min, signal_minimum)
        self.approve(src, router, amount)
        # Approval may take time; preserve the original bound and also quote again.
        min_out = max(initial_min, minimum_out(self.chain.quote(pool, amount, buy), tolerance))
        before = self.chain.balance(dest, self.owner)
        deadline = int(time.time()) + 30
        contract = self.chain.contract(router, abi)
        if pool.router == "V2":
            function = contract.functions.swapExactTokensForTokensSupportingFeeOnTransferTokens(
                amount, min_out, [src, dest], self.owner, deadline)
        else:
            function = contract.functions.exactInputSingle(
                (src, dest, pool.fee, self.owner, deadline, amount, min_out, 0))
        self.send(function, "BUY" if buy else "SELL")
        received = self.chain.balance(dest, self.owner) - before
        if received < min_out:
            raise UncertainTransaction("Сделка подтверждена, но изменение баланса ниже minOut. Нужна сверка")
        return received

    def wrap(self, amount):
        self.send(self.chain.contract(WBNB, TOKEN_ABI).functions.deposit(), "BNB → WBNB", value=amount)

    def unwrap(self, amount):
        if self.chain.balance(WBNB, self.owner) < amount:
            raise ValueError("Недостаточно WBNB")
        self.send(self.chain.contract(WBNB, TOKEN_ABI).functions.withdraw(amount), "WBNB → BNB")

    def conversion_route(self, src, dest, amount):
        """Quote original candidate order; retain safe alternatives per tier."""
        src, dest = address(src), address(dest)
        if src == dest or not 0 < amount < 2**256:
            raise ValueError("Некорректный маршрут / сумма Converter")
        routes = []
        cache = {}
        for kind, nodes, fees in conversion_specs(src, dest):
            path = []
            for index, (left, right) in enumerate(zip(nodes, nodes[1:])):
                pair = (left, right)
                if pair not in cache:
                    cache[pair] = self.chain.find_pools(right, left)
                match = next((p for p in cache[pair] if p.router == kind
                              and (kind == "V2" or p.fee == fees[index])), None)
                if match is None:
                    break
                path.append(match)
            else:
                try:
                    output = self.chain.quote_route(path, amount)
                    if output <= 0:
                        continue
                    returned = self.chain.quote_route(path, output, reverse=True)
                except ContractLogicError:
                    continue
                # Keep the exact 15% bound; the original floors loss to integer bps.
                if returned * 10000 >= amount * 8500:
                    routes.append((output, path))
        if not routes:
            raise ValueError("Нет маршрута Converter с round-trip loss ≤ 15% для этой суммы")
        return max(routes, key=lambda item: item[0])[1]

    def convert(self, quote, amount, buy, slippage):
        if not 0 < amount < 2**256 or not slippage.is_finite() or not 0 <= slippage <= 20:
            raise ValueError("Некорректная сумма / Slippage Converter")
        quote = address(quote)
        if quote == address(WBNB):
            self.wrap(amount) if buy else self.unwrap(amount)
            return amount
        src, dest = (address(WBNB), quote) if buy else (quote, address(WBNB))
        route = self.conversion_route(src, dest, amount)
        route = [self.chain.verify_pool(p.address, p.token) for p in route]
        tokens, packed = route_path(route)
        if (tokens[0], tokens[-1]) != (src, dest):
            raise ValueError("Маршрут не соответствует направлению Converter")
        router, abi = self.verify_router(route[0])
        minimum = minimum_out(self.chain.quote_route(route, amount), slippage)
        if not buy:
            if self.chain.balance(src, self.owner) < amount:
                raise ValueError("Недостаточно базового актива")
            self.approve(src, router, amount)
        # Fresh preflight preserves the earlier minimum and round-trip constraint.
        fresh = self.chain.quote_route(route, amount)
        if self.chain.quote_route(route, fresh, reverse=True) * 10000 < amount * 8500:
            raise ValueError("Round-trip loss изменился: Converter остановлен")
        minimum = max(minimum, minimum_out(fresh, slippage))
        native_sell = not buy and route[0].router == "V2"
        before = (self.chain.w3.eth.get_balance(self.owner) if native_sell
                  else self.chain.balance(dest, self.owner))
        deadline = int(time.time()) + 60
        functions = self.chain.contract(router, abi).functions
        if route[0].router == "V2":
            function = (functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
                minimum, tokens, self.owner, deadline) if buy else
                functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
                    amount, minimum, tokens, self.owner, deadline))
        else:
            function = functions.exactInput((packed, self.owner, deadline, amount, minimum))
        receipt = self.send(function, "CONVERTER BUY" if buy else "CONVERTER SELL",
                            value=amount if buy else 0)
        if native_sell:
            received = self.chain.w3.eth.get_balance(self.owner) - before
            received += receipt["gasUsed"] * receipt["effectiveGasPrice"]
        else:
            received = self.chain.balance(dest, self.owner) - before
        if received < minimum:
            raise UncertainTransaction("Converter подтверждён, но выход ниже minOut; нужна сверка")
        if not buy and not native_sell:
            # Like the original V3 SELL, unwrap only the newly received WBNB.
            self.unwrap(received)
        return received

    def reconcile(self):
        operation = self.store.data.get("operation")
        if not operation:
            return "Незавершённых операций нет"
        if operation["wallet"].lower() != self.owner.lower():
            raise ValueError("Для сверки нужен тот же кошелёк, который начал операцию")
        for record in operation["transactions"]:
            try:
                receipt = self.chain.w3.eth.get_transaction_receipt(record["hash"])
            except TransactionNotFound:
                raise UncertainTransaction(f"Не найден receipt {record['hash']}; блокировка сохранена") from None
            record["status"] = "confirmed" if receipt["status"] == 1 else "reverted"
        # Do not clear the latch automatically: balances/position also need review.
        self.store.save()
        return "Все записанные транзакции завершены. Проверьте балансы; затем снимите блокировку вручную"


class PaperTrader:
    def __init__(self, slippage: D):
        self.slippage = slippage
        self.position = D(0)
        self.cost = D(0)
        self.realized = D(0)

    def buy(self, amount: D, price: D):
        if self.position:
            raise ValueError("Бумажная позиция уже открыта")
        self.cost = amount
        self.position = amount / (price * (1 + self.slippage / 100))
        return amount / self.position

    def sell(self, price: D):
        proceeds = self.position * price * (1 - self.slippage / 100)
        pnl = proceeds - self.cost
        self.realized += pnl
        self.position = self.cost = D(0)
        return pnl
