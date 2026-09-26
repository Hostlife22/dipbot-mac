"""Shared discovery fixtures/builders."""

from dipbot.domain.assets import WBNB
from dipbot.market.chain import Pool, address

OWNER = address("0x" + "34" * 20)


TOKEN = address("0x" + "ab" * 20)


BASE = address("0x" + "cd" * 20)


POOL = Pool(address("0x" + "12" * 20), "V2", TOKEN, address(WBNB), 18, 18, True)
