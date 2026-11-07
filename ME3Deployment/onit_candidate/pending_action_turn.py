"""Nonexecuting, one-turn review binding. No language interpretation or dispatch.

The host, not this module, owns evidence and authorization. Never deserialize an
issuer from a model/tool response. See integration-notes.txt for the final gate.
"""
from dataclasses import dataclass, replace
from collections.abc import Mapping
import hashlib
import math
import re
import threading

_HASH = re.compile(r"[0-9a-fA-F]{64}\Z")
_IDENTITIES = ("operation_id", "action", "goal_id", "conversation_id",
               "communication_owner")
_INTS = ("context_revision", "controller_generation",
         "engine_generation")
_HASHES = ("source_revision", "expected_digest", "prompt_sha256", "raw_request_sha256")
_FLAGS = ("direct_separate_user_input", "delivered_complete", "nonpartial",
          "capture_finished_after_review", "current_owner", "current_source",
          "current_grant")
_STATES = {"phase": "confirm", "ledger": "WAITING_CONFIRMATION",
           "journal": "PROPOSED"}
_DECISIONS = ("confirm_unchanged", "ask_or_explain", "cancel", "revise")


def _identity(value):
    if type(value) is not str or not value.strip() or len(value) > 512:
        raise ValueError("invalid identity")


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
    # JSON-compatible values can never serve as host capabilities.
    if value is None or isinstance(value, (str, bytes, int, float, bool,
                                          list, tuple, dict, set, frozenset)):
        raise ValueError("issuer must be an opaque host object")


@dataclass(frozen=True)
class ReviewBinding:
    operation_id: str
    action: str
    expected_digest: str
    prompt_sha256: str
    goal_id: str
    conversation_id: str
    source_revision: str
    context_revision: int
    controller_generation: int
    engine_generation: int
    communication_owner: str
    raw_request_sha256: str
    expires: float

    def __post_init__(self):
        for name in _IDENTITIES:
            _identity(getattr(self, name))
        for name in _INTS:
            _integer(getattr(self, name))
        for name in _HASHES:
            value = getattr(self, name)
            if type(value) is not str or not _HASH.fullmatch(value):
                raise ValueError("invalid SHA256")
        _finite(self.expires)


@dataclass(frozen=True)
class ReviewDecision:
    """Informational result only: not an execution/authorization capability."""
    decision: str
    operation_id: str
    expected_digest: str
    current_request: str
    binding: ReviewBinding


class PendingReviewTurn:
    def __init__(self, binding, current_request, issuer, evidence):
        if type(binding) is not ReviewBinding:
            raise ValueError("expected ReviewBinding")
        _issuer(issuer)
        if type(current_request) is not str:
            raise ValueError("request must be exact text")
        try:
            digest = hashlib.sha256(current_request.encode("utf-8")).hexdigest()
        except UnicodeError as exc:
            raise ValueError("request must be valid UTF-8") from exc
        if digest != binding.raw_request_sha256:
            raise ValueError("raw request mismatch")
        if not isinstance(evidence, Mapping):
            raise ValueError("expected host evidence mapping")
        copied = dict(evidence)
        expected = set(_FLAGS) | set(_STATES) | {
            "operation_id", "action", "expected_digest"}
        if set(copied) != expected:
            raise ValueError("incomplete or unexpected evidence")
        for key in _FLAGS:
            if copied[key] is not True:
                raise ValueError("unqualified input evidence")
        for key, value in _STATES.items():
            if type(copied[key]) is not str or copied[key] != value:
                raise ValueError("review state mismatch")
        for key in ("operation_id", "action", "expected_digest"):
            if type(copied[key]) is not str or copied[key] != getattr(binding, key):
                raise ValueError("review identity mismatch")
        # All retained evidence members are validated immutable scalars.
        self._evidence = tuple(sorted(copied.items()))
        self._binding = replace(binding)
        self._request = current_request
        self._issuer = issuer
        self._bound = False
        self._used = False
        self._closed = False
        self._lock = threading.RLock()

    @property
    def binding(self):
        return self._binding

    def _available(self):
        if self._closed or self._used:
            raise ValueError("turn unavailable")

    def bind(self, context_revision, engine_generation):
        """One host handoff, with both generations strictly increasing."""
        _integer(context_revision)
        _integer(engine_generation)
        with self._lock:
            self._available()
            if self._bound:
                raise ValueError("already bound")
            if (context_revision <= self._binding.context_revision or
                    engine_generation <= self._binding.engine_generation):
                raise ValueError("both binding counters must increase")
            self._binding = replace(self._binding,
                                    context_revision=context_revision,
                                    engine_generation=engine_generation)
            self._bound = True
            return self._binding

    def declaration(self):
        """Fresh JSONSchema data only. No executable tool or issuer token."""
        with self._lock:
            self._available()
            if not self._bound:
                raise ValueError("turn not bound")
            return {
                "type": "object",
                "properties": {
                    "operation_id": {"type": "string", "const": self._binding.operation_id},
                    "expected_digest": {"type": "string", "const": self._binding.expected_digest},
                    "current_request": {"type": "string", "const": self._request},
                    "decision": {"type": "string", "enum": list(_DECISIONS)},
                },
                "required": ["operation_id", "expected_digest", "current_request", "decision"],
                "additionalProperties": False,
            }

    def validate_decision(self, args, current_binding, now, issuer):
        """Validate and consume; never confirm, dispatch, or restore anything."""
        with self._lock:
            self._available()
            if not self._bound:
                raise ValueError("turn not bound")
            if issuer is not self._issuer:
                raise ValueError("wrong issuer")
            _finite(now)
            if now >= self._binding.expires:
                self._closed = True
                raise ValueError("expired turn")
            if type(current_binding) is not ReviewBinding or current_binding != self._binding:
                raise ValueError("full current binding mismatch")
            if not isinstance(args, Mapping):
                raise ValueError("expected decision object")
            copied = dict(args)
            if set(copied) != {"operation_id", "expected_digest", "current_request", "decision"}:
                raise ValueError("expected exactly four arguments")
            expected = {"operation_id": self._binding.operation_id,
                        "expected_digest": self._binding.expected_digest,
                        "current_request": self._request}
            for key, value in expected.items():
                if type(copied[key]) is not str or copied[key] != value:
                    raise ValueError("decision binding mismatch")
            if type(copied["decision"]) is not str or copied["decision"] not in _DECISIONS:
                raise ValueError("unknown informational decision")
            result = ReviewDecision(copied["decision"], expected["operation_id"],
                                    expected["expected_digest"], self._request,
                                    self._binding)
            self._used = True  # consume before handing anything to the caller
            return result

    def close(self):
        with self._lock:
            self._closed = True
