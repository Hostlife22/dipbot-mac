"""Canonical, bounded pool event screening. Swap count is not organic volume."""
from web3 import Web3
from .chain import address

SIGNATURES = {
    'V2': 'Swap(address,uint256,uint256,uint256,uint256,address)',
    'V3': 'Swap(address,address,int256,int256,uint160,uint128,int24,uint128,uint128)',
}


def swap_count(chain, pool, blocks=100):
    if type(blocks) is not int or not 1 <= blocks <= 100:
        raise ValueError('Окно активности должно быть от 1 до 100 блоков')
    end = chain.check(force_network=False)
    header = dict(chain.checked_header)
    start = max(0, end-blocks+1)
    events = read_swaps(chain, pool, start, end, header)
    return {'count':len(events), 'from_block':start, 'to_block':end}


def read_swaps(chain, pool, start, end, header, limit=10000):
    topic = Web3.keccak(text=SIGNATURES[pool.router])
    logs = chain.w3.eth.get_logs({'address':pool.address, 'fromBlock':start,
                                 'toBlock':end, 'topics':[Web3.to_hex(topic)]})
    if len(logs) > limit:
        raise ValueError('Ответ активности превышает лимит; вход запрещён')
    identities = {}
    for row in logs:
        if (row.get('removed', False) or address(row['address']) != pool.address
                or not start <= row['blockNumber'] <= end
                or len(row['topics']) != 3 or bytes(row['topics'][0]) != topic
                or len(row['data']) != (128 if pool.router == 'V2' else 224)
                or len(row['blockHash']) != 32 or len(row['transactionHash']) != 32
                or type(row['logIndex']) is not int or row['logIndex'] < 0):
            raise ValueError('Некорректное событие активности; вход запрещён')
        key = (bytes(row['blockHash']), bytes(row['transactionHash']), row['logIndex'])
        identities[key] = {'block': row['blockNumber'], 'block_hash': key[0].hex(),
                           'transaction_hash': key[1].hex(), 'log_index': key[2], 'data': bytes(row['data']).hex()}
    chain.canonical_receipt({'blockNumber':end, 'blockHash':header['hash']})
    return sorted(identities.values(), key=lambda row: (row['block'],row['log_index']))
