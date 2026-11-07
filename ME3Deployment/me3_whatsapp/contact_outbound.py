"""Command-bound named messages. No background contact discovery or auto-send."""
import asyncio, hashlib, json, re, secrets, time, unicodedata, sqlite3, stat
from pathlib import Path
from contextlib import closing
from dataclasses import asdict
from .guard import Rejected
from .recipients import Command, Directory, norm, review, command_text
from .service import Store
from .action import fingerprint
from me3_assistant.intent import request_binding, message_text

def qualification_target(root, destination):
    policy = json.loads((root / 'config/operating_policy.json').read_bytes())
    if policy.get('routine_whatsapp_contact_messages') is not True or policy.get('whatsapp_recipient_scope') != 'verified_contacts':
        raise Rejected('named_messages_not_enabled')
    allowed = policy.get('whatsapp_qualification_outbound_only')
    if not isinstance(allowed, list) or not any((t == {'name': destination['label'], 'phone': destination['phone']} for t in allowed)):
        raise Rejected('recipient_outside_qualification_scope')

def pre_send_rejection(root, op):
    root = Path(root).resolve()
    path = root / 'data/whatsapp-contact-message-operations.sqlite3'
    if path.is_symlink() or path.resolve().parent != root / 'data':
        raise Rejected('untrusted_contact_rejection_store')
    s = path.stat()
    if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1:
        raise Rejected('untrusted_contact_rejection_store')
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=3)) as c:
        c.row_factory = sqlite3.Row
        c.execute('BEGIN')
        found = c.execute('SELECT * FROM messages WHERE operation_id=?', (op,)).fetchone()
        events = [dict(r) for r in c.execute('SELECT sequence,state,at FROM events WHERE operation_id=? ORDER BY sequence', (op,))]
    if found is None:
        return None
    row = dict(found)
    for k in ['intent', 'binding', 'observation']:
        row[k] = json.loads(row[k]) if row[k] else None
    b = row['binding'] or {}
    f = b.get('failure') or {}
    if row['state'] != 'UNCERTAIN' or fingerprint(row['intent']) != row['expected_digest'] or (not re.fullmatch('[0-9a-f]{32}', row['operation_id'])) or (not re.fullmatch('[0-9a-f]{32}', row['owner'])) or (not re.fullmatch('[0-9a-f]{32}', row['token'])) or (row.get('guard_id') is not None) or (row['observation'] not in (None, {})) or (f not in ({'phase': 'prepare_exact_editor', 'reason': 'one_empty_contact_editor_required_existing_draft_preserved', 'send_click_may_have_started': False}, {'phase': 'prepare_exact_editor', 'reason': 'exact_contact_draft_not_verified', 'send_click_may_have_started': False})) or (type(f.get('send_click_may_have_started')) is not bool) or ([e['state'] for e in events] != ['PREPARED', 'DISPATCHED', 'UNCERTAIN']):
        return None
    try:
        command = Command(**b['command'])
        command.validate()
    except (KeyError, TypeError, Rejected):
        return None
    if command.owner != row['owner'] or b.get('request_binding') != request_binding(command.owner, command.goal, command.generation, command.request, source=command.source):
        return None
    recipient = (b.get('recipient') or {}).get('destination') or {}
    if any((recipient.get(k) != row['intent']['recipient'].get(k) for k in ['label', 'phone'])):
        return None
    evidence = dict(operation_id=op, owner=row['owner'], intent=row['intent'], expected_digest=row['expected_digest'], state=row['state'], binding=b, observation=row['observation'], events=events, token_sha256=hashlib.sha256(row['token'].encode()).hexdigest(), expires=row['expires'], guard_id=row['guard_id'])
    return dict(kind='known_pre_send_rejection', operation_id=op, expected_digest=row['expected_digest'], backend_state='UNCERTAIN', failure=f, evidence_sha256=fingerprint(evidence))

def validate_pre_send_rejection(root, proof, op, expected_digest):
    if type(proof) is not dict or proof.get('operation_id') != op or proof.get('expected_digest') != expected_digest:
        raise Rejected('exact_pre_send_rejection_required')
    actual = pre_send_rejection(root, op)
    if actual is None or actual != proof:
        raise Rejected('durable_pre_send_rejection_not_verified')
    return True

class NamedStore(Store):

    @staticmethod
    def retirement_key(intent):
        phone = intent['recipient']['phone']
        text = re.sub('\\s+', ' ', unicodedata.normalize('NFKC', intent['text'])).strip().casefold()
        if not re.fullmatch('\\+[1-9][0-9]{6,14}', phone) or not text:
            raise Rejected('exact_retired_message_identity_required')
        return hashlib.sha256(('retired_named_message_v1\x00' + json.dumps([phone, text], ensure_ascii=False, separators=(',', ':'))).encode()).hexdigest()

    def retired(self, intent):
        with self.db() as c:
            return c.execute('SELECT 1 FROM protected WHERE text_sha256=?', (self.retirement_key(intent),)).fetchone() is not None

    def prepare_named(self, op, intent, command, resolved):
        if not re.fullmatch('[0-9a-f]{32}', op):
            raise Rejected('host_operation_identity_required')
        if intent['text'] == 'ME3 WB-06 check.':
            raise Rejected('consumed_self_test_must_not_be_resent')
        if self.retired(intent):
            raise Rejected('retired_unknown_message_must_not_be_repeated')
        binding = dict(command=asdict(command), recipient=resolved, request_binding=request_binding(command.owner, command.goal, command.generation, command.request, source=command.source))
        with self.db() as c:
            if c.execute('SELECT 1 FROM messages WHERE operation_id=?', (op,)).fetchone():
                raise Rejected('existing_message_intent_not_replayed')
            c.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?)', (op, secrets.token_hex(16), json.dumps(intent), fingerprint(intent), 'PREPARED', time.time() + 120, command.owner, json.dumps(binding), None, None))
            c.execute('INSERT INTO events(operation_id,state,at) VALUES (?,?,?)', (op, 'PREPARED', time.time()))
        row = self.row(op)
        return dict(operation_id=op, intent=intent, expected_digest=row['expected_digest'], token=row['token'], request_binding=row['binding']['request_binding'])

class ContactOutbound:

    def __init__(self, service):
        self.service = service
        self.root = service.root
        self.backend = service.backend

    def store(self):
        return NamedStore(self.root / 'data/whatsapp-contact-message-operations.sqlite3')

    def current(self, command):
        return self.service.active is True

    async def prepare(self, op, selection_id, text, owner, goal, request, generation):
        message_text(text)
        command = Command(owner, goal, generation, request)
        command.validate()
        resolved = Directory(self.root / 'data/whatsapp-directory.sqlite3').resolve_draft(command, selection_id, self.current)
        d = resolved['destination']
        if not isinstance(d.get('phone'), str) or not re.fullmatch('\\+[1-9][0-9]{6,14}', d['phone']):
            raise Rejected('visibly_verified_recipient_phone_required')
        qualification_target(self.root, d)
        intent = dict(recipient=dict(selection_id=selection_id, label=d['label'], phone=d['phone']), text=text)
        return self.store().prepare_named(op, intent, command, resolved)

    async def commit(self, op, token, confirmation):
        if confirmation != 'confirm':
            raise Rejected('direct_host_confirmation_required')
        store = self.store()
        row = store.row(op)
        if not secrets.compare_digest(row['token'], token):
            raise Rejected('private_host_token_required')
        if row['state'] != 'PREPARED':
            return dict(state=row['state'], write_retried=False)
        if store.retired(row['intent']):
            raise Rejected('retired_unknown_message_must_not_be_repeated')
        command = Command(**row['binding']['command'])
        message_text(row['intent']['text'])
        expected = request_binding(command.owner, command.goal, command.generation, command.request, source=command.source)
        if row['binding'].get('request_binding') != expected:
            raise Rejected('draft_request_binding_changed')
        if fingerprint(row['intent']) != row['expected_digest']:
            raise Rejected('reviewed_message_intent_changed')
        resolved = Directory(self.root / 'data/whatsapp-directory.sqlite3').resolve_draft(command, row['intent']['recipient']['selection_id'], self.current)
        if resolved != row['binding']['recipient']:
            raise Rejected('prepared_contact_identity_changed')
        qualification_target(self.root, resolved['destination'])
        if not store.claim(op, token):
            return dict(state=store.row(op)['state'], write_retried=False)
        from .contact_message_browser import ContactMessageBrowser
        browser = ContactMessageBrowser(self.backend, resolved, row['owner'], row['intent']['text'])
        phase = 'connect_existing_app'
        binding = row['binding']
        try:
            async with self.backend.transaction(False):
                phase = 'prepare_exact_editor'
                baseline = await browser.compose()
                binding = {**row['binding'], **baseline}
                store.transition(op, 'DISPATCHED', binding=binding)
                phase = 'snapshot_exact_editor'
                snapshot = await browser.snapshot()
                if snapshot.get('text') != row['intent']['text'] or snapshot.get('valid') is not True:
                    raise Rejected('exact_verified_editor_required')
                if not self.current(command):
                    raise asyncio.CancelledError()
                phase = 'send_click'
                if await browser.click_send(snapshot) is not True:
                    raise Rejected('send_click_not_verified')
                phase = 'observe_outgoing'
                observation = await browser.observe(baseline['before_ids'])
                if not observation.get('exact_message') or observation.get('status') not in {'submitted', 'sent', 'delivered', 'read'}:
                    store.transition(op, 'UNCERTAIN', observation=observation)
                    return dict(state='UNCERTAIN', write_retried=False)
                state = 'SUBMITTED' if observation['status'] == 'submitted' else 'VERIFIED'
                binding = {**binding, 'post_send_return': dict(state='pending', target_id=resolved['target_id'], message_id=observation.get('message_id'), expires=time.time() + 15)}
                store.transition(op, state, observation=observation, binding=binding)
                return dict(state=state, write_retried=False)
        except BaseException as exc:
            reason = str(exc) if isinstance(exc, Rejected) and re.fullmatch('[a-z][a-z0-9_]{0,95}', str(exc)) else type(exc).__name__
            store.transition(op, 'UNCERTAIN', binding={**binding, 'failure': dict(phase=phase, reason=reason, send_click_may_have_started=phase in {'send_click', 'observe_outgoing'})})
            raise

    async def read(self, op):
        store = self.store()
        row = store.row(op)
        previous = row['observation'] or {}
        binding = row['binding'] or {}
        recipient = binding.get('recipient')
        rejection = pre_send_rejection(self.root, op) if row['state'] == 'UNCERTAIN' else None
        if rejection is not None:
            return dict(row=self.public(row), observation={}, verification_source='durable_owned_contact_pre_send_rejection', pre_send_rejection=rejection)
        if row['state'] not in {'SUBMITTED', 'VERIFIED', 'UNCERTAIN'} or not recipient or (not previous.get('message_id')):
            return dict(row=self.public(row), observation={}, verification_source='no_trustworthy_owned_message_binding')
        from .contact_message_browser import ContactMessageBrowser
        browser = ContactMessageBrowser(self.backend, recipient, row['owner'], row['intent']['text'])
        async with self.backend.transaction(False):
            observation = await browser.find(message_id=previous['message_id'])
            cleanup = await self._return_after_verified_send(store, row, browser, observation)
        public = dict(exact_outgoing_message=observation.get('exact_message') is True, recipient=row['intent']['recipient'], text_sha256=hashlib.sha256(row['intent']['text'].encode()).hexdigest(), status=observation.get('status'))
        return dict(row=self.public(row), observation=public, verification_source='fresh_scoped_whatsapp_ui', window_return=cleanup)

    @staticmethod
    def public(row):
        return {k: row[k] for k in ['operation_id', 'intent', 'expected_digest', 'state']}

    async def _return_after_verified_send(self, store, row, browser, observation):
        if row.get('state') not in {'SUBMITTED', 'VERIFIED'}:
            return None
        marker = (row.get('binding') or {}).get('post_send_return')
        previous = row.get('observation') or {}
        if not isinstance(marker, dict) or marker.get('state') not in {'pending', 'first_read_verified'}:
            return None
        keys = {'state', 'target_id', 'message_id', 'expires'} | ({'first_read_sha256'} if marker['state'] == 'first_read_verified' else set())
        if set(marker) != keys or type(marker.get('expires')) not in (int, float) or (not time.time() < marker['expires'] <= time.time() + 15) or (marker['target_id'] != row['binding']['recipient'].get('target_id')) or (not isinstance(marker['message_id'], str)) or (not marker['message_id']) or (len(marker['message_id']) > 1024) or (previous.get('message_id') != marker['message_id']) or (observation.get('message_id') != marker['message_id']) or (observation.get('exact_message') is not True) or (observation.get('status') not in {'submitted', 'sent', 'delivered', 'read'}):
            return None
        identity = fingerprint(dict(operation_id=row['operation_id'], expected_digest=row['expected_digest'], target_id=marker['target_id'], message_id=marker['message_id']))
        outcome = dict(status='unverified', target_closed=False, conversation_stop_requested=False, automatic_retry=False)
        try:
            if marker['state'] == 'pending':
                binding = {**row['binding'], 'post_send_return': {**marker, 'state': 'first_read_verified', 'first_read_sha256': identity}}
                store.transition(row['operation_id'], row['state'], binding=binding)
                return dict(status='awaiting_final_readback', target_closed=False, conversation_stop_requested=False, automatic_retry=False)
            if marker['first_read_sha256'] != identity:
                return None
            binding = {**row['binding'], 'post_send_return': {**marker, 'state': 'attempted'}}
            store.transition(row['operation_id'], row['state'], binding=binding)
            from .window import WindowBackend
            async with asyncio.timeout(2):
                outcome = await WindowBackend(self.backend).return_after_verified_send(marker['target_id'], browser.verified)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        return outcome
