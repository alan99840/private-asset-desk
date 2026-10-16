"""Local-only credential storage. Never expose values via HTTP or logs."""
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = ROOT / 'state'
CREDENTIALS = STATE / 'credentials.json'


def prepare_state():
    STATE.mkdir(mode=0o700, exist_ok=True)
    os.chmod(STATE, 0o700)


def private_json(path, data):
    prepare_state()
    fd, temp = tempfile.mkstemp(prefix='.private-', dir=STATE)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def load_credentials():
    if not CREDENTIALS.exists():
        return None
    if CREDENTIALS.stat().st_mode & 0o077:
        raise ValueError('credential_file_permissions')
    return json.loads(CREDENTIALS.read_text())


def save_credentials(payload):
    required = ('api_key', 'identifier', 'password')
    if any(not isinstance(payload.get(k), str) or not payload[k].strip() for k in required):
        raise ValueError('请填写 API Key、IG 用户名和登录密码。')
    if any(len(payload[k]) > 500 for k in required):
        raise ValueError('输入长度异常。')
    if payload.get('environment') not in ('live', 'demo'):
        raise ValueError('请选择 Live 或 Demo。')
    since = payload.get('history_since', '')
    try:
        since_date = datetime.strptime(since, '%Y-%m-%d').date()
        if since_date > datetime.now(timezone.utc).date():
            raise ValueError()
    except ValueError:
        raise ValueError('历史起始日必须为有效日期，且不能晚于今天。')
    account_id = str(payload.get('account_id', '')).strip()
    if account_id and (len(account_id) > 30 or not account_id.isalnum()):
        raise ValueError('Account ID 应为 IG 账户编号，不是邮箱。可以先留空。')
    old = load_credentials() or {}
    now = datetime.now(timezone.utc).isoformat()
    saved = {k: payload[k].strip() if k != 'password' else payload[k] for k in required}
    same_identity=(old.get('identifier')==saved['identifier'] and old.get('environment')==payload['environment'] and (not old.get('account_id') or not account_id or old.get('account_id')==account_id))
    saved.update(environment=payload['environment'], account_id=account_id,
                 history_since=since, saved_at=now,
                 risk_baseline_at=old.get('risk_baseline_at', now) if same_identity else now,
                 initial_capital=30000)
    private_json(CREDENTIALS, saved)


def credential_status():
    if not CREDENTIALS.exists():
        return {'configured': False}
    try:
        c = load_credentials()
        return {'configured': True, 'environment': c['environment'],
                'account_selected': bool(c.get('account_id')), 'history_since': c['history_since'],
                'saved_at': c['saved_at']}
    except (ValueError, OSError, KeyError):
        return {'configured': False, 'error': '本机配置不可读或权限不正确。'}
