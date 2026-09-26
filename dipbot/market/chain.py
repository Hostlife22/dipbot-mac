from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping, cast

from eth_typing import HexStr
from hexbytes import HexBytes
from web3.contract.contract import Contract
from web3.types import BlockData, RPCEndpoint, RPCResponse

if TYPE_CHECKING:
    from dipbot.domain.entry_guard import EntryQuote
    from dipbot.market.discovery import Resolution
import json
import time
from dataclasses import dataclass
from decimal import localcontext
from importlib.resources import files
from urllib.parse import urlsplit

from eth_typing import ChecksumAddress
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

from dipbot.domain.assets import (
    FEES,
    V2_FACTORY,
    V2_ROUTER,
    V3_FACTORY,
    V3_QUOTER,
    ZERO,
)
from dipbot.domain.strategy import D
from dipbot.market.rpc import BscHTTPProvider
from dipbot.observability.telemetry import TIMINGS, timed


def fn(
    name: str, inputs: tuple[str, ...] = (), outputs: tuple[str, ...] = (), mutability: str = "view"
) -> dict[str, Any]:
    return {
        "type": "function",
        "name": name,
        "stateMutability": mutability,
        "inputs": [{"name": f"p{i}", "type": t} for i, t in enumerate(inputs)],
        "outputs": [{"name": f"r{i}", "type": t} for i, t in enumerate(outputs)],
    }


TOKEN_ABI = [
    fn("decimals", outputs=("uint8",)),
    fn("symbol", outputs=("string",)),
    fn("balanceOf", ("address",), ("uint256",)),
    fn("allowance", ("address", "address"), ("uint256",)),
    fn("approve", ("address", "uint256"), ("bool",), "nonpayable"),
    fn("deposit", mutability="payable"),
    fn("withdraw", ("uint256",), mutability="nonpayable"),
]
POOL_ABI = [
    fn("factory", outputs=("address",)),
    fn("token0", outputs=("address",)),
    fn("token1", outputs=("address",)),
    fn("fee", outputs=("uint24",)),
    fn("liquidity", outputs=("uint128",)),
    fn("getReserves", outputs=("uint112", "uint112", "uint32")),
    fn("slot0", outputs=("uint160", "int24", "uint16", "uint16", "uint16", "uint32", "bool")),
]
FACTORY_ABI = [
    fn("getPair", ("address", "address"), ("address",)),
    fn("getPool", ("address", "address", "uint24"), ("address",)),
]
V2_ABI = [
    fn("factory", outputs=("address",)),
    fn("WETH", outputs=("address",)),
    fn("getAmountsOut", ("uint256", "address[]"), ("uint256[]",)),
    fn(
        "swapExactETHForTokensSupportingFeeOnTransferTokens",
        ("uint256", "address[]", "address", "uint256"),
        mutability="payable",
    ),
    fn(
        "swapExactTokensForETHSupportingFeeOnTransferTokens",
        ("uint256", "uint256", "address[]", "address", "uint256"),
        mutability="nonpayable",
    ),
    fn(
        "swapExactTokensForTokensSupportingFeeOnTransferTokens",
        ("uint256", "uint256", "address[]", "address", "uint256"),
        mutability="nonpayable",
    ),
]


def tuple_fn(name: str, components: list[tuple[str, str]], outputs: tuple[str, ...]) -> dict[str, Any]:
    result = fn(
        name,
        outputs=outputs,
        mutability="payable" if name in ("exactInputSingle", "exactInput") else "nonpayable",
    )
    result["inputs"] = [
        {"name": "params", "type": "tuple", "components": [{"name": n, "type": t} for n, t in components]}
    ]
    return result


V3_ABI = [
    fn("factory", outputs=("address",)),
    fn("WETH9", outputs=("address",)),
    tuple_fn(
        "exactInput",
        [
            ("path", "bytes"),
            ("recipient", "address"),
            ("deadline", "uint256"),
            ("amountIn", "uint256"),
            ("amountOutMinimum", "uint256"),
        ],
        ("uint256",),
    ),
    tuple_fn(
        "exactInputSingle",
        [
            ("tokenIn", "address"),
            ("tokenOut", "address"),
            ("fee", "uint24"),
            ("recipient", "address"),
            ("deadline", "uint256"),
            ("amountIn", "uint256"),
            ("amountOutMinimum", "uint256"),
            ("sqrtPriceLimitX96", "uint160"),
        ],
        ("uint256",),
    ),
]
QUOTER_ABI = [
    fn(
        "quoteExactInput", ("bytes", "uint256"), ("uint256", "uint160[]", "uint32[]", "uint256"), "nonpayable"
    ),
    tuple_fn(
        "quoteExactInputSingle",
        [
            ("tokenIn", "address"),
            ("tokenOut", "address"),
            ("amountIn", "uint256"),
            ("fee", "uint24"),
            ("sqrtPriceLimitX96", "uint160"),
        ],
        ("uint256", "uint160", "uint32", "uint256"),
    ),
]


def address(value: str) -> ChecksumAddress:
    if not Web3.is_address(value) or value.lower() == ZERO:
        raise ValueError("Нужен ненулевой адрес 0x… (40 hex символов)")
    return Web3.to_checksum_address(value)


def profiles() -> dict[str, str]:
    return {
        p["symbol"]: address(p["address"])
        for p in json.loads(files("dipbot").joinpath("profiles.json").read_text())
    }


@dataclass(frozen=True)
class Pool:
    address: str
    router: str
    token: str
    quote: str
    token_decimals: int
    quote_decimals: int
    token_is_0: bool
    fee: int = 0

    @property
    def label(self) -> str:
        names = {v.lower(): k for k, v in profiles().items()}
        return f"{self.router} / {names.get(self.quote.lower(), self.quote[:10])} / {self.fee or 2500} · {self.address}"


def route_path(route: list[Pool], reverse: bool = False) -> tuple[list[ChecksumAddress], bytes]:
    if not route or len(route) > 2:
        raise ValueError("Converter требует один или два пула")
    kind = route[0].router
    if kind not in ("V2", "V3") or any(p.router != kind for p in route):
        raise ValueError("Смешанный маршрут не может быть исполнен атомарно")
    tokens = [address(route[0].quote)]
    fees = []
    for pool in route:
        if address(pool.quote) != tokens[-1]:
            raise ValueError("Разрыв маршрута Converter")
        tokens.append(address(pool.token))
        if kind == "V3" and pool.fee not in FEES:
            raise ValueError("Неподдерживаемый fee tier")
        fees.append(pool.fee)
    if len(set(tokens)) != len(tokens):
        raise ValueError("Циклический маршрут Converter")
    if reverse:
        tokens.reverse()
        fees.reverse()
    packed = bytes.fromhex(tokens[0][2:])
    for fee, token in zip(fees, tokens[1:]):
        packed += fee.to_bytes(3, "big") + bytes.fromhex(token[2:])
    return tokens, packed


class StaleBlock(ValueError, TimeoutError):
    """Retryable old market data; never a fresh quote or permission to trade."""


class Chain:
    checked_header: BlockData
    _price_cache: tuple[tuple[Pool, int, bytes], D]
    _paper_price_snapshot: tuple[Pool, dict[str, Any], float] | None
    price_block: dict[str, Any]

    def __init__(self, endpoint: str, *, request_timeout: float = 10, max_block_age: float = 30) -> None:
        if not 0 < request_timeout <= 30:
            raise ValueError("Недопустимый RPC timeout")
        if not 1 <= max_block_age <= 30:
            raise ValueError("Недопустимый возраст блока")
        self.max_block_age = max_block_age
        parsed = urlsplit(endpoint)
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")
        ):
            raise ValueError("RPC должен быть HTTPS (HTTP допустим для localhost)")
        if not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError("Некорректный RPC URL")
        self.w3 = Web3(
            BscHTTPProvider(
                endpoint, request_kwargs={"timeout": request_timeout}, exception_retry_configuration=None
            )
        )
        self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        self._decimals: dict[str, int] = {}

    def contract(self, addr: str, abi: Any) -> Contract:
        return self.w3.eth.contract(address=address(addr), abi=abi)

    def call(self, addr: str, abi: Any, name: str, *args: Any, block: int | str = "latest") -> Any:
        return getattr(self.contract(addr, abi).functions, name)(*args).call(block_identifier=block)

    def check(self, *, force_network: bool = True) -> int:
        if force_network and isinstance(self.w3.provider, BscHTTPProvider):
            self.w3.provider.invalidate_network()
        if self.w3.eth.chain_id != 56:
            raise ValueError("RPC подключён не к BSC mainnet (chainId 56)")
        block = self.w3.eth.get_block("latest")
        age = time.time() - block["timestamp"]
        if age > getattr(self, "max_block_age", 30):
            raise StaleBlock("RPC возвращает устаревший блок; новые данные ожидаются")
        if age < -15:
            raise ValueError("RPC возвращает устаревший блок; проверьте узел и часы Mac")
        self.checked_header = block
        return block["number"]

    def check_receipt_access(self) -> None:
        """Reject endpoints that accept broadcasts but refuse receipt reads."""
        head = self.check(force_network=False)
        try:
            for offset in (3, 4, 5):
                block = self.w3.eth.get_block(max(0, head - offset))
                if not block["transactions"]:
                    continue
                tx_hash = block["transactions"][0]
                if not isinstance(tx_hash, HexBytes):
                    raise ValueError("Receipt probe expected a transaction hash")
                receipt = self.w3.eth.get_transaction_receipt(tx_hash)
                if (
                    receipt["transactionHash"] != tx_hash
                    or receipt["blockNumber"] != block["number"]
                    or receipt["status"] not in (0, 1)
                ):
                    raise ValueError("Receipt does not match the probe block")
                return
            raise ValueError("No receipt probe transaction")
        except Exception:
            raise ValueError(
                "RPC не подтверждает чтение receipts. Выберите другой RPC перед LIVE; транзакция не отправлена"
            ) from None

    def canonical_receipt(self, receipt: Mapping[str, Any]) -> str:
        raw = receipt["blockHash"]
        expected = Web3.to_hex(hexstr=HexStr(raw)) if isinstance(raw, str) else Web3.to_hex(raw)
        if len(Web3.to_bytes(hexstr=expected)) != 32:
            raise ValueError("Некорректный hash блока receipt")
        block = self.w3.eth.get_block(receipt["blockNumber"])
        if Web3.to_hex(block["hash"]) != expected or block["number"] != receipt["blockNumber"]:
            raise ValueError("Блок receipt не совпадает с канонической цепочкой")
        return expected

    def balance_snapshot(self, token: str | None, owner: str) -> tuple[int, dict[str, Any]]:
        number = self.check(force_network=False)
        header = self.w3.eth.get_block(number)
        value = self.balance_at(token, owner, number)
        if self.w3.eth.get_block(number)["hash"] != header["hash"]:
            raise ValueError("Блок баланса изменился во время чтения")
        return value, {"blockNumber": number, "blockHash": header["hash"]}

    def balance_at(self, token: str | None, owner: str, number: int) -> int:
        if token is None:
            return self.w3.eth.get_balance(address(owner), block_identifier=number)
        return cast(int, self.call(token, TOKEN_ABI, "balanceOf", owner, block=number))

    def receipt_balance(
        self, token: str | None, owner: str, receipt: Mapping[str, Any], snapshot: Mapping[str, Any]
    ) -> int:
        self.canonical_receipt(snapshot)
        if receipt["blockNumber"] < snapshot["blockNumber"]:
            raise ValueError("Receipt старше снимка баланса")
        self.canonical_receipt(receipt)
        value = self.balance_at(token, owner, receipt["blockNumber"])
        self.canonical_receipt(receipt)
        return value

    def restrict_to_reads(self) -> None:
        original = self.w3.provider.make_request
        allowed = {
            "eth_chainId",
            "eth_getBlockByNumber",
            "eth_getBlockByHash",
            "eth_call",
            "eth_getCode",
            "eth_getLogs",
        }

        def request(method: RPCEndpoint, params: Any) -> RPCResponse:
            if method not in allowed:
                raise RuntimeError("Резервный RPC разрешает только чтение рынка")
            return original(method, params)

        self.w3.provider.make_request = request  # type: ignore[method-assign]  # Deliberate read-only transport wrapper.

    def decimals(self, token: str) -> int:
        token = address(token)
        if token not in self._decimals:
            if not self.w3.eth.get_code(token):
                raise ValueError("По адресу токена нет контракта")
            value = self.call(token, TOKEN_ABI, "decimals")
            if not 0 <= value <= 36:
                raise ValueError("Неподдерживаемые decimals")
            self._decimals[token] = value
        return self._decimals[token]

    def symbol(self, token: str) -> str | bytes | None:
        from web3.exceptions import BadFunctionCallOutput, ContractLogicError

        try:
            return cast(str | None, self.call(token, TOKEN_ABI, "symbol"))
        except (ContractLogicError, BadFunctionCallOutput):
            try:
                return cast(bytes, self.call(token, [fn("symbol", outputs=("bytes32",))], "symbol"))
            except (ContractLogicError, BadFunctionCallOutput):
                return ""

    def balance(self, token: str, owner: str) -> int:
        return cast(int, self.call(token, TOKEN_ABI, "balanceOf", address(owner)))

    def verify_pool(self, pool_address: str, target: str, *, require_liquidity: bool = True) -> Pool:
        self.check()
        pool_address, target = address(pool_address), address(target)
        if not self.w3.eth.get_code(pool_address):
            raise ValueError("Пул не содержит контракта")
        factory = self.call(pool_address, POOL_ABI, "factory")
        t0 = address(self.call(pool_address, POOL_ABI, "token0"))
        t1 = address(self.call(pool_address, POOL_ABI, "token1"))
        if t0 == t1 or target not in (t0, t1):
            raise ValueError("TOKEN ADDRESS не является стороной пула")
        if factory.lower() == V2_FACTORY.lower():
            canonical = self.call(V2_FACTORY, FACTORY_ABI, "getPair", t0, t1)
            router, fee = "V2", 0
        elif factory.lower() == V3_FACTORY.lower():
            fee = self.call(pool_address, POOL_ABI, "fee")
            if fee not in FEES:
                raise ValueError("Неподдерживаемый fee tier")
            canonical = self.call(V3_FACTORY, FACTORY_ABI, "getPool", t0, t1, fee)
            router = "V3"
        else:
            raise ValueError("Пул не принадлежит официальной PancakeSwap factory")
        if canonical.lower() != pool_address.lower():
            raise ValueError("Адрес не совпадает с каноническим пулом factory")
        quote = t1 if t0 == target else t0
        pool = Pool(
            pool_address,
            router,
            target,
            quote,
            self.decimals(target),
            self.decimals(quote),
            t0 == target,
            fee,
        )
        if require_liquidity:
            self.price(pool)
        return pool

    @timed("chain.price")
    def price(self, pool: Pool) -> D:
        # A failed read must invalidate the snapshot used by a subsequent PAPER fill.
        self._paper_price_snapshot = None
        block = self.check(force_network=False)
        header = getattr(self, "checked_header", None)
        key = (pool, block, bytes(header["hash"])) if header is not None and header.get("hash") else None
        cached = getattr(self, "_price_cache", None)
        self.price_cache_hit = False
        if (
            key is not None
            and cached is not None
            and cached[0] == key
            and getattr(self, "price_cache_enabled", True)
        ):
            assert header is not None
            self.price_block = dict(header)
            self.price_cache_hit = True
            self._paper_price_snapshot = (pool, dict(header), time.monotonic())
            TIMINGS.record("chain.price_cache_hit", 0)
            return cast(D, cached[1])
        with localcontext() as context:
            context.prec = 78
            if pool.router == "V2":
                r0, r1, _ = self.call(pool.address, POOL_ABI, "getReserves", block=block)
                if not r0 or not r1:
                    raise ValueError("WAITING: нулевая ликвидность")
                numerator, denominator = (r1, r0) if pool.token_is_0 else (r0, r1)
                ratio = D(numerator) / D(denominator)
            else:
                from dipbot.market.discovery import batch, request

                liquidity, slot = batch(
                    self, [request(pool.address, POOL_ABI, name) for name in ("liquidity", "slot0")], block
                )
                if liquidity is None or slot is None:
                    raise ValueError("Не удалось прочитать состояние V3 через Multicall")
                if not liquidity:
                    raise ValueError("WAITING: нулевая ликвидность")
                sqrt = slot[0]
                if sqrt <= 0:
                    raise ValueError("Пустая цена V3")
                ratio = D(sqrt) ** 2 / D(2) ** 192
                if not pool.token_is_0:
                    ratio = 1 / ratio
            price = ratio * D(10) ** (pool.token_decimals - pool.quote_decimals)
            if key is not None:
                assert header is not None
                self.canonical_receipt({"blockNumber": block, "blockHash": header["hash"]})
                self._price_cache = (key, price)
            if header is not None:
                self.price_block = dict(header)
                self._paper_price_snapshot = (pool, dict(header), time.monotonic())
            return price

    def resolve_address(self, raw: str, catalogs: dict[str, dict[str, str]]) -> Resolution:
        from dipbot.market.discovery import resolve

        return cast("Resolution", resolve(self, raw, catalogs))

    def discover_candidates(self, target: str, quote: str, router: str, pair_name: str) -> Any:
        from dipbot.market.autopair import Candidate

        self.check()
        target, quote = address(target), address(quote)
        if target == quote:
            return []
        if router == "V2":
            addresses = [self.call(V2_FACTORY, FACTORY_ABI, "getPair", target, quote)]
        elif router == "V3":
            addresses = [self.call(V3_FACTORY, FACTORY_ABI, "getPool", target, quote, fee) for fee in FEES]
        else:
            raise ValueError("Неподдерживаемый router")
        result, seen = [], set()
        block = self.check()
        for raw in addresses:
            if raw.lower() == ZERO or raw.lower() in seen:
                continue
            seen.add(raw.lower())
            pool = self.verify_pool(raw, target, require_liquidity=False)
            if pool.router != router or pool.quote != quote:
                raise ValueError("Factory вернула пул другого маршрута")
            if router == "V2":
                r0, r1, _ = self.call(pool.address, POOL_ABI, "getReserves", block=block)
                score = r0 * r1
            else:
                score = self.call(pool.address, POOL_ABI, "liquidity", block=block)
            result.append(Candidate(pool, pair_name, score > 0, score))
        return result

    def find_pools(self, target: str, quote: str, routers: tuple[str, ...] = ("V2", "V3")) -> list[Pool]:
        self.check()
        target, quote = address(target), address(quote)
        if target == quote:
            return []
        result = []
        candidates = []
        if "V2" in routers:
            candidates.append(self.call(V2_FACTORY, FACTORY_ABI, "getPair", target, quote))
        if "V3" in routers:
            candidates.extend(self.call(V3_FACTORY, FACTORY_ABI, "getPool", target, quote, f) for f in FEES)
        for candidate in candidates:
            if candidate.lower() != ZERO:
                try:
                    result.append(self.verify_pool(candidate, target))
                except ValueError as exc:
                    if "WAITING" not in str(exc):
                        raise
        return result

    @timed("chain.quote")
    def quote(self, pool: Pool, amount: int, buy: bool, *, block: int | str = "latest") -> int:
        if amount <= 0:
            raise ValueError("Нулевая сумма")
        token_in, token_out = (pool.quote, pool.token) if buy else (pool.token, pool.quote)
        if pool.router == "V2":
            return cast(
                int,
                self.call(V2_ROUTER, V2_ABI, "getAmountsOut", amount, [token_in, token_out], block=block)[-1],
            )
        return cast(
            int,
            self.call(
                V3_QUOTER,
                QUOTER_ABI,
                "quoteExactInputSingle",
                (token_in, token_out, amount, pool.fee, 0),
                block=block,
            )[0],
        )

    @timed("chain.entry_quote")
    def entry_quote(self, pool: Pool, amount: int, maximum: D) -> EntryQuote:
        from dipbot.domain.entry_guard import assess

        block = self.check(force_network=False)
        header = cast(dict[str, Any], dict(self.checked_header))
        target = self.quote(pool, amount, True, block=block)
        if type(target) is not int or target <= 0:
            from dipbot.domain.entry_guard import EntryRejected

            raise EntryRejected("Вход пропущен: нулевая котировка BUY")
        reverse = self.quote(pool, target, False, block=block)
        # A numbered block can change during a reorg. Revalidate before accepting.
        self.canonical_receipt({"blockNumber": block, "blockHash": header["hash"]})
        self.quote_context = {"block": block, "block_hash": bytes(header["hash"]).hex()}
        return cast("EntryQuote", assess(amount, target, reverse, block, maximum))

    @timed("chain.exit_quote")
    def exit_quote(self, pool: Pool, amount: int) -> int:
        block = self.check(force_network=False)
        header = cast(dict[str, Any], dict(self.checked_header))
        output = self.quote(pool, amount, False, block=block)
        if type(output) is not int or not 0 <= output < 2**256:
            raise ValueError("Некорректная котировка выхода")
        self.canonical_receipt({"blockNumber": block, "blockHash": header["hash"]})
        self.quote_context = {"block": block, "block_hash": bytes(header["hash"]).hex()}
        return output

    def paper_quote(self, pool: Pool, amount: int, buy: bool) -> int:
        # One immutable block for this simulated fill; no approval or signature.
        snapshot = getattr(self, "_paper_price_snapshot", None)
        self._paper_price_snapshot = None  # Single-use, including failed quotes.
        reusable = (
            getattr(self, "paper_price_reuse_enabled", True)
            and snapshot is not None
            and snapshot[0] == pool
            and 0 <= time.monotonic() - snapshot[2] <= 0.55
            and "timestamp" in snapshot[1]
            and -15 <= time.time() - snapshot[1]["timestamp"] <= getattr(self, "max_block_age", 30)
        )
        if reusable:
            assert snapshot is not None
            header = dict(snapshot[1])
            block = header["number"]
            TIMINGS.record("chain.paper_price_snapshot_reused", 0)
        else:
            block = self.check(force_network=False)
            header = cast(dict[str, Any], dict(self.checked_header))
        output = self.quote(pool, amount, buy, block=block)
        if type(output) is not int or not 0 <= output < 2**256:
            raise ValueError("Некорректная котировка PAPER")
        self.canonical_receipt({"blockNumber": block, "blockHash": header["hash"]})
        self.quote_context = {"block": block, "block_hash": bytes(header["hash"]).hex()}
        return output

    def quote_route(self, route: list[Pool], amount: int, reverse: bool = False) -> int:
        if not 0 < amount < 2**256:
            raise ValueError("Сумма Converter вне диапазона")
        tokens, packed = route_path(route, reverse)
        if route[0].router == "V2":
            return cast(int, self.call(V2_ROUTER, V2_ABI, "getAmountsOut", amount, tokens)[-1])
        return cast(int, self.call(V3_QUOTER, QUOTER_ABI, "quoteExactInput", packed, amount)[0])
