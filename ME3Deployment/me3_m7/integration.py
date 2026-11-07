"""Process-local opt-in integration; never edits accepted entrypoints or audio implementation."""
def apply():
    import m4_voice_agent as app
    from me3_m7.client import M7SpeechClients
    from me3_m7.controller import M7TurnController
    app.SpeechClients=M7SpeechClients;app.m3.TurnController=M7TurnController
    return app
