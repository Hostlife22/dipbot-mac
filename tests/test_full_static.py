from types import SimpleNamespace
from dataclasses import asdict
import pytest
from web3 import Web3
from eth_abi.exceptions import DecodingError
from dipbot import discovery, preferences, wallet_registry
from dipbot.chain import WBNB, USDT, address, POOL_ABI
from dipbot.strategy import Settings, D
from tools.native_models import converter_preview, registry_key, purpose_entropy


def fallback_chain(payload):
    w3=Web3()
    def contract(addr,abi):
        if addr==discovery.MULTICALL:
            return SimpleNamespace(functions=SimpleNamespace(aggregate3=lambda calls:
                SimpleNamespace(call=lambda **kwargs:[(True,payload)]*len(calls))))
        return w3.eth.contract(address=address(addr),abi=abi)
    w3.eth.get_code=lambda *args,**kwargs:b'contract'
    return SimpleNamespace(w3=w3,contract=contract,check=lambda:42)


def test_wbnb_empty_success_for_missing_pool_selectors_is_catalog_token():
    result=discovery.resolve(fallback_chain(b''),WBNB,{'V2':{'WBNB':WBNB}})
    assert result.state=='CATALOG_TOKEN' and result.input_kind=='PROFILE_TOKEN'
    assert result.selected is None and not result.candidates


def test_empty_success_does_not_mask_mandatory_read():
    with pytest.raises(DecodingError):
        discovery.batch(fallback_chain(b''),[discovery.request(WBNB,POOL_ABI,'factory')],42)


def test_nonempty_malformed_optional_result_is_still_an_error():
    with pytest.raises(DecodingError):
        discovery.resolve(fallback_chain(b'\x01'),WBNB,{'V2':{'WBNB':WBNB}})


def test_recovered_ui_defaults_and_old_preferences_coexist():
    migrated=preferences.from_windows_ui({})
    assert migrated['settings']=={'amount':'0.02','dip':'3','take_profit':'2',
        'stop_loss':'2','slippage':'3','dynamic':'150'}
    old={'version':1,'settings':{k:str(v) for k,v in asdict(Settings(stop_loss=D(5),slippage=D(2))).items() if k!='max_gap'},
         'gas':'0.1','interval':'0.1'}
    assert preferences.normalize(old)['settings']['stop_loss']=='5'
    assert preferences.normalize(old)['settings']['slippage']=='2'


def test_windows_per_pair_amount_beats_legacy_amount_and_uses_missing_field_defaults():
    value=preferences.from_windows_ui({'trade_router':'v3','pair':'USDT',
        'trade':{'amount_wbnb':'0.02'},'pair_amounts':{'V3:USDT':'25'}})
    assert value['settings']['amount']=='25'
    assert value['settings']['slippage']=='3' and value['settings']['stop_loss']=='2'


@pytest.mark.parametrize('router,slip,expected',[
    ('V2',2,9300),('V3',2,9800),('V2',20,7500),('V2',0,9500),
    ('V3',0.005,10000),('V3',0.015,9998),
])
def test_native_converter_preview_vectors(router,slip,expected):
    assert converter_preview(10000,slip,router)['min_out_raw']==expected


def test_native_minimum_one_raw_unit_and_parameter_limits():
    assert converter_preview(1,20,'V2')['min_out_raw']==1
    for slip in (-1,21,float('nan'),float('inf')):
        with pytest.raises(ValueError):converter_preview(10000,slip,'V2')


def test_router_scoped_registry_identity():
    v2=registry_key(' v2 ',USDT)
    assert v2==('V2',USDT.lower()) and v2!=registry_key('V3',USDT)


def test_synthetic_dpapi_purpose_domains_are_separate():
    options={'default_prefix':'TEST|','owner_prefix':'OWNER|','owner_purposes':{'owner-test'}}
    assert purpose_entropy('runtime-settings',**options)==b'TEST|runtime-settings'
    assert purpose_entropy('profiles',**options)==b'TEST|profiles'
    assert purpose_entropy('owner-test',**options)==b'OWNER|owner-test'


def test_read_only_rpc_guard_blocks_writes_before_provider():
    from tools.read_only_probe import guard_provider
    calls=[]
    provider=SimpleNamespace(make_request=lambda method,params:calls.append(method) or {'result':'0x38'})
    counts=guard_provider(provider)
    assert provider.make_request('eth_chainId',[])=={'result':'0x38'}
    for method in ('eth_sendRawTransaction','eth_sendTransaction','personal_sign'):
        with pytest.raises(RuntimeError):provider.make_request(method,[])
    assert calls==['eth_chainId'] and dict(counts)=={'eth_chainId':1}


def test_public_ui_file_conversion_excludes_credentials_and_preserves_existing_files(tmp_path):
    import json
    from tools.migrate_windows_ui import convert
    source=tmp_path/'windows.json';out=tmp_path/'preferences.json'
    source.write_text(json.dumps({'trade':{'amount_wbnb':'0.08'},'private_key':'synthetic-secret'}))
    result=convert(source,out)
    assert result['settings']['amount']=='0.08' and 'synthetic-secret' not in out.read_text()
    with pytest.raises(FileExistsError):convert(source,out)
    assert json.loads(out.read_text())==result


def test_vault_cannot_be_mistaken_for_ui_defaults(tmp_path):
    from tools.migrate_windows_ui import convert
    source=tmp_path/'vault.json';out=tmp_path/'out.json'
    source.write_text('{"format":"NRNF-DPAPI","version":1,"payload":"eA=="}')
    with pytest.raises(ValueError):convert(source,out)
    assert not out.exists()
