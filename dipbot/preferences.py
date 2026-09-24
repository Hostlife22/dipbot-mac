"""Versioned Mac UI preferences. No credentials or automatic LIVE resume."""
from .strategy import D, Settings

FIELDS = ('amount', 'dip', 'take_profit', 'stop_loss', 'slippage', 'dynamic')


def normalize(value):
    if not isinstance(value, dict) or value.get('version') != 1:
        raise ValueError('Неизвестный формат настроек')
    try:
        settings = Settings(**{k: D(str(value['settings'][k])) for k in FIELDS})
        gas, interval = D(str(value['gas'])), D(str(value['interval']))
    except (KeyError, TypeError, ArithmeticError) as exc:
        raise ValueError('Повреждены настройки') from exc
    if not gas.is_finite() or not 0 < gas <= 1000:
        raise ValueError('Недопустимый GAS GWEI')
    if not interval.is_finite() or not D('0.1') <= interval <= D('0.5'):
        raise ValueError('Недопустимый интервал')
    return {'version': 1, 'settings': {k: str(getattr(settings, k)) for k in FIELDS},
            'gas': str(gas), 'interval': str(interval)}


def save(store, value):
    normalized = normalize(value)
    previous = store.data.copy()
    store.data['ui_preferences'] = normalized
    try:
        store.save()
    except Exception:
        store.data = previous
        raise
