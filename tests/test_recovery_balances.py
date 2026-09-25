from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace
import pytest
from dipbot.recovery import compare_positions
from dipbot.storage import Store
from test_autopair_dynamic import POOL
from test_app_autopair_flow import window


@pytest.mark.parametrize('actual,match',[(100,True),(99,False)])
def test_comparison_pins_block_and_preserves_records(tmp_path,actual,match):
    store=Store(tmp_path/'state.json')
    store.data['positions']={'0x'+'34'*20+':'+POOL.address:{'amount':100,'pool':asdict(POOL)}}
    store.data['operation']={'pending':True}
    before=deepcopy(store.data);calls=[]
    chain=SimpleNamespace(check=lambda **kw:12,
        w3=SimpleNamespace(eth=SimpleNamespace(get_block=lambda n:{'hash':b'a'})),
        balance_at=lambda t,o,n:(calls.append(n) or actual))
    result=compare_positions(chain,store)
    assert result['rows'][0]['matches']==match and calls==[12]
    assert store.data==before
    hashes=iter([b'a',b'b'])
    chain.w3.eth.get_block=lambda n:{'hash':next(hashes)}
    with pytest.raises(ValueError,match='Блок изменился'):compare_positions(chain,store)
    assert store.data==before


def test_ui_shows_mismatch_and_failed_comparison(window):
    window.on_event('position_comparison',{'block':12,'rows':[{'owner':'owner','token':'token','pool':'pool',
        'decimals':2,'saved_raw':100,'actual_raw':99,'matches':False}]})
    assert 'РАСХОЖДЕНИЕ' in window.position_comparison.text()
    assert '0.99' in window.position_comparison.text()
    window.on_event('position_comparison_error','TimeoutError')
    assert 'Сверка не выполнена' in window.position_comparison.text()
