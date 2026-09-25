"""Mutation-aware JSON cache for historical ledgers; on-disk format stays unchanged."""
import json
from copy import deepcopy


def tracked(value, changed):
    if isinstance(value, dict):
        return TrackedDict(value, changed)
    if isinstance(value, list):
        return TrackedList(value, changed)
    return value


class TrackedDict(dict):
    def __init__(self, value, changed):
        self.changed = changed
        dict.__init__(self, ((k, tracked(v, changed)) for k,v in value.items()))

    def __deepcopy__(self, memo):
        result = {}
        memo[id(self)] = result
        result.update((deepcopy(k,memo),deepcopy(v,memo)) for k,v in self.items())
        return result

    def __setitem__(self, key, value):
        value = tracked(value, self.changed)
        self.changed()
        dict.__setitem__(self, key, value)

    def __delitem__(self, key):
        self.changed();dict.__delitem__(self,key)

    def update(self, *args, **kwargs):
        for k,v in dict(*args, **kwargs).items():self[k]=v

    def setdefault(self, key, default=None):
        if key not in self:self[key]=default
        return self[key]

    def pop(self, key, *default):
        self.changed();return dict.pop(self,key,*default)

    def popitem(self):
        self.changed();return dict.popitem(self)

    def clear(self):
        self.changed();dict.clear(self)

    def __ior__(self, other):
        self.update(other);return self


class TrackedList(list):
    def __init__(self, value, changed):
        self.changed=changed
        list.__init__(self,(tracked(v,changed) for v in value))

    def __deepcopy__(self, memo):
        result = []
        memo[id(self)] = result
        result.extend(deepcopy(v,memo) for v in self)
        return result

    def __setitem__(self, key, value):
        value=([tracked(v,self.changed) for v in value] if isinstance(key,slice)
               else tracked(value,self.changed))
        self.changed();list.__setitem__(self,key,value)

    def __delitem__(self,key):
        self.changed();list.__delitem__(self,key)

    def append(self,value):
        value=tracked(value,self.changed)
        self.changed();list.append(self,value)

    def extend(self,values):
        values=[tracked(v,self.changed) for v in values]
        self.changed();list.extend(self,values)

    def insert(self,index,value):
        value=tracked(value,self.changed)
        self.changed();list.insert(self,index,value)

    def pop(self,index=-1):
        self.changed();return list.pop(self,index)

    def remove(self,value):
        self.changed();list.remove(self,value)

    def clear(self):
        self.changed();list.clear(self)

    def reverse(self):
        self.changed();list.reverse(self)

    def sort(self,*args,**kwargs):
        self.changed();list.sort(self,*args,**kwargs)

    def __iadd__(self,values):
        self.extend(values);return self

    def __imul__(self,count):
        self.changed();list.__imul__(self,count);return self


class Ledger(TrackedDict):
    def __init__(self,value):
        self.revision=0
        self._json=None
        self._derived={}
        super().__init__(value,self.invalidate)

    def invalidate(self):
        self.revision+=1
        self._json=None
        self._derived.clear()

    def encoded(self):
        if self._json is None:
            self._json=json.dumps(self,ensure_ascii=False,separators=(',',':'))
        return self._json

    def summary(self,key,calculate):
        if key not in self._derived:
            if len(self._derived)>=64:self._derived.clear()
            self._derived[key]=calculate()
        return dict(self._derived[key])
