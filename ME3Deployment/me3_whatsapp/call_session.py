"""Host-owned native-browser call handoff; no browser/model/device operations.

Install only in the normal GUI owner. A fresh exact review and separate user
confirmation are checked by the supplied host authorize callback, not inferred
here. Queuing a handoff is not dialing or verification. The independent GUI
task waits for the confirming turn, closes ME3's voice owner completely, checks
the actual stream/worker objects, then calls the supplied guarded lifecycle.
Remote call audio cannot be command input because ME3 capture is closed.
"""
import asyncio
import copy
import inspect
import re
import threading
import time
from dataclasses import dataclass


class CallSessionError(ValueError):
    pass


class _FencedInput(BaseException):
    """Internal unwind of a queued processor frame, never a pipeline fault."""


@dataclass(frozen=True)
class _GuiEndRequest:
    operation_id: str
    seal: object
    action: str = 'end_call'


def _ident(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{32}', value)


class HostCallSession:
    """One GUI-lifetime owner, independent of the closed voice generation.

    configure() accepts trusted host functions only. They are never MCP tools:
      authorize(p, generation) -> bound private confirmation proof, synchronously
      commit(proof) -> actual guarded lifecycle result, asynchronously
      hangup(proof, sealed_gui_request) -> guarded lifecycle result, async
      verify_terminal(result, proof) -> True only for exact independently ENDED
          or durable proven NO_DIAL_ABORTED; may be asynchronous
    No supplied dictionary can authenticate a GUI request by itself.
    """
    HELD_STATES = {'HANDOFF_AUTHORIZED', 'CLOSING_VOICE', 'VOICE_CLOSED',
                   'DIALING', 'RINGING', 'CONNECTED', 'HELD_UNCERTAIN'}

    def __init__(self, console, *, clock=time.monotonic):
        self.console = console
        self.clock = clock
        self.lock = threading.Lock()
        self.epoch = 0
        self.held = False
        self.state = 'IDLE'
        self.proof = None
        self.task = None
        self.resources = None
        self.callbacks = None
        self._gui_seal = object()
        self._hangup_claimed = False
        self._confirmed_settled = False
        self._voice_closed = False
        self.failure = None
        self._confirming_task = None
        self._consumed_ops = set()
        self._recovered = False
        self._end_inflight = False
        self._check_inflight = False

    def configure(self, *, authorize, commit, hangup, verify_terminal, abort_before_dispatch=None, reconcile=None):
        if (self.held or self.task is not None and not self.task.done()
                or self.state not in {'IDLE','ENDED','LOCAL_ENDED','NO_DIAL_ABORTED'}):
            raise CallSessionError('active_call_callbacks_binding_immutable')
        if not all(callable(f) for f in (authorize, commit, hangup, verify_terminal)):
            raise CallSessionError('trusted_call_callbacks_required')
        self.callbacks = dict(authorize=authorize, commit=commit, hangup=hangup,
                              verify_terminal=verify_terminal,
                              abort_before_dispatch=abort_before_dispatch,
                              reconcile=reconcile)

    def ticket(self):
        with self.lock:
            return None if self.held else self.epoch

    def accepts(self, ticket):
        with self.lock:
            return type(ticket) is int and ticket == self.epoch and not self.held

    def admit(self, action):
        # Hangup has a distinct authenticated endpoint. Ordinary Stop cannot
        # pretend that a remote call ended, and Clear cannot erase its owner.
        if self.held and action in {'send', 'start_voice', 'start_text', 'clear',
                                    'stop', 'cancel'}:
            raise CallSessionError('call_owns_audio_use_End_call')

    def snapshot(self):
        with self.lock:
            p = self.proof or {}
            return dict(state=self.state, held=self.held,
                        operation_id=p.get('operation_id'),
                        microphone_closed=self._voice_closed,
                        end_requested=self._hangup_claimed,
                        can_end=bool(self.held and self._voice_closed and
                                     self.state in {'DIALING', 'RINGING', 'CONNECTED', 'HELD_UNCERTAIN'}
                                     and not self._hangup_claimed),
                        can_check=bool(self.held and self._voice_closed
                                       and self._hangup_claimed and not self._end_inflight
                                       and not self._check_inflight and self.callbacks
                                       and callable(self.callbacks.get('reconcile'))),
                        ordinary_input_enabled=not self.held,
                        automatic_retry=False)

    def _emit(self, event, **fields):
        observe = getattr(self.console, 'observe', None)
        if callable(observe):
            observe(event, **fields)

    def bind_voice_resources(self, result, *, echo, music_bus, stream_binding):
        """Called before streams open, after all ordinary music mix wrappers.

        Install this callback BEFORE acoustic_echo.attach: final order becomes
        ordinary voice/music -> held suppression -> final AEC reference. Calls
        use native browser audio only AFTER this entire voice owner is closed;
        there is deliberately no remote-PCM injection or virtual-mic lane.
        """
        if self.held:
            raise CallSessionError('call_blocks_new_voice_owner')
        _, _, transport, processor = result
        self.resources = dict(transport=transport, processor=processor, echo=echo,
                              music_bus=music_bus, stream_binding=stream_binding)
        processor.task_ack_call_held=lambda:self.held # same final-mix privacy owner, no call authority
        original_output = transport.output().callback

        def quiet_output(*args):
            pcm, flag = original_output(*args)
            return (bytes(len(pcm)) if self.held else pcm), flag

        transport.output().callback = quiet_output
        original_frame = processor.process_frame
        original_takeover = processor.takeover
        original_vad = processor.vad.analyze_audio

        async def process_frame(frame, direction):
            # The current pipeline carries no ASR through any other frame type.
            if type(frame).__name__ != 'InputAudioRawFrame':
                return await original_frame(frame, direction)
            ticket = self.ticket()
            if ticket is None:
                return
            try:
                await original_frame(frame, direction)
            except _FencedInput:
                return

        async def vad(pcm):
            ticket = self.ticket()
            if ticket is None:
                raise _FencedInput()
            result = await original_vad(pcm)
            if not self.accepts(ticket):
                raise _FencedInput()
            return result

        async def takeover(*args, **kwargs):
            # Never call controller.interrupt() on the committing generation.
            if self.held:
                raise _FencedInput()
            return await original_takeover(*args, **kwargs)

        processor.process_frame = process_frame
        processor.vad.analyze_audio = vad
        processor.takeover = takeover
        controller = processor.controller
        for name in ('submit', 'submit_recognized'):
            original = getattr(controller, name)

            def guarded_submit(*args, _original=original, **kwargs):
                if self.held:
                    return None
                return _original(*args, **kwargs)

            setattr(controller, name, guarded_submit)
        original_speak = controller.speak

        async def speak(*args, **kwargs):
            ticket = self.ticket()
            if ticket is None:
                return False  # Handoff has a GUI result, never ordinary TTS.
            result = await original_speak(*args, **kwargs)
            return result if self.accepts(ticket) else False

        controller.speak = speak
        provider = getattr(controller, 'provider', None)
        if provider is not None:
            original_answer = provider.answer

            async def answer(*args, **kwargs):
                ticket = self.ticket()
                if ticket is None:
                    raise asyncio.CancelledError()
                result = await original_answer(*args, **kwargs)
                # The already running confirmation transaction retains its
                # own authority; it must return the handoff result normally.
                if (not self.accepts(ticket)
                        and asyncio.current_task() is not self._confirming_task):
                    raise asyncio.CancelledError()
                return result

            provider.answer = answer
        # Actual overlap probes use the supplied speech client directly, so
        # fence its request and late result, not only controller.recognize().
        clients = getattr(controller, 'real_clients', None)
        seen = set()
        while clients is not None and id(clients) not in seen:
            seen.add(id(clients))
            delegated = getattr(clients, 'client', None)
            if delegated is None:
                break
            clients = delegated
        if clients is not None:
            original_call = clients.call

            async def speech_call(role, *args, **kwargs):
                ticket = self.ticket()
                if role in {'asr', 'tts'} and ticket is None:
                    raise asyncio.CancelledError()
                result = await original_call(role, *args, **kwargs)
                if role in {'asr', 'tts'} and not self.accepts(ticket):
                    raise asyncio.CancelledError()
                return result

            clients.call = speech_call
        self._emit('call_runtime_voice_hooks_bound', native_browser_handoff=True,
                   no_remote_ASR=True, automatic_restart=False)

    async def queue(self, p, generation):
        """Trusted exact-review/confirmation edge only; contains no await."""
        if self.callbacks is None:
            raise CallSessionError('call_host_callbacks_not_configured')
        if self.held or self.task is not None and not self.task.done():
            raise CallSessionError('call_handoff_not_replayed')
        if self.console.mode != 'voice' or self.resources is None:
            raise CallSessionError('owned_voice_call_handoff_required')
        confirming = asyncio.current_task()
        voice_owner = self.console.task
        controller = self.resources['processor'].controller
        # Engine/Gateway generation and voice-controller generation are separate
        # clocks. authorize(p,g) validates the former via registry.current(g).
        # Here check the actual voice owner without equating those namespaces.
        voice_generation = getattr(getattr(controller, 'clock', None), 'current', None)
        if (confirming is None or voice_owner is None or voice_owner.done()
                or voice_owner is confirming or voice_generation is None
                or not controller.current(voice_generation)):
            raise CallSessionError('current_confirming_turn_required')
        proof = self.callbacks['authorize'](p, generation)
        if inspect.isawaitable(proof):
            if inspect.iscoroutine(proof):
                proof.close()
            raise CallSessionError('synchronous_confirmation_boundary_required')
        if (not isinstance(proof, dict) or proof.get('source') != 'host_exact_review_confirmation'
                or not all(_ident(proof.get(k)) for k in ('owner', 'gui_session', 'operation_id'))
                or proof['operation_id'] != p.get('operation_id')
                or proof.get('intent_hash') != p.get('expected_digest')
                or not isinstance(proof.get('intent_hash'), str)
                or not re.fullmatch('[0-9a-f]{64}', proof['intent_hash'])
                or proof.get('gui_session') != self.console.shared.conversation_id
                or type(proof.get('expires_monotonic')) not in (int, float)
                or not self.clock() < proof['expires_monotonic'] <= self.clock() + 120):
            raise CallSessionError('exact_current_private_confirmation_binding_required')
        if proof['operation_id'] in self._consumed_ops:
            raise CallSessionError('confirmed_call_operation_not_replayed')
        # Do not yield between accepting authority and closing all ingress.
        with self.lock:
            self.proof = copy.deepcopy(proof)
            self.held = True
            self.epoch += 1
            self.state = 'HANDOFF_AUTHORIZED'
            self._hangup_claimed = False
            self._end_inflight = False
            self._check_inflight = False
            self._confirmed_settled = False
            self._voice_closed = False
            self.failure = None
            self._confirming_task = confirming
            self._consumed_ops.add(proof['operation_id'])
            self._recovered = False
        self.console.voice_armed = False
        self.console.state = 'call_handoff'
        self.console.notice = 'Preparing WhatsApp call; assistant microphone is suspended.'
        self.task = asyncio.create_task(self._continue(confirming, voice_owner),
                                        name='ME3-owned-call-handoff')
        self._emit('call_handoff_authorized', operation_id=proof['operation_id'],
                   dial_started=False, automatic_retry=False)
        return dict(state='HANDOFF_AUTHORIZED', operation_id=proof['operation_id'],
                    execution_started=False, verified=False)

    async def _continue(self, confirming, voice_owner):
        commit_started = False
        try:
            # This is a different GUI-owned task. It never makes the confirming
            # turn await its own controller/voice cleanup.
            result = await asyncio.shield(asyncio.gather(confirming, return_exceptions=True))
            self._confirmed_settled = True
            if any(isinstance(x, BaseException) for x in result):
                raise CallSessionError('confirming_transaction_did_not_settle')
            self.state = 'CLOSING_VOICE'
            self.console.stop_event.set()
            await asyncio.shield(asyncio.gather(voice_owner, return_exceptions=False))
            self.assert_voice_closed()
            if getattr(self.console, 'public_error', None):
                raise CallSessionError('voice_owner_closed_with_failure')
            self._voice_closed = True
            self.state = 'VOICE_CLOSED'
            self._emit('call_voice_owner_closed', operation_id=self.proof['operation_id'],
                       physical_ME3_capture_closed=True, ordinary_models_closed=True,
                       automatic_restart=False)
            if self.clock() >= self.proof['expires_monotonic']:
                raise CallSessionError('confirmed_call_authority_expired_before_dial')
            commit_started = True
            value = await self.callbacks['commit'](copy.deepcopy(self.proof))
            await self.observe(value)
        except BaseException as exc:
            if not self.held and self.state in {'ENDED','LOCAL_ENDED','NO_DIAL_ABORTED'}:
                self._emit('call_late_handoff_failure_after_terminal',
                           operation_id=(self.proof or {}).get('operation_id'),
                           error_type=type(exc).__name__, terminal_preserved=True,
                           automatic_retry=False)
                return
            self.state = 'HELD_UNCERTAIN'
            self.failure = type(exc).__name__
            self.console.state = 'call_uncertain'
            self.console.notice = 'Call state needs checking. No retry; use End call if available.'
            self._emit('call_handoff_failed', operation_id=(self.proof or {}).get('operation_id'),
                       error_type=type(exc).__name__, automatic_retry=False,
                       ordinary_input_suspended=True)
            abort = self.callbacks.get('abort_before_dispatch')
            if not commit_started and callable(abort) and confirming.done():
                # A distinct no-dial abort, never redial or a manufactured ENDED.
                # This also covers failure before128 has acquired an audiohold.
                try:
                    self.console.stop_event.set()
                    await asyncio.shield(asyncio.gather(voice_owner, return_exceptions=False))
                    self.assert_voice_closed();self._voice_closed = True
                    result = await abort(copy.deepcopy(self.proof))
                    if not isinstance(result, dict) or result.get('state') != 'NO_DIAL_ABORTED':
                        raise CallSessionError('durable_no_dial_abort_result_required')
                    await self.observe(result)
                except BaseException:
                    # Failed local cleanup/abort stays fenced; no second try.
                    self.state = 'HELD_UNCERTAIN'

    def assert_voice_closed(self):
        if self._recovered and self.resources is None:
            task = getattr(self.console, 'task', None)
            if (getattr(self.console, 'controller', None) is None
                    and getattr(self.console, 'processor', None) is None
                    and (task is None or task.done())):
                return True
            raise CallSessionError('recovered_GUI_must_have_no_voice_owner')
        if self.resources is None or not self._confirmed_settled:
            raise CallSessionError('settled_owned_voice_resources_required')
        r = self.resources
        transport = r['transport']
        processor = r['processor']
        if (getattr(transport.input(), '_in_stream', object()) is not None
                or getattr(transport.output(), 'stream', object()) is not None
                or getattr(transport, '_closed', False) is not True
                or getattr(processor, 'closing', False) is not True
                or getattr(processor.controller, 'closed', False) is not True):
            raise CallSessionError('actual_ME3_streams_and_controller_must_be_closed')
        echo = r['echo']
        if echo is not None:
            worker = getattr(echo, 'capture_worker', None)
            if (echo.closed is not True or worker is None or not worker.closed.is_set()
                    or worker.cleanup_failed):
                raise CallSessionError('actual_AEC_capture_worker_must_be_closed')
        for key in ('music_bus', 'stream_binding'):
            resource = r[key]
            if resource is not None and getattr(resource, 'closed', False) is not True:
                raise CallSessionError('actual_owned_media_must_be_closed')
        return True

    async def recover_hold(self, record, *, verify_recovery):
        """Trusted startup only, BEFORE exposing GUI start controls.

        The injected service verifier must prove the durable active/uncertain
        operation, old ME3 process/capture closure, and explicit adoption by this
        GUI session. It returns newly bound private proof, not a JSON boolean.
        PREPARED/unconfirmed proposals must not be adopted. No commit is run.
        """
        if self.held or self.resources is not None or self.task is not None:
            raise CallSessionError('recovery_requires_fresh_standby_GUI')
        if not callable(verify_recovery) or self.callbacks is None:
            raise CallSessionError('trusted_call_recovery_verifier_required')
        proof = verify_recovery(record, self.console.shared.conversation_id)
        if inspect.isawaitable(proof):
            proof = await proof
        if (not isinstance(proof, dict) or proof.get('source') != 'host_recovered_call_hold'
                or not all(_ident(proof.get(k)) for k in ('owner','operation_id','gui_session'))
                or proof['gui_session'] != self.console.shared.conversation_id
                or not isinstance(proof.get('intent_hash'), str)
                or not re.fullmatch('[0-9a-f]{64}', proof['intent_hash'])):
            raise CallSessionError('independently_bound_recovery_proof_required')
        self.proof = copy.deepcopy(proof)
        self._recovered = True
        self.assert_voice_closed()
        self._confirmed_settled = True;self._voice_closed = True
        # Only a trusted durable recovery proof may carry an earlier End claim.
        self._hangup_claimed = proof.get('end_requested') is True
        self._consumed_ops.add(proof['operation_id'])
        with self.lock:
            self.held = True;self.epoch += 1;self.state = 'HELD_UNCERTAIN'
        self.console.voice_armed = False;self.console.state = 'call_uncertain'
        self.console.notice = 'An earlier call needs checking. Use End call; no automatic retry.'
        return self.snapshot()

    async def observe(self, value):
        # Called only by the trusted lifecycle callback/monitor, never JSON/API.
        if not self.proof or not isinstance(value, dict):
            raise CallSessionError('owned_call_observation_required')
        if value.get('operation_id') != self.proof['operation_id']:
            raise CallSessionError('call_observation_identity_changed')
        state = value.get('state')
        if not self.held:
            # An independently verified terminal observation is final. A late
            # commit/End response cannot reopen privacy or change local state.
            if self.state in {'ENDED','LOCAL_ENDED','NO_DIAL_ABORTED'}:
                self._emit('call_late_observation_after_terminal',
                           operation_id=self.proof['operation_id'],
                           terminal_preserved=True, automatic_retry=False)
                return self.snapshot()
            raise CallSessionError('owned_active_call_required')
        if state in {'ENDED', 'LOCAL_ENDED', 'NO_DIAL_ABORTED'}:
            verified = self.callbacks['verify_terminal'](value, copy.deepcopy(self.proof))
            if inspect.isawaitable(verified):
                verified = await verified
            if verified is not True:
                raise CallSessionError('independent_exact_terminal_call_proof_required')
            self.assert_voice_closed()
            with self.lock:
                self.epoch += 1
                self.held = False
                self.state = state
            self.console.state = 'stopped'
            self.console.notice = (
                'Call ended. Tap the microphone to continue with ME3.' if state == 'ENDED' else
                'WhatsApp closed locally; remote end is not confirmed. Tap the microphone for ME3.'
                if state == 'LOCAL_ENDED' else
                'Call did not start. Tap the microphone for a new request.')
            self._emit('call_session_released', operation_id=self.proof['operation_id'],
                       terminal_state=state, automatic_restart=False,
                       shared_history_preserved=True)
        elif state in {'DIALING', 'RINGING', 'CONNECTED', 'HELD_UNCERTAIN'}:
            self.state = state
            self.console.state = 'call_' + state.lower()
            self.console.notice = {'DIALING':'Calling…', 'RINGING':'Ringing…',
                                   'CONNECTED':'WhatsApp call connected.',
                                   'HELD_UNCERTAIN':'Call status is unverified; no automatic retry.'}[state]
        else:
            raise CallSessionError('unsupported_call_state_observation')
        return self.snapshot()

    def gui_request(self, operation_id, *, action='end_call'):
        """Use only after Handler verified actual cookie/Origin/CSRF/Host."""
        if not self.proof or operation_id != self.proof['operation_id']:
            raise CallSessionError('current_GUI_call_operation_required')
        if action not in {'end_call','check_call_status'}:
            raise CallSessionError('exact_GUI_call_action_required')
        return _GuiEndRequest(operation_id, self._gui_seal, action)

    def authenticate_gui(self, request):
        if (not isinstance(request, _GuiEndRequest) or request.seal is not self._gui_seal
                or not self.held or not self.proof
                or request.operation_id != self.proof['operation_id']
                or request.action not in {'end_call','check_call_status'}):
            raise CallSessionError('authenticated_current_GUI_hangup_required')
        return dict(source='authenticated_gui', owner=self.proof['owner'],
                    gui_session=self.proof['gui_session'], action=request.action,
                    operation_id=request.operation_id)

    async def end_call(self, request):
        if self.authenticate_gui(request)['action'] != 'end_call':
            raise CallSessionError('exact_GUI_End_call_required')
        if self._hangup_claimed or not self._voice_closed:
            raise CallSessionError('owned_once_only_call_hangup_required')
        self._hangup_claimed = True
        self._end_inflight = True
        try:
            value = await self.callbacks['hangup'](copy.deepcopy(self.proof), request)
            return await self.observe(value)
        except BaseException:
            if self.held:
                self.state = 'HELD_UNCERTAIN'
                self.console.state = 'call_uncertain'
                self.console.notice = 'End call was not verified. Use Check call status; End will not be repeated.'
            raise
        finally:
            self._end_inflight = False

    async def check_call_status(self, request):
        """One owner-clicked read/reconciliation; never End/dial/retry/ASR."""
        if self.authenticate_gui(request)['action'] != 'check_call_status':
            raise CallSessionError('exact_GUI_Check_call_status_required')
        if (not self._hangup_claimed or not self._voice_closed or self._end_inflight
                or self._check_inflight or not callable(self.callbacks.get('reconcile'))):
            raise CallSessionError('failed_or_unverified_End_required_before_status_check')
        self.assert_voice_closed()
        self._check_inflight = True
        try:
            value = await self.callbacks['reconcile'](copy.deepcopy(self.proof), request)
            result = await self.observe(value)
            if self.held:
                self.console.notice = 'Call state remains unverified. ME3 listening stays off; no End or call was repeated.'
        except BaseException:
            if self.held:
                self.state = 'HELD_UNCERTAIN';self.console.state = 'call_uncertain'
                self.console.notice = 'Call status could not be verified. ME3 listening stays off; no automatic retry.'
            raise
        finally:
            self._check_inflight = False
        return self.snapshot()


class ClosedVoiceBarriers:
    """128's synchronous adapter, backed by actual131 close assertions.

    The async GUI continuation has already awaited real owner shutdown. This
    does no async work and asserts no native-browser audio has ended. If the
    lifecycle lives in another process, only a private authenticated host proof
    created AFTER assert_voice_closed may represent these host observations;
    an MCP/model-provided voice_closed boolean must never substitute for it.
    """
    def __init__(self, manager):
        self.manager = manager

    def _closed(self, operation):
        manager = self.manager
        if (not manager.held or not manager.proof
                or operation != manager.proof['operation_id']
                or manager.ticket() is not None):
            raise CallSessionError('current_privacy_held_call_required')
        return manager.assert_voice_closed()

    def suspend_ingress(self, operation):
        return self._closed(operation)

    def quiet_output(self, operation):
        return self._closed(operation)

    def pause_media(self, operation):
        return self._closed(operation)

    def restore_before_dispatch(self, operation):
        # No physical reopening or old media restart.128 verifies no-dial abort.
        return self._closed(operation)

    def restore_after_ended(self, operation, receipt):
        if (not isinstance(receipt, dict) or receipt.get('operation_id') != operation
                or receipt.get('state') != 'ENDED'):
            raise CallSessionError('exact_ended_restore_required')
        return self._closed(operation)

