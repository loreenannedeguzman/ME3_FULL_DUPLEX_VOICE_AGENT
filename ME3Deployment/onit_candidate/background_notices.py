"""Bounded, passive completion notifications. No workers, timers, TTS or launch.

A host callback must establish real completion and current artifact ownership.
Capacity is a lifetime admission budget, including dropped/settled notices: this
bounds dedup memory without ever forgetting an accepted key.
"""
from dataclasses import dataclass, replace
import math
import re
import threading

_HASH = re.compile(r"[0-9a-fA-F]{64}\Z")


def _identity(value):
    if type(value) is not str or not value.strip() or len(value) > 512:
        raise ValueError("invalid identity")


def _source_revision(value):
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("expected exact lowercase source SHA256")


def _integer(value):
    if type(value) is not int or value < 0:
        raise ValueError("expected strict nonnegative integer")


def _finite(value):
    if type(value) not in (int, float):
        raise ValueError("expected finite time")
    try:
        valid = math.isfinite(value)
    except (OverflowError, ValueError):
        valid = False
    if not valid:
        raise ValueError("expected finite time")


def _issuer(value):
    if value is None or isinstance(value, (str, bytes, int, float, bool,
                                          list, tuple, dict, set, frozenset)):
        raise ValueError("issuer must be an opaque host object")


def _text(value):
    if type(value) is not str or not value.strip() or len(value) > 2048:
        raise ValueError("notice text must contain 1..2048 characters")
    if any(ord(ch) < 32 and ch not in "\n\t" for ch in value) or "\x7f" in value:
        raise ValueError("control character in notice")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError("invalid notice text") from exc


@dataclass(frozen=True)
class CompletionNotice:
    notice_id: str
    task_id: str
    task_revision: int
    conversation_id: str
    source_revision: str
    artifact_path: str
    artifact_sha256: str
    workspace_sha256: str
    completed_at: float
    expires: float
    ready_text: str
    offer_text: str

    def __post_init__(self):
        for key in ("notice_id", "task_id", "conversation_id"):
            _identity(getattr(self, key))
        _integer(self.task_revision)
        _source_revision(self.source_revision)
        path = self.artifact_path
        if (type(path) is not str or not path or len(path) > 1024 or
                "\\" in path or ":" in path or
                any(ord(ch) < 32 or ord(ch) == 127 for ch in path) or
                any(part in ("", ".", "..") for part in path.split("/"))):
            raise ValueError("expected safe relative artifact path")
        try:
            path.encode("utf-8")
        except UnicodeError as exc:
            raise ValueError("invalid artifact path") from exc
        for key in ("artifact_sha256", "workspace_sha256"):
            value = getattr(self, key)
            if type(value) is not str or not _HASH.fullmatch(value):
                raise ValueError("invalid SHA256")
        _finite(self.completed_at)
        _finite(self.expires)
        if self.completed_at >= self.expires:
            raise ValueError("completion must precede expiry")
        _text(self.ready_text)
        _text(self.offer_text)


@dataclass(frozen=True, eq=False)
class NoticeClaim:
    """Delivery bookkeeping, never an action/launch capability."""
    notice: CompletionNotice
    delivery_epoch: int
    controller_generation: int
    engine_generation: int


class BackgroundNotices:
    def __init__(self, issuer, capacity=4):
        _issuer(issuer)
        _integer(capacity)
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self._issuer = issuer
        self._capacity = capacity
        self._ids = set()
        self._keys = set()
        self._pending = []
        self._inflight = None
        self._closed = False
        self._checking = False
        self._lock = threading.RLock()

    def _guard(self):
        if self._checking:
            raise ValueError("validator must not reenter notice manager")

    def enqueue(self, notice, issuer):
        with self._lock:
            self._guard()
            if issuer is not self._issuer:
                raise ValueError("wrong issuer")
            if type(notice) is not CompletionNotice:
                raise ValueError("expected CompletionNotice")
            if self._closed:
                return False
            key = (notice.task_id, notice.task_revision, notice.artifact_sha256.lower())
            if (notice.notice_id in self._ids or key in self._keys or
                    len(self._ids) >= self._capacity):
                return False
            self._ids.add(notice.notice_id)
            self._keys.add(key)
            self._pending.append(replace(notice))
            return True  # starts nothing

    def _refresh(self, conversation_id, source_revision, now, validate):
        _identity(conversation_id)
        _source_revision(source_revision)
        _finite(now)
        if not callable(validate):
            raise ValueError("host validator required")

        def current(notice):
            if (notice.conversation_id != conversation_id or
                    notice.source_revision != source_revision or
                    now < notice.completed_at or now >= notice.expires):
                return False
            self._checking = True
            try:
                return validate(notice) is True
            except Exception:
                return False  # uncertainty drops; no retry/requeue
            finally:
                self._checking = False

        self._pending = [n for n in self._pending if current(n)]
        if self._inflight is not None and not current(self._inflight.notice):
            self._inflight = None

    def peek(self, conversation_id, source_revision, now, validate):
        with self._lock:
            self._guard()
            if self._closed:
                return None
            self._refresh(conversation_id, source_revision, now, validate)
            return self._pending[0] if self._pending else None

    def claim(self, conversation_id, source_revision, now, validate,
              delivery_epoch, controller_generation, engine_generation,
              idle, session_active):
        for value in (delivery_epoch, controller_generation, engine_generation):
            _integer(value)
        with self._lock:
            self._guard()
            if self._closed:
                return None
            self._refresh(conversation_id, source_revision, now, validate)
            if (idle is not True or session_active is not True or
                    self._inflight is not None or not self._pending):
                return None
            notice = self._pending.pop(0)  # consumed BEFORE any TTS
            claim = NoticeClaim(notice, delivery_epoch, controller_generation,
                                engine_generation)
            self._inflight = claim
            return claim

    def settle(self, claim, outcome):
        with self._lock:
            self._guard()
            if type(outcome) is not str or outcome not in ("played", "dropped", "failed"):
                raise ValueError("invalid delivery outcome")
            if self._closed or claim is not self._inflight or claim is None:
                return False
            self._inflight = None
            return True  # all outcomes terminal; never requeue

    def discard(self, task_id=None, conversation_id=None):
        """Drop matching pending AND claimed notices; never touch a backend."""
        if task_id is not None:
            _identity(task_id)
        if conversation_id is not None:
            _identity(conversation_id)
        with self._lock:
            self._guard()
            def matches(notice):
                return ((task_id is None or notice.task_id == task_id) and
                        (conversation_id is None or notice.conversation_id == conversation_id))
            before = len(self._pending)
            self._pending = [n for n in self._pending if not matches(n)]
            count = before - len(self._pending)
            if self._inflight is not None and matches(self._inflight.notice):
                self._inflight = None
                count += 1
            return count

    def close(self):
        with self._lock:
            self._guard()
            self._closed = True
            self._pending.clear()
            self._inflight = None
