# Normal Pi operation

Complete the reviewed prerequisites and fresh operator/source authorization in `SETUP.md` first. The following is the actual current GUI entry and option shape; it does not bypass missing prerequisites or start an authorized voice session by itself.

```sh
cd "$HOME/ME3Deployment"
PYTHONPATH="$PWD/tools:$PWD" ME3_PROJECT_ROOT="$PWD" PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python -P -B -X utf8 -m onit_candidate.gui \
  --port 8765 --warm-floor --validated-delivery --background-ready
```

Open `http://127.0.0.1:8765/` in Chromium **on the Pi**. The GUI is local/authenticated, and its microphone remains off until the user starts it. Do not expose the control port directly to the Internet. A Pi desktop session over Wi-Fi can run and display this interface without a laptop runtime process. Wi-Fi still supplies the external ASR/reasoning/research/account connections when those features are authorized.

The project-only `PYTHONPATH` matches the reviewed Pi launch environment and is required because `-P` does not automatically add the current directory. It does not point to any laptop directory or install global Python settings.

Keep the explicit options consistent with the reviewed profile. `--task-recovery` remains off. `scripts/open_assistant_gui.py` is a historical desktop helper whose default launch omits the reviewed opt-in flags; it is not an equivalent command for this measured profile. Older `run_*voice.sh`, Gemini and benchmark entry points are separate historical tools.

Check the normal/text readiness indication and the selected input/output route before starting. The observed normal configuration allows up to 3,600 seconds and 128 turns, with a separate 32-ASR-attempt session limit. These are guards, not a promise of uninterrupted provider service. Rolling standing admission is 14 hosted ASR/reasoning attempts per 60 seconds; cumulative recorded usage is not a lifetime quota. Do not raise limits or reset counters to continue a failed request.

The normal pipeline uses Pi-local capture, VAD/AEC and validated PCM output, student ASR/TTS MCP clients, the sole Onit task loop and guarded tool execution. Speak normally and interrupt when needed. A start acknowledgment describes a validated task decision; it does not certify the task's result. A service-ready notice covers only an explicitly requested owned Python service becoming freshly healthy, not arbitrary background code generation or all task completion.

Review and confirm each real-world action through its current context. A stopped conversation does not undo a completed send or stop an already transferred background server. Closing an app preview is separate from explicitly stopping its backend. Never retry an uncertain send automatically; inspect its recorded identity and use independent readback or a genuine owner report through the existing reconciliation procedure.

Stop through the GUI/normal conversation controls and wait for owned cleanup/readiness. Preserve the current conversation and error metadata when diagnosing a failure. Do not broadly kill Python processes, erase journals, reuse old operation IDs or clear a failed request as a substitute for reconciliation.

Known accepted limitation: after an automatically verified WhatsApp send and window return, an additional close command against the already closed window could stop the conversation. The final recorded operation was verified as submitted, and the redundant close was not dispatched. This is not a claim of recipient delivery for arbitrary future messages or guaranteed network recovery.

## English/Spanish output

Ask naturally for a Spanish translation or response; no language button is required. The main reasoner's validated answer segments select the configured English or female Spanish Piper voice. English remains `en_US-ljspeech-medium`; Spanish is `es_ES-sharvard-medium` with female speaker ID 1. These are distinct voice identities.

The recognition path remains English-configured. Spoken Spanish can be captured as English or need a typed request/correction; successful Spanish output is not proof of automatic multilingual ASR. The [product report](<../2 - ME3_PRODUCT_REPORT.md#spanish-translation-and-spoken-output>) separates owner demonstrations from unmeasured Spanish recognition, translation and pronunciation quality. Other spoken languages are not qualified, and an unsupported language must not be labelled English/Spanish to force playback.
