"""Model-free Futu MCP reader; credentials remain managed by Codex."""
import json
import queue
import re
import subprocess
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ALLOWED = set(json.loads((ROOT / 'rules.json').read_text())['allowed_mcp_tools'])
EXE = Path('/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex')


def redact(value):
    text = str(value)
    text = re.sub(r'(?i)Bearer\s+[^\s,;"\'<>]+', 'Bearer [REDACTED]', text)
    text = re.sub(r'(?i)(access_token|refresh_token|client_secret|authorization|api_key|password)(["\']?\s*[:=]\s*["\']?)[^\s,;"\'<>}]+', r'\1\2[REDACTED]', text)
    return text[:350]


def payload(result):
    for c in result.get('content', []):
        if c.get('type') == 'text':
            try:
                return json.loads(c['text'])
            except ValueError:
                pass
    return result


class QuoteClient:
    def __init__(self):
        self.seq = 0
        self.messages = queue.Queue()
        self.last_call = 0
        self.proc = subprocess.Popen([str(EXE), 'app-server'], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, bufsize=1)
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._discard_stderr, daemon=True).start()
        try:
            self.request('initialize', {'clientInfo': {'name': 'v23-quote-only-advisor', 'version': '2.3'},
                                        'capabilities': {'experimentalApi': True}}, timeout=15)
            self.send({'jsonrpc': '2.0', 'method': 'initialized'})
            ctx = self.request('thread/start', {'ephemeral': True, 'cwd': str(ROOT.parent),
                                               'approvalPolicy': 'never'}, timeout=50)
            self.context = ctx['thread']['id']
            status = self.request('mcpServerStatus/list', {'serverName': 'futu-mcp',
                                  'threadId': self.context, 'detail': 'toolsAndAuthOnly'}, timeout=50)
            futu = next(s for s in status.get('data', []) if s.get('name') == 'futu-mcp')
            if futu.get('authStatus') != 'oAuth':
                raise RuntimeError('Futu OAuth needs user attention in official UI')
            self.auth_status = futu.get('authStatus')
            self.runtime_status = futu.get('runtimeStatus')
        except Exception:
            self.close()
            raise

    def _read(self):
        try:
            for line in self.proc.stdout:
                if len(line) > 20_000_000:
                    self.messages.put({'local_error': 'RPC response exceeds size bound'})
                    break
                self.messages.put(json.loads(line))
        except Exception as e:
            self.messages.put({'local_error': redact(e)})
        finally:
            self.messages.put({'local_error': 'RPC reader closed'})

    def _discard_stderr(self):
        for _ in self.proc.stderr:
            pass

    def send(self, item):
        self.proc.stdin.write(json.dumps(item, separators=(',', ':')) + '\n')
        self.proc.stdin.flush()

    def request(self, method, params, timeout=65):
        self.seq += 1
        request_id = self.seq
        self.send({'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params})
        until = time.monotonic() + timeout
        while True:
            message = self.messages.get(timeout=max(0.01, until - time.monotonic()))
            if message.get('local_error'):
                raise RuntimeError(message['local_error'])
            if 'method' in message and 'id' in message:
                self.send({'jsonrpc': '2.0', 'id': message['id'],
                           'error': {'code': -32601, 'message': 'Read-only client declines interactive requests'}})
                raise RuntimeError('Finish requested interaction in official UI')
            if message.get('id') != request_id:
                continue
            if message.get('error'):
                raise RuntimeError(redact(message['error']))
            return message.get('result', {})

    def call(self, name, arguments):
        if name not in ALLOWED:
            raise ValueError('Tool is outside quote-only allowlist')
        for attempt in range(3):
            time.sleep(max(0, 1.1 - (time.monotonic() - self.last_call)))
            self.last_call = time.monotonic()
            raw = self.request('mcpServer/tool/call', {'server': 'futu-mcp', 'threadId': self.context,
                               'tool': name, 'arguments': arguments})
            parsed = payload(raw)
            if parsed.get('ret_code') == -11:
                time.sleep(8 + attempt * 2)
                continue
            return raw, parsed
        return raw, parsed

    def close(self):
        proc = getattr(self, 'proc', None)
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
