"""Synthetic Windows-only DPAPI probe; no user files, bot or license input."""
import base64
import json
import subprocess
import sys
from tools.protected_format import encode, decode, ProtectedFormatError

PREFIX = 'NRNF-DipBot-Commercial-USDT-v1|'
PS = r'''
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Security
$r = [Console]::In.ReadToEnd() | ConvertFrom-Json
$data = [Convert]::FromBase64String($r.data)
$entropy = [Convert]::FromBase64String($r.entropy)
$scope = [System.Security.Cryptography.DataProtectionScope]::CurrentUser
if ($r.action -eq 'protect') {
 $result = [System.Security.Cryptography.ProtectedData]::Protect($data, $entropy, $scope)
} elseif ($r.action -eq 'unprotect') {
 $result = [System.Security.Cryptography.ProtectedData]::Unprotect($data, $entropy, $scope)
} else { throw 'Unknown operation' }
[Console]::Out.Write([Convert]::ToBase64String($result))
'''


def crypt(data, purpose, action):
    if sys.platform != 'win32':
        raise RuntimeError('Native DPAPI probe requires Windows')
    request = {'action':action, 'data':base64.b64encode(data).decode('ascii'),
               'entropy':base64.b64encode((PREFIX+purpose).encode('utf-8')).decode('ascii')}
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', PS],
                            input=json.dumps(request), text=True, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Windows DPAPI operation failed')
    return base64.b64decode(result.stdout.strip(), validate=True)


def probe():
    if sys.platform != 'win32':
        raise RuntimeError('Native DPAPI probe requires Windows')
    protect = lambda data, purpose: crypt(data, purpose, 'protect')
    unprotect = lambda data, purpose: crypt(data, purpose, 'unprotect')
    payload = {'kind':'runtime-settings', 'version':1, 'synthetic':True, 'text':'тест'}
    raw = encode(payload, 'runtime-settings', protect)
    if decode(raw, 'runtime-settings', unprotect) != payload:
        raise AssertionError('DPAPI round trip mismatch')
    envelope = json.loads(raw)
    blob = bytearray(base64.b64decode(envelope['payload'])); blob[len(blob)//2] ^= 1
    envelope['payload'] = base64.b64encode(blob).decode('ascii')
    cases = [(raw, 'different-synthetic-purpose'),
             (json.dumps(envelope).encode('ascii'), 'runtime-settings')]
    for candidate, purpose in cases:
        try:
            decode(candidate, purpose, unprotect)
        except ProtectedFormatError:
            continue
        raise AssertionError('Invalid DPAPI data was accepted')
    return {'native_dpapi':'passed', 'scope':'CurrentUser', 'synthetic_only':True,
            'checks':['round_trip', 'wrong_entropy_rejected', 'tamper_rejected'],
            'original_bot_executed':False, 'original_file_compatibility':'not_tested'}


if __name__ == '__main__':
    if sys.platform != 'win32':
        raise SystemExit('NOT RUN: requires Windows; no DPAPI compatibility claim')
    print(json.dumps(probe(), indent=2))
