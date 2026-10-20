"""Local-only credential storage. Never expose values via HTTP or logs."""
import json
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = ROOT / 'state'
CREDENTIALS = STATE / 'credentials.json'
CONFIG_LOCK = threading.RLock()
MAX_PROFILES = 8


def profile_directory(profile_id='default'):
    if profile_id == 'default':
        return STATE
    if not isinstance(profile_id, str) or not re.fullmatch(r'p_[a-f0-9]{16}', profile_id):
        raise ValueError('账户配置编号无效。')
    return STATE / 'profiles' / profile_id


def credential_path(profile_id='default'):
    return CREDENTIALS if profile_id == 'default' else profile_directory(profile_id) / 'credentials.json'


def profile_ids():
    root = STATE / 'profiles'
    return ['default'] + sorted(p.name for p in root.glob('p_*')
        if re.fullmatch(r'p_[a-f0-9]{16}', p.name) and not p.is_symlink()
        and (p / 'credentials.json').is_file())


def prepare_state():
    STATE.mkdir(mode=0o700, exist_ok=True)
    os.chmod(STATE, 0o700)


def private_json(path, data):
    prepare_state()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    if STATE / 'profiles' in path.parents:
        os.chmod(STATE / 'profiles', 0o700)
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


def load_credentials(profile_id='default'):
    path = credential_path(profile_id)
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise ValueError('credential_file_permissions')
    return json.loads(path.read_text())


def save_credentials(payload):
    with CONFIG_LOCK:
        return _save_credentials(payload)


def _save_credentials(payload):
    profile_id = payload.get('profile_id', 'default')
    adding = profile_id == 'new'
    if adding:
        if len(profile_ids()) >= MAX_PROFILES:
            raise ValueError('最多配置 8 个独立登录账户。')
        profile_id = 'p_' + uuid.uuid4().hex[:16]
    elif profile_id not in profile_ids():
        raise ValueError('账户配置不存在，请选择新增账户。')
    old = load_credentials(profile_id) or {}
    payload = dict(payload)
    required = ('api_key', 'identifier', 'password')
    # Blank secrets on an existing profile mean keep the saved value.
    for key in required:
        if not payload.get(key):
            payload[key] = old.get(key)
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
    name = payload.get('name', old.get('name', '主账户' if profile_id == 'default' else 'IG 账户'))
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 40:
        raise ValueError('请填写 1–40 个字符的账户名称。')
    if any(ord(c) < 32 for c in name):
        raise ValueError('账户名称不能包含控制字符。')
    enabled = payload.get('enabled', old.get('enabled', True))
    if not isinstance(enabled, bool):
        raise ValueError('连接启用状态无效。')
    now = datetime.now(timezone.utc).isoformat()
    saved = {k: payload[k].strip() if k != 'password' else payload[k] for k in required}
    # A new owner/login must receive a new storage namespace.
    if old and (old['identifier'].casefold() != saved['identifier'].casefold()
                or old['environment'] != payload['environment']):
        raise ValueError('更换 IG 登录名或 Live/Demo 环境，请新增账户，保留原账户历史。')
    for other_id in profile_ids():
        if other_id == profile_id:
            continue
        other = load_credentials(other_id)
        if not other or other['environment'] != payload['environment']:
            continue
        if (other['identifier'].casefold() == saved['identifier'].casefold()
                or other['api_key'] == saved['api_key']):
            raise ValueError('这个 IG 登录名或 API Key 已有配置，请编辑现有账户，避免重复连接。')
    same_identity=(old.get('identifier')==saved['identifier'] and old.get('environment')==payload['environment'] and (not old.get('account_id') or not account_id or old.get('account_id')==account_id))
    saved.update(environment=payload['environment'], account_id=account_id,
                 history_since=since, saved_at=now,
                 risk_baseline_at=old.get('risk_baseline_at', now) if same_identity else now,
                 initial_capital=30000, name=name.strip(), enabled=enabled)
    private_json(credential_path(profile_id), saved)
    return profile_id


def credential_status(profile_id='default'):
    base = {'id': profile_id, 'name': '主账户' if profile_id == 'default' else 'IG 账户', 'enabled': True}
    if not credential_path(profile_id).exists():
        return {**base, 'configured': False}
    try:
        c = load_credentials(profile_id)
        return {**base, 'name': c.get('name', base['name']), 'enabled': c.get('enabled', True),
                'configured': True, 'environment': c['environment'],
                'account_selected': bool(c.get('account_id')), 'account_id': c.get('account_id', ''),
                'history_since': c['history_since'],
                'saved_at': c['saved_at']}
    except (ValueError, OSError, KeyError):
        return {**base, 'configured': False, 'error': '本机配置不可读或权限不正确。'}
