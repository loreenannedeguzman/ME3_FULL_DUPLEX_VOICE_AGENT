"""Close exactly one owned WhatsApp target; never browser/process/ME3 shutdown."""
import asyncio, hashlib, json, os, re, secrets, sqlite3, time
from contextlib import contextmanager, closing
from pathlib import Path
import aiohttp
from .guard import Rejected
from .ui_controls import CloseGuard, CloseApproval, close_binding, approval_digest

def command_allowed(text):
    if not isinstance(text, str) or len(text) > 4000:
        return False
    value = ' '.join(text.casefold().replace('’', "'").split())
    # A final direct imperative may follow acknowledgments. Quoted/reported,
    # conditional, negated and embedded instructions never authorize cleanup.
    if re.search(r'["“”`<>\[\]{}\n\r:;]|(?<!\w)\'|\'(?!\w)', value):
        return False
    if re.search(r"\b(?:not|never|without|instead|unless|if|when|until|before|after|example|quote|quoted|said|says|told|asked|asks|instructed|instructions|source|history|message|email|document|pretend)\b|\bdon'?t\b", value):
        return False
    sentences = [part.strip() for part in re.split(r'(?<=[.!?])\s+', value)]
    if not sentences or len(sentences) > 4:
        return False
    prefix, value = sentences[:-1], sentences[-1]
    # Earlier imperative/action clauses and coordinated requests are outside
    # this narrow cleanup admission. Onit still selects the exact window tool.
    for part in prefix:
        if not part.endswith('.') or re.search(r"\b(?:please|can you|could you|would you|will you|must|should|want|need|let|then|and|or|also)\b", part):
            return False
        if re.search(r"\b(?:close|hide|open|send|write|delete|remove|call|dial|run|start|stop|click|type|copy|paste|forward|reply|schedule|create|change|use|read|show|search|find|look|tell|ask|make|do)\b", part):
            return False
    value = re.sub('^(?:(?:please|can you|could you|would you|will you)\\s+)+', '', value)
    return bool(re.fullmatch("(?:close|hide) (?:the )?(?:whatsapp|what\\W?s\\s?app|what[\\' ]?s up)(?: (?:app|application|window))?(?: please)?[.!?]?", value))
WINDOW_SNAPSHOT = '(()=>{const visible=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(e).visibility!==\'hidden\'};const official=location.origin===\'https://web.whatsapp.com\';const dialogs=Array.from(document.querySelectorAll(\'[role="dialog"],[aria-modal="true"]\')).filter(visible);const controls=Array.from(document.querySelectorAll(\'button,[role="button"]\')).filter(visible);const active=controls.some(e=>/^(?:end call|hang up|return to call|mute microphone|turn off camera|leave call|call in progress)$/i.test(e.getAttribute(\'aria-label\')||\'\'))||Array.from(document.querySelectorAll(\'[data-testid]\')).filter(visible).some(e=>/^(?:call-screen|call-modal|call-in-progress|call-banner|call-controls|active-call)$/i.test(e.getAttribute(\'data-testid\')||\'\'));const editors=Array.from(document.querySelectorAll(\'#main footer [contenteditable="true"]\')).filter(visible);return {official,linked:!!document.querySelector(\'#pane-side\'),draft_empty:editors.length<=1&&editors.every(e=>e.innerText.trim()===\'\'),active_call:active,unknown_dialog:dialogs.length!==0,contents_read:false}})()'

class WindowBackend:

    def __init__(self, backend):
        self.backend = backend

    def current(self):
        self.backend.check()

    async def snapshot(self):
        b = self.backend
        self.current()
        info = (await b.page.call('Target.getTargetInfo'))['targetInfo']
        target = info.get('targetId')
        if info.get('type') != 'page' or info.get('url', '').split('#', 1)[0].split('?', 1)[0].rstrip('/') != 'https://web.whatsapp.com' or (not target):
            raise Rejected('owned_official_target_required')
        port = int((b.profile() / 'DevToolsActivePort').read_text().splitlines()[0])
        tabs = await self.targets(port)
        pages = [t for t in tabs if t.get('type') == 'page']
        if len(pages) != 1 or pages[0].get('id') != target:
            raise Rejected('single_owned_app_window_required')
        s = await b.evaluate(WINDOW_SNAPSHOT)
        s.update(target_id=target, owned_profile=True, port=port)
        return s

    async def targets(self, port):
        if type(port) is not int or not 1024 <= port <= 65535:
            raise Rejected('owned_debug_port_required')
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2), trust_env=False) as session:
            async with session.get('http://127.0.0.1:' + str(port) + '/json/list') as r:
                if r.status != 200:
                    raise Rejected('owned_target_list_unavailable')
                raw = await r.content.read(1048577)
                if len(raw) > 1048576:
                    raise Rejected('bounded_target_response_required')
                value = json.loads(raw)
                if not isinstance(value, list) or any((not isinstance(t, dict) for t in value)):
                    raise Rejected('target_list_required')
                pages = [t for t in value if t.get('type') == 'page']
                if len(pages) > 8:
                    raise Rejected('bounded_page_targets_required')
                return pages

    async def closed(self, target, port):
        deadline = time.monotonic() + 4
        while True:
            self.current()
            if not self.backend.browser_running(self.backend.profile()):
                return True
            try:
                tabs = await self.targets(port)
                if all((t.get('id') != target for t in tabs)):
                    return True
            except (OSError, aiohttp.ClientError, TimeoutError):
                pass
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.1)

    async def return_after_verified_send(self, target_id, verify_contact):
        self.current()
        await verify_contact()
        first = await self.snapshot()

        def safe(s):
            if s.get('target_id') != target_id or s.get('official') is not True or s.get('owned_profile') is not True or (s.get('linked') is not True) or (s.get('draft_empty') is not True) or (s.get('active_call') is not False) or (s.get('unknown_dialog') is not False):
                raise Rejected('verified_send_window_cleanup_refused')
        safe(first)
        await verify_contact()
        second = await self.snapshot()
        safe(second)
        if second['port'] != first['port']:
            raise Rejected('verified_send_window_identity_changed')
        self.current()
        try:
            await self.backend.page.call('Page.close')
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        self.current()
        closed = await self.closed(target_id, second['port'])
        return dict(status='closed' if closed is True else 'unverified', target_closed=closed is True, conversation_stop_requested=False, automatic_retry=False)

class WindowStore:

    def __init__(self, path):
        self.path = Path(path)
        if self.path.is_symlink():
            raise Rejected('untrusted_window_store')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 384)
        os.close(fd)
        if os.name != 'nt':
            os.chmod(self.path, 384)
        with self.db() as c:
            c.execute('CREATE TABLE IF NOT EXISTS windows (id TEXT PRIMARY KEY, token TEXT, owner TEXT, generation INTEGER, target TEXT, port INTEGER, digest TEXT, state TEXT, expires REAL)')
            c.execute('CREATE TABLE IF NOT EXISTS events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT, state TEXT, at REAL)')

    @contextmanager
    def db(self):
        with closing(sqlite3.connect(self.path, timeout=3)) as c:
            c.row_factory = sqlite3.Row
            with c:
                yield c

    def row(self, op):
        with self.db() as c:
            r = c.execute('SELECT * FROM windows WHERE id=?', (op,)).fetchone()
        if r is None:
            raise Rejected('unknown_window_operation')
        return dict(r)

    def transition(self, op, state):
        with self.db() as c:
            c.execute('UPDATE windows SET state=? WHERE id=?', (state, op))
            c.execute('INSERT INTO events(operation_id,state,at) VALUES (?,?,?)', (op, state, time.time()))

class WindowService:

    def __init__(self, service, store):
        self.service = service
        self.store = store
        self.backend = WindowBackend(service.backend)

    async def prepare(self, op, g, request):
        if not command_allowed(request) or not re.fullmatch('[0-9a-f]{32}', op or '') or type(g) is not int or (g < 1):
            raise Rejected('explicit_current_window_command_required')
        with self.store.db() as c:
            if c.execute('SELECT 1 FROM windows WHERE id=?', (op,)).fetchone():
                raise Rejected('window_operation_consumed')
            if c.execute("SELECT 1 FROM windows WHERE state IN ('DISPATCHED','UNCERTAIN') LIMIT 1").fetchone():
                raise Rejected('previous_window_outcome_requires_readback')
        owner = secrets.token_hex(16)
        async with self.service.backend.transaction(False):
            s = await self.backend.snapshot()
        approval = CloseApproval(op, s['target_id'], owner, g, True)
        expected = close_binding(s, approval)
        token = secrets.token_hex(16)
        with self.store.db() as c:
            c.execute('INSERT INTO windows VALUES (?,?,?,?,?,?,?,?,?)', (op, token, owner, g, s['target_id'], s['port'], expected, 'PREPARED', time.time() + 120))
            c.execute('INSERT INTO events(operation_id,state,at) VALUES (?,?,?)', (op, 'PREPARED', time.time()))
        return dict(operation_id=op, token=token, expected_digest=expected, target_id=s['target_id'], will_stop_conversation=False)

    async def commit(self, op, token, g):
        row = self.store.row(op)
        if not secrets.compare_digest(row['token'], token) or row['generation'] != g:
            raise Rejected('host_window_identity_required')
        if row['state'] != 'PREPARED':
            return dict(state=row['state'], write_retried=False)
        if time.time() >= row['expires']:
            raise Rejected('window_proposal_expired')
        self.store.transition(op, 'DISPATCHED')
        try:
            b = self.service.backend
            async with b.transaction(False):
                a = CloseApproval(op, row['target'], row['owner'], g, True)
                if close_binding(await self.backend.snapshot(), a) != row['digest']:
                    raise Rejected('window_target_changed')
                guard = CloseGuard(self.service.root / 'data/whatsapp-window-actions.jsonl', b.page, self.backend.snapshot, lambda target: self.backend.closed(target, row['port']), lambda owner, generation: self.service.active and owner == row['owner'] and (generation == g))
                result = await guard.close(a)
            self.store.transition(op, result['state'])
            return result
        except BaseException:
            self.store.transition(op, 'UNCERTAIN')
            raise

    async def read(self, op):
        row = self.store.row(op)
        closed = False
        if row['state'] not in {'PREPARED', 'CANCELED'}:
            closed = await self.backend.closed(row['target'], row['port'])
            if closed and row['state'] != 'VERIFIED':
                self.store.transition(op, 'VERIFIED')
                row = self.store.row(op)
        return dict(operation_id=op, expected_digest=row['digest'], state=row['state'], target_closed=closed, verification_source='fresh_owned_target_list_or_owned_process_exit', conversation_stop_requested=False, write_retried=False)
