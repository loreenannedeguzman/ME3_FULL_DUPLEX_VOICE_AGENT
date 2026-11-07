# ME3 Deployment — review copy

This directory contains the ME3 implementation staged for owner inspection before any commit or upload to [ME3_FULL_DUPLEX_VOICE_AGENT](https://github.com/loreenannedeguzman/ME3_FULL_DUPLEX_VOICE_AGENT).

The measured Raspberry Pi candidate is `03d7a144917edb920949c1ebaca116e277cdb55ae5c2589018334f14d522721b` (586 source entries, 64 native package entries). This publication copy contains disclosed privacy substitutions and excludes the operator's governance, credentials, authorization and working data. It has **not** been admitted or run as a replacement installation. Its hashes are recorded separately in `provenance/`; it must not be presented as a byte-identical copy of the measured candidate.

ME3 runs the normal voice/GUI process, Onit task loop, local VAD/AEC, guarded MCP services and resident Piper on the Pi. ASR uses the configured Groq speech endpoint; the selected main reasoner uses the existing ChatGPT-plan transport. Internet access and separately authorized provider/account setup remain required. The laptop used for development and later benchmark collection is not a normal runtime dependency.

Read these documents in order:

The supported speech-output profile includes English and female Spanish Piper voices. Spanish drafting/translation and contextual replies have owner demonstrations; current ASR remains English-configured and may need typed clarification. This is not formal multilingual/translation-quality qualification. See [the capability report](<../2 - ME3_PRODUCT_REPORT.md#spanish-translation-and-spoken-output>) and [operation](OPERATING.md#englishspanish-output).

1. [SETUP.md](SETUP.md): supported environment, prerequisites and provisioning boundaries.
2. [OPERATING.md](OPERATING.md): exact Pi-local standby command, microphone control, interruption and stopping.
3. [REPRODUCIBILITY.md](REPRODUCIBILITY.md): what was actually restored, what remains operator-specific and every publication delta.
4. [DEPENDENCIES_AND_LICENSES.md](DEPENDENCIES_AND_LICENSES.md): dependency environments, model assets, native ABI and licensing inventory.

No credentials, OAuth/browser profile, private phone/email recipient, existing action token, consumed operation or account database should be added to this directory. The example files do not authorize any provider or action. Never reuse a historical grant or reset/refund a consumed action to make a new installation pass.

The owner accepted the final measured scoped send/autoclose result with a known limitation: a redundant WhatsApp-close request after the window had already closed could end the conversation. The completed send remains unchanged. Network/ASR availability is not guaranteed. See the project report for complete measured results and limitations; this directory adds no benchmark or whole-project pass claim.

Project-wide redistribution licensing awaits the owner's decision. Existing third-party notices remain in place.
