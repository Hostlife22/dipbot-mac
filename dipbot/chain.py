from .telemetry import timed, TIMINGS
from dataclasses import dataclass
from decimal import Decimal, localcontext
import json
import time
from importlib.resources import files
from urllib.parse import urlsplit

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

from .strategy import D
from .rpc import BscHTTPProvider

V2_FACTORY = "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"
V3_FACTORY = "0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865"
V2_ROUTER = "0x10ED43C718714eb63d5aA57B78B54704E256024E"
V3_ROUTER = "0x1b81D678ffb9C0263b24A97847620C99d213eB14"
V3_QUOTER = "0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997"
WBNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
USDT = "0x55d398326f99059fF775485246999027B3197955"
ETH = "0x2170Ed0880ac9A755fd29B2688956BD959F933F8"
ZERO = "0x" + "0" * 40
FEES = (100, 500, 2500, 10000)


def fn(name, inputs=(), outputs=(), mutability="view"):
    return {"type": "function", "name": name, "stateMutability": mutability,
            "inputs": [{"name": f"p{i}", "type": t} for i, t in enumerate(inputs)],
            "outputs": [{"name": f"r{i}", "type": t} for i, t in enumerate(outputs)]}


TOKEN_ABI = [fn("decimals", outputs=("uint8",)), fn("symbol", outputs=("string",)),
             fn("balanceOf", ("address",), ("uint256",)),
             fn("allowance", ("address", "address"), ("uint256",)),
             fn("approve", ("address", "uint256"), ("bool",), "nonpayable"),
             fn("deposit", mutability="payable"),
             fn("withdraw", ("uint256",), mutability="nonpayable")]
POOL_ABI = [fn("factory", outputs=("address",)), fn("token0", outputs=("address",)),
            fn("token1", outputs=("address",)), fn("fee", outputs=("uint24",)),
            fn("liquidity", outputs=("uint128",)),
            fn("getReserves", outputs=("uint112", "uint112", "uint32")),
            fn("slot0", outputs=("uint160", "int24", "uint16", "uint16", "uint16", "uint32", "bool"))]
FACTORY_ABI = [fn("getPair", ("address", "address"), ("address",)),
               fn("getPool", ("address", "address", "uint24"), ("address",))]
V2_ABI = [fn("factory", outputs=("address",)), fn("WETH", outputs=("address",)),
          fn("getAmountsOut", ("uint256", "address[]"), ("uint256[]",)),
          fn("swapExactETHForTokensSupportingFeeOnTransferTokens",
             ("uint256", "address[]", "address", "uint256"), mutability="payable"),
          fn("swapExactTokensForETHSupportingFeeOnTransferTokens",
             ("uint256", "uint256", "address[]", "address", "uint256"), mutability="nonpayable"),
          fn("swapExactTokensForTokensSupportingFeeOnTransferTokens",
             ("uint256", "uint256", "address[]", "address", "uint256"), mutability="nonpayable")]


def tuple_fn(name, components, outputs):
    result = fn(name, outputs=outputs, mutability="payable" if name in ("exactInputSingle", "exactInput") else "nonpayable")
    result["inputs"] = [{"name": "params", "type": "tuple",
                         "components": [{"name": n, "type": t} for n, t in components]}]
    return result


V3_ABI = [fn("factory", outputs=("address",)), fn("WETH9", outputs=("address",)),
          tuple_fn("exactInput", [("path", "bytes"), ("recipient", "address"),
                   ("deadline", "uint256"), ("amountIn", "uint256"),
                   ("amountOutMinimum", "uint256")], ("uint256",)),
          tuple_fn("exactInputSingle", [("tokenIn", "address"), ("tokenOut", "address"),
                   ("fee", "uint24"), ("recipient", "address"), ("deadline", "uint256"),
                   ("amountIn", "uint256"), ("amountOutMinimum", "uint256"),
                   ("sqrtPriceLimitX96", "uint160")], ("uint256",))]
QUOTER_ABI = [fn("quoteExactInput", ("bytes", "uint256"),
                ("uint256", "uint160[]", "uint32[]", "uint256"), "nonpayable"),
             tuple_fn("quoteExactInputSingle", [("tokenIn", "address"), ("tokenOut", "address"),
                ("amountIn", "uint256"), ("fee", "uint24"), ("sqrtPriceLimitX96", "uint160")],
                ("uint256", "uint160", "uint32", "uint256"))]


def address(value: str) -> str:
    if not Web3.is_address(value) or value.lower() == ZERO:
        raise ValueError("Нужен ненулевой адрес 0x… (40 hex символов)")
    return Web3.to_checksum_address(value)


def profiles() -> dict[str, str]:
    return {p["symbol"]: address(p["address"]) for p in json.loads(files("dipbot").joinpath("profiles.json").read_text())}


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
    def label(self):
        names = {v.lower(): k for k, v in profiles().items()}
        return f"{self.router} / {names.get(self.quote.lower(), self.quote[:10])} / {self.fee or 2500} · {self.address}"


def route_path(route, reverse=False):
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
    def __init__(self, endpoint: str, *, request_timeout=10, max_block_age=30):
        if not 0 < request_timeout <= 30:
            raise ValueError("Недопустимый RPC timeout")
        if not 1 <= max_block_age <= 30:
            raise ValueError("Недопустимый возраст блока")
        self.max_block_age = max_block_age
        parsed = urlsplit(endpoint)
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")):
            raise ValueError("RPC должен быть HTTPS (HTTP допустим для localhost)")
        if not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError("Некорректный RPC URL")
        self.w3 = Web3(BscHTTPProvider(endpoint, request_kwargs={"timeout": request_timeout},
                                       exception_retry_configuration=None))
        self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        self._decimals = {}

    def contract(self, addr, abi):
        return self.w3.eth.contract(address=address(addr), abi=abi)

    def call(self, addr, abi, name, *args, block="latest"):
        return getattr(self.contract(addr, abi).functions, name)(*args).call(block_identifier=block)

    def check(self, *, force_network=True):
        if force_network and isinstance(self.w3.provider, BscHTTPProvider):
            self.w3.provider.invalidate_network()
        if self.w3.eth.chain_id != 56:
            raise ValueError("RPC подключён не к BSC mainnet (chainId 56)")
        block = self.w3.eth.get_block("latest")
        age = time.time() - block["timestamp"]
        if age > getattr(self, 'max_block_age', 30):
            raise StaleBlock('RPC возвращает устаревший блок; новые данные ожидаются')
        if age < -15:
            raise ValueError("RPC возвращает устаревший блок; проверьте узел и часы Mac")
        self.checked_header = block
        return block["number"]

    def check_receipt_access(self):
        """Reject endpoints that accept broadcasts but refuse receipt reads."""
        head = self.check(force_network=False)
        try:
            for offset in (3, 4, 5):
                block = self.w3.eth.get_block(max(0, head-offset))
                if not block['transactions']:
                    continue
                tx_hash = block['transactions'][0]
                receipt = self.w3.eth.get_transaction_receipt(tx_hash)
                if (receipt['transactionHash'] != tx_hash or receipt['blockNumber'] != block['number']
                        or receipt['status'] not in (0, 1)):
                    raise ValueError('Receipt does not match the probe block')
                return
            raise ValueError('No receipt probe transaction')
        except Exception:
            raise ValueError('RPC не подтверждает чтение receipts. Выберите другой RPC перед LIVE; транзакция не отправлена') from None

    def canonical_receipt(self, receipt):
        raw = receipt['blockHash']
        expected = Web3.to_hex(hexstr=raw) if isinstance(raw, str) else Web3.to_hex(raw)
        if len(Web3.to_bytes(hexstr=expected)) != 32:
            raise ValueError('Некорректный hash блока receipt')
        block = self.w3.eth.get_block(receipt['blockNumber'])
        if Web3.to_hex(block['hash']) != expected or block['number'] != receipt['blockNumber']:
            raise ValueError('Блок receipt не совпадает с канонической цепочкой')
        return expected

    def balance_snapshot(self, token, owner):
        number = self.check(force_network=False)
        header = self.w3.eth.get_block(number)
        value = self.balance_at(token, owner, number)
        if self.w3.eth.get_block(number)['hash'] != header['hash']:
            raise ValueError('Блок баланса изменился во время чтения')
        return value, {'blockNumber': number, 'blockHash': header['hash']}

    def balance_at(self, token, owner, number):
        if token is None:
            return self.w3.eth.get_balance(owner, block_identifier=number)
        return self.call(token, TOKEN_ABI, 'balanceOf', owner, block=number)

    def receipt_balance(self, token, owner, receipt, snapshot):
        self.canonical_receipt(snapshot)
        if receipt['blockNumber'] < snapshot['blockNumber']:
            raise ValueError('Receipt старше снимка баланса')
        self.canonical_receipt(receipt)
        value = self.balance_at(token, owner, receipt['blockNumber'])
        self.canonical_receipt(receipt)
        return value

    def restrict_to_reads(self):
        original = self.w3.provider.make_request
        allowed = {'eth_chainId', 'eth_getBlockByNumber', 'eth_getBlockByHash', 'eth_call', 'eth_getCode', 'eth_getLogs'}
        def request(method, params):
            if method not in allowed:
                raise RuntimeError('Резервный RPC разрешает только чтение рынка')
            return original(method, params)
        self.w3.provider.make_request = request

    def decimals(self, token):
        token = address(token)
        if token not in self._decimals:
            if not self.w3.eth.get_code(token):
                raise ValueError("По адресу токена нет контракта")
            value = self.call(token, TOKEN_ABI, "decimals")
            if not 0 <= value <= 36:
                raise ValueError("Неподдерживаемые decimals")
            self._decimals[token] = value
        return self._decimals[token]

    def symbol(self, token):
        from web3.exceptions import ContractLogicError, BadFunctionCallOutput
        try:
            return self.call(token, TOKEN_ABI, "symbol")
        except (ContractLogicError, BadFunctionCallOutput):
            try:
                return self.call(token, [fn("symbol", outputs=("bytes32",))], "symbol")
            except (ContractLogicError, BadFunctionCallOutput):
                return ""

    def balance(self, token, owner):
        return self.call(token, TOKEN_ABI, "balanceOf", address(owner))

    def verify_pool(self, pool_address: str, target: str, *, require_liquidity=True) -> Pool:
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
        pool = Pool(pool_address, router, target, quote, self.decimals(target),
                    self.decimals(quote), t0 == target, fee)
        if require_liquidity:
            self.price(pool)
        return pool

    @timed("chain.price")
    def price(self, pool: Pool) -> D:
        block = self.check(force_network=False)
        header = getattr(self, 'checked_header', None)
        key = (pool, block, bytes(header['hash'])) if header is not None and header.get('hash') else None
        cached = getattr(self, '_price_cache', None)
        self.price_cache_hit = False
        if key is not None and cached is not None and cached[0] == key and getattr(self, 'price_cache_enabled', True):
            self.price_block = dict(header)
            self.price_cache_hit = True
            TIMINGS.record('chain.price_cache_hit', 0)
            return cached[1]
        with localcontext() as context:
            context.prec = 78
            if pool.router == "V2":
                r0, r1, _ = self.call(pool.address, POOL_ABI, "getReserves", block=block)
                if not r0 or not r1:
                    raise ValueError("WAITING: нулевая ликвидность")
                numerator, denominator = (r1, r0) if pool.token_is_0 else (r0, r1)
                ratio = D(numerator) / D(denominator)
            else:
                from .discovery import batch, request
                liquidity, slot = batch(self, [request(pool.address, POOL_ABI, name)
                                               for name in ("liquidity", "slot0")], block)
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
                self.canonical_receipt({'blockNumber':block, 'blockHash':header['hash']})
                self._price_cache = (key, price)
            if header is not None:
                self.price_block = dict(header)
            return price

    def resolve_address(self, raw, catalogs):
        from .discovery import resolve
        return resolve(self, raw, catalogs)

    def discover_candidates(self, target, quote, router, pair_name):
        from .autopair import Candidate
        self.check()
        target, quote = address(target), address(quote)
        if target == quote:
            return []
        if router == "V2":
            addresses = [self.call(V2_FACTORY, FACTORY_ABI, "getPair", target, quote)]
        elif router == "V3":
            addresses = [self.call(V3_FACTORY, FACTORY_ABI, "getPool", target, quote, fee)
                         for fee in FEES]
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

    def find_pools(self, target: str, quote: str, routers=("V2", "V3")) -> list[Pool]:
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
    def quote(self, pool: Pool, amount: int, buy: bool, *, block="latest"):
        if amount <= 0:
            raise ValueError("Нулевая сумма")
        token_in, token_out = (pool.quote, pool.token) if buy else (pool.token, pool.quote)
        if pool.router == "V2":
            return self.call(V2_ROUTER, V2_ABI, "getAmountsOut", amount, [token_in, token_out], block=block)[-1]
        return self.call(V3_QUOTER, QUOTER_ABI, "quoteExactInputSingle",
                         (token_in, token_out, amount, pool.fee, 0), block=block)[0]

    @timed("chain.entry_quote")
    def entry_quote(self, pool, amount, maximum):
        from .entry_guard import assess
        block = self.check(force_network=False)
        header = dict(self.checked_header)
        target = self.quote(pool, amount, True, block=block)
        if type(target) is not int or target <= 0:
            from .entry_guard import EntryRejected
            raise EntryRejected('Вход пропущен: нулевая котировка BUY')
        reverse = self.quote(pool, target, False, block=block)
        # A numbered block can change during a reorg. Revalidate before accepting.
        self.canonical_receipt({'blockNumber': block, 'blockHash': header['hash']})
        self.quote_context = {'block': block, 'block_hash': bytes(header['hash']).hex()}
        return assess(amount, target, reverse, block, maximum)

    @timed("chain.exit_quote")
    def exit_quote(self, pool, amount):
        block = self.check(force_network=False)
        header = dict(self.checked_header)
        output = self.quote(pool, amount, False, block=block)
        if type(output) is not int or not 0 <= output < 2**256:
            raise ValueError('Некорректная котировка выхода')
        self.canonical_receipt({'blockNumber': block, 'blockHash': header['hash']})
        self.quote_context = {'block': block, 'block_hash': bytes(header['hash']).hex()}
        return output

    def paper_quote(self, pool, amount, buy):
        # One immutable block for this simulated fill; no approval or signature.
        block = self.check(force_network=False)
        header = dict(self.checked_header)
        output = self.quote(pool, amount, buy, block=block)
        if type(output) is not int or not 0 < output < 2**256:
            raise ValueError('Некорректная котировка PAPER')
        self.canonical_receipt({'blockNumber': block, 'blockHash': header['hash']})
        self.quote_context = {'block': block, 'block_hash': bytes(header['hash']).hex()}
        return output

    def quote_route(self, route, amount, reverse=False):
        if not 0 < amount < 2**256:
            raise ValueError("Сумма Converter вне диапазона")
        tokens, packed = route_path(route, reverse)
        if route[0].router == "V2":
            return self.call(V2_ROUTER, V2_ABI, "getAmountsOut", amount, tokens)[-1]
        return self.call(V3_QUOTER, QUOTER_ABI, "quoteExactInput", packed, amount)[0]
