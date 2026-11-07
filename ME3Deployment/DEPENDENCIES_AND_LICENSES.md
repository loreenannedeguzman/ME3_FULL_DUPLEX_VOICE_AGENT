# Dependencies, assets and licenses

## Included

The source tree, project dependency declarations/lock files, AEC shim source/build helper, and the existing Baby Onit adaptation notice/license are included. `third_party/baby-onit/NOTICE` identifies the adapted retrieval approach, upstream commit and changes; `third_party/baby-onit/LICENSE` retains Apache License 2.0. This notice does not license unrelated dependencies or the entire ME3 project.

The owner has not selected a project-wide redistribution license in this staging step. Do not add an assumed MIT/Apache/GPL grant or remove existing notices before owner review.

## Separate Python environments

The measured main environment includes Pipecat 1.12.0, MCP 1.26.0, aiohttp 3.14.5, NumPy 2.5.4, ONNX Runtime 1.24.4, PyAudio 0.2.14 and soundfile 0.13.1. The complete observed versions are in `provenance/dependency-identities.json`. The lock files include older/alternate milestone environments as well; use the explicitly reviewed main/Piper/Onit environment layout rather than installing every file into one environment.

Piper 1.8.0 is isolated from the main environment. Full Onit has its own pinned source/archive and venv. Benchmark/scoring dependencies belong to separate environments and are not requirements for normal Pi voice use. No environment directory, compiled wheel cache or account sign-in store is redistributed here.

## Voice and model assets

`provenance/voice-assets.json` records six observed model/config identities. The normal voice profile actively selects English `en_US-ljspeech-medium` and Spanish `es_ES-sharvard-medium`; the additional observed Spanish Dave assets are not an automatic fallback. All six captured assets matched their recorded hashes.

The selected Spanish voice uses female speaker ID 1. It is a separate voice from English, not a single bilingual speaker identity. Translation is prepared through the existing main reasoner, with validated language-tagged segments selecting local TTS; there is no separate translation API in this speech-output integration. Owner demonstrations and the unmeasured Spanish quality/recognition boundaries are described in [the product report](<../2 - ME3_PRODUCT_REPORT.md#spanish-translation-and-spoken-output>).

Weights and model cards/licenses are not included. Obtain each exact voice from its original publisher, retain its corresponding model card and license, and verify the recorded hash before use. A checksum is not a license. The historical download helper targets an older English high voice and does not provision the current normal profile.

## Native/system requirements

The AEC shim is built by `tools/build_webrtc_aec.py` for aarch64 Linux against the matching WebRTC audio-processing 1.3 runtime/development headers. It expects ABI 1 and profile `0x101` (AEC3, low noise suppression, no AGC). The helper preserves an existing build and does not install packages or alter routing. Native binary/build hashes from the measured Pi are retained in `dependency-identities.json`; the binaries themselves are not included.

Bubblewrap/user namespaces, FFmpeg, Chromium, ALSA/PortAudio, libsndfile and relevant PDF/media tools are separate OS packages. Reproduce matching architecture/runtime compatibility and retain the package copyright/license materials when distributing binaries. The source-only restore did not load/rebuild those libraries or prove their installation on a new OS.

Full Onit's executable source is not vendored by this handoff. Obtain the exact pinned upstream archive and its license before provisioning or redistributing it. Historical source references are not permission to substitute a different upstream revision.
