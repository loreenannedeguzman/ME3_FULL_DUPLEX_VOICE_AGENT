"""Explicit normal-session backend selection; never infer a provider from a key."""
import os,stat
from pathlib import Path
from dotenv import dotenv_values
from task_mcp.client import ROOT

BACKENDS=('google','groq')

def selected(grant,requested=None):
    backend=grant.get('backend','google')
    if backend not in BACKENDS or (requested is not None and requested!=backend):
        raise ValueError('assistant_backend_authorization_mismatch')
    return backend

def groq_key(path=None):
    path=Path(path or ROOT/'.env.groq')
    # The fixed project-owned file is read only by the selected reasoner/ASR.
    # It is never forwarded to Piper, task MCP servers, or the browser.
    if path.is_symlink() or not path.is_file():raise ValueError('private_groq_key_file_required')
    info=path.stat()
    if os.name!='nt' and (stat.S_IMODE(info.st_mode)&0o077 or info.st_uid!=os.getuid()):
        raise ValueError('private_groq_key_file_required')
    key=dotenv_values(path,encoding='utf-8-sig',interpolate=False).get('GROQ_API_KEY')
    if not isinstance(key,str) or not key.strip() or any(c.isspace() for c in key):raise ValueError('groq_credential_required')
    return key

def check_ready(backend):
    if backend not in BACKENDS:raise ValueError('unsupported_assistant_backend')
    if backend=='groq':groq_key()

def audio_settings(backend):
    # Normal authorization already covers microphone transmission to the
    # selected provider. Legacy Live's Google key/model gate applies to Google.
    check_ready(backend)
    from voice_agent import Settings
    return Settings.load(live=backend=='google')

def speech_module(backend):
    if backend not in BACKENDS:raise ValueError('unsupported_assistant_backend')
    return 'me3_assistant.groq_server' if backend=='groq' else 'me3_assistant.server'

def reasoning_class(backend):
    if backend=='google':
        from me3_assistant.reasoning import Reasoning
        return Reasoning
    if backend=='groq':
        from me3_assistant.groq_json import JSONGroqReasoning
        return JSONGroqReasoning
    raise ValueError('unsupported_assistant_backend')
