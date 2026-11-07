# Raspberry Pi setup

## Measured target

The observed host was a Raspberry Pi 5 Model B Rev 1.1, aarch64 Linux, kernel `6.18.50+rpt-rpi-2712`, four logical CPUs and 8,449,769,472 bytes of reported RAM. Wi-Fi interface `wlan0` was up; Ethernet was down. The observed USB audio card reported vendor/product `08bb:2902`; HDMI audio cards were also present. These kernel observations identify the measured host; they are not a new device test, hardware photograph or clean-OS installation result.

`provenance/hardware-observed.json` retains the bounded observation. `config/asound-pi.conf` describes the measured USB-microphone/HDMI-speaker routing. Select and inspect the devices on a different Pi; card numbers, names and acoustic conditions may differ. Do not change global routing merely to copy a historical device index.

## Environments and files to provision

Choose a Pi-local directory, for example `$HOME/ME3Deployment`. Run commands from that directory. The Python source resolves its project root locally; normal operation does not need an `F:` drive, a Windows folder or a laptop SSH process.

The following are separate prerequisites, not bundled installations:

- Main CPython environment: the observed interpreter was CPython 3.13.5 on aarch64. Preserve `requirements-m3-pi.lock.txt` and `requirements-pi.lock.txt` as recorded version evidence. Review platform wheels/system build prerequisites before installing; a version list alone does not recreate the environment.
- Resident Piper environment at `.venv-piper/`, using `piper-tts==1.8.0`, with its own lock file. Do not merge it into the main environment.
- Exact pinned full-Onit source archive and extracted source at `.tmp/full-onit-trial-20261026-001/{source.tar.gz,source}`, and the separately provisioned Onit interpreter environment at `.tmp/full-onit-trial-20261026-003/venv`. The engine checks archive SHA256 `00da74309accbdf6f5483a9167d3b52da8f1a61826b7272d55f91babf08da346` and extracted bytes. Obtain the matching upstream commit `e0bbe9179b39ca3e928bf2d83ae972d3df6bb6c5` and its license; no upstream executable archive or venv is bundled here. Do not substitute a current checkout under the old hash.
- Bubblewrap with private user/PID/network namespace support; the observed service runtime used version 0.12.0. FFmpeg, Chromium, PortAudio, ALSA, libsndfile and relevant PDF/media tools are separate system dependencies.
- The WebRTC AEC shim and matching native runtime described in `DEPENDENCIES_AND_LICENSES.md`.
- The exact Piper voice weights/configuration files in `provenance/voice-assets.json`. They are omitted from this directory.

The selected output profile uses English `en_US-ljspeech-medium` and Spanish `es_ES-sharvard-medium` with female speaker ID 1. Provision the exact model/config identities and voice-specific licenses; do not substitute the older male Spanish assets as a fallback. The resident TTS language configuration validates the selected assets before advertising Spanish output. No new setup or voice download was performed for this documentation update. Spanish output and translation do not change the English-configured ASR setting or establish multilingual recognition qualification.

For a separately reviewed installation, the conventional local environment commands are:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-m3-pi.lock.txt
.venv/bin/python -m pip check
python3 -m venv .venv-piper
.venv-piper/bin/python -m pip install -r requirements-piper-pi.lock.txt
.venv-piper/bin/python -m pip check
```

These commands were **not rerun** for the publication copy. They may need matching OS headers/shared libraries and available aarch64 distributions. `scripts/setup_pi.sh` is an older dependency helper and rewrites a lock file; it is not an automatic final-release installer. `scripts/download_piper_model.py` targets an older **high** English voice and must not be used as the normal **medium** voice setup.

## Private operator configuration

Keep private files outside version control and set restrictive permissions. The normal speech reader expects the project-local `.env.groq` with `GROQ_API_KEY`; its POSIX reader requires ownership by the current user and no group/other permission bits. Use `operator-templates/.env.groq.example` only as a blank form. Do not print the key to logs or place it in a command line.

The selected main reasoner is `openai_chatgpt_plan` / `gpt-6.1-sol`, as declared in `onit_candidate/reasoning_profile.py`. This is the existing account-backed transport, not a generic OpenAI API-key configuration. Provision sign-in and source-bound host/usage records through the existing reviewed local authentication flow. This package does not include a new self-service authentication CLI and does not export the measured owner's sign-in store. Do not change the provider/model or silently fall back to make setup succeed.

Google Calendar/Gmail and WhatsApp require the new operator's own OAuth/browser/account setup and separately reviewed action authority. None is included. Sanitized phone/email/contact placeholders in the source are examples and cannot authorize a real recipient.

## Mandatory source/authority binding before startup

The measured runtime deliberately fails closed if governance, source, native index, configuration, policy or authority differs. The private omissions and privacy substitutions mean this folder is not already activated:

1. Review all `provenance/publication-delta.json` changes and the operator-specific configuration.
2. Privately provision the two governance documents and `config/operating_policy.json` for the new operator. The exact measured private documents are not public setup assets. `operator-templates/` contains incomplete, non-authorizing forms only.
3. Review and bind the resulting source map, native `candidate-files.json`, configuration/policy digests and governance pins. Changes to a hard-pinned module may also require its reviewed dependent hash guard to be rebound. Do not disable validation or replace a pin with a wildcard.
4. Issue fresh source-bound normal/text authority, provider admission and monotonic accounting records for that operator using the existing guarded activation procedure. Do not copy historical approvals, action journals or consumed tokens.
5. Verify source/native identities and mic-off standby before enabling the microphone. Any new operator binding has its own identity; it is not the measured `03d7…` candidate.

This source/privacy handoff does not claim a completed clean installation on a second Pi. The retained original Pi installation and the exact-source isolated restore are the demonstrated reproduction scopes.
