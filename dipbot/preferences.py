"""Versioned Mac UI preferences. No credentials or automatic LIVE resume."""
from .strategy import D, Settings
from .signal_policy import SignalPolicy
from .storage import SaveAfterReplaceError

FIELDS = ('amount', 'dip', 'take_profit', 'stop_loss', 'slippage', 'dynamic', 'max_roundtrip_loss')


def normalize(value):
    if not isinstance(value, dict) or value.get('version') != 1:
        raise ValueError('Неизвестный формат настроек')
    try:
        settings = Settings(**{k: D(str((value['settings'].get(k, '3') if k == 'max_roundtrip_loss' else value['settings'][k]))) for k in FIELDS})
        gas, interval = D(str(value['gas'])), D(str(value['interval']))
    except (KeyError, TypeError, ArithmeticError) as exc:
        raise ValueError('Повреждены настройки') from exc
    if not gas.is_finite() or not 0 < gas <= 1000:
        raise ValueError('Недопустимый GAS GWEI')
    if not interval.is_finite() or not D('0.1') <= interval <= D('0.5'):
        raise ValueError('Недопустимый интервал')
    result = {'version': 1, 'settings': {k: str(getattr(settings, k)) for k in FIELDS},
              'gas': str(gas), 'interval': str(interval)}
    if 'record_market' in value:
        if type(value['record_market']) is not bool:
            raise ValueError('Некорректная настройка записи рынка')
        result['record_market'] = value['record_market']
    if 'signal_policy' in value:
        result['signal_policy'] = SignalPolicy.parse(value['signal_policy']).export()
    if 'selection' in value or 'pair_amounts' in value:
        try:
            selection = value['selection']
            pair_key(selection['router'], selection['pair'])
            amounts = value.get('pair_amounts', {})
            checked = {}
            for key, amount in amounts.items():
                router, pair = key.split(':', 1)
                checked[pair_key(router,pair)] = positive_amount(amount)
            result['selection'] = {k:selection[k] for k in ('router','pair')}
            result['pair_amounts'] = checked
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError('Повреждены настройки пар') from exc
    return result


def save(store, value):
    normalized = normalize(value)
    previous = store.data.copy()
    store.data['ui_preferences'] = normalized
    try:
        store.save()
    except SaveAfterReplaceError:
        # Replacement already happened. Keep the visible new state, report the
        # durability error, and do not pretend the on-disk change was rolled back.
        raise
    except Exception:
        store.data = previous
        raise


def pair_key(router, pair):
    if router not in ('AUTO', 'V2', 'V3') or not isinstance(pair, str) or not pair:
        raise ValueError('Некорректный router/pair')
    return router.upper() + ':' + pair


def positive_amount(raw):
    try:
        number = D(str(raw))
    except ArithmeticError as exc:
        raise ValueError('Некорректная сумма пары') from exc
    if not number.is_finite() or number <= 0:
        raise ValueError('Некорректная сумма пары')
    return str(number)


def from_windows_ui(payload, gas='0.1', interval='0.1'):
    """Explicit pure-data migration of public UI JSON, never credentials/vault."""
    if not isinstance(payload, dict) or not isinstance(payload.get('trade', {}), dict):
        raise ValueError('Повреждён публичный Windows UI JSON')
    trade = payload.get('trade', {})
    defaults = Settings()
    mapping = {'amount':'amount_wbnb','dip':'dip_pct','take_profit':'tp_pct',
               'stop_loss':'sl_pct','slippage':'slippage_pct','dynamic':'dynamic_dip_x100'}
    settings = {key:str(trade.get(old, getattr(defaults,key))) for key,old in mapping.items()}
    router, pair = payload.get('trade_router','V2'), payload.get('pair','WBNB')
    if not isinstance(router, str) or not isinstance(payload.get('pair_amounts', {}), dict):
        raise ValueError('Повреждены Windows настройки пар')
    router = router.upper()
    amounts = dict(payload.get('pair_amounts', {}))
    key = pair_key(router,pair)
    amounts.setdefault(key,settings['amount'])
    settings['amount'] = amounts[key]
    return normalize({'version':1,'settings':settings,'gas':gas,'interval':interval,
                      'selection':{'router':router,'pair':pair},'pair_amounts':amounts})
