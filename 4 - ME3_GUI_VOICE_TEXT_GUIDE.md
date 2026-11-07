# ME3 GUI — voice, typed input and captured text

The GUI is deliberately simple: conversation, a microphone control, an alternative text composer, status/notices, task progress, Artifacts and Copy conversation. It runs in the normal Pi interface. The [product report](<2 - ME3_PRODUCT_REPORT.md>) describes the broader capability and evidence boundaries.

## Voice and recognized-text display

Press the microphone to start or restart a voice conversation. Wait for the greeting and Listening before speaking. Successful speech recognition supplies text to the same conversation controller used by typed input. The conversation displays user input and ME3 replies; the user can inspect what the recognizer understood.

This is a recognized-text display, not a guarantee of continuous word-by-word live transcription or an exact verbatim recording. The configured recognizer can mishear names or return wording/language that differs from what was spoken. No transcript is available when recognition fails before returning text; an orange notice reports the failure instead.

The visible reply can have Preparing, Interrupted or Not completed state. Displayed text is not proof that every word was audibly played. Actual speech delivery and the display have separate completion/ownership handling.

## Typed input as an alternative to voice

1. Type a request, clarification, name, email address or correction into **Type a message…**.
2. Press **Enter** or click the **Send arrow**. **Shift+Enter** inserts a newline.
3. Continue by text or return to voice in the same conversation. The composer has a 4,000-character input limit.

The frontend sends typed text to the same conversation owner. In an active voice session it submits into that session; outside active voice it selects text mode when ready. Typed input avoids the recognition step for that input, but reasoning/tools may still require internet access and ordinary safeguards.

To correct a misrecognized name, type a new message such as “I meant Kyoto, Japan.” This is a new clarification in the conversation, not an in-place edit of the earlier transcript. Voice and text do not create separate assistants or independent task histories.

## Context and action safeguards

Related typed and spoken requests share bounded conversational context, task references and pending-review handling. Earlier owner trials demonstrated voice/text artifact revision and a typed email recipient within a voice task. These are finite demonstrations, not a guarantee of unlimited memory or recovery across every new session.

A typed recipient or correction does not itself authorize sending. Consequential actions still require the current exact review and separate confirmation. If a result is uncertain, check its status instead of repeating the write/send. The **New conversation** control archives the current conversation and starts a new one; it should not be used when the intent is to continue the current context.

## Spanish requests and responses

ME3 demonstrated Spanish drafting/translation and contextual Spanish replies. Ask naturally, for example, “Translate this short message into Spanish” or “Please answer in Spanish.” No language button is required. The requested/contextual answer language selects the corresponding voice; English and Spanish use different Piper voices, with a female Spanish speaker.

Recognition remains configured for English, so spoken Spanish can appear as English text or require clarification. Type the intended Spanish wording or request when speech is unclear, then continue in the same conversation. Spanish speech output is separate from recognition; it does not establish fully qualified multilingual ASR. The [product report](<2 - ME3_PRODUCT_REPORT.md>) records the demonstrated scope and lack of a formal Spanish quality benchmark.

## Copy conversation

Click **Copy conversation** to copy the displayed user/assistant conversation as text. The copy retains displayed reply-state labels, including Interrupted and Not completed. It does not copy microphone audio, internal journals or every hidden tool event, and clicking it does not call a model or send the conversation to another service. Browser clipboard restrictions can require a fallback selection/copy step.

## Status and artifacts

- Ready/Listening/Thinking/Stopping/Stopped indicate the current session state. Start/restart voice through the microphone when appropriate.
- Orange notices report relevant connection, recognition or processing problems. A stopped conversation does not undo an already completed external action.
- Small task-progress text provides background information about execution; an acknowledgment or progress label is not completion evidence.
- Artifacts contains saved deliverables and supported in-GUI previews. Closing a preview and stopping its background server are distinct operations when a server is running.

## Implementation references

| Function | Source |
|---|---|
| Text composer, controls and preview markup | [onit_candidate/product_html.py](ME3Deployment/onit_candidate/product_html.py) |
| Enter/Shift+Enter, Send, displayed messages and Copy conversation | [onit_candidate/app.js](ME3Deployment/onit_candidate/app.js) |
| GUI state/notices and recognized-input handling | [onit_candidate/gui.py](ME3Deployment/onit_candidate/gui.py) |
| Shared conversation/history and task state | [onit_candidate/conversation.py](ME3Deployment/onit_candidate/conversation.py) |

These source links point to the sanitized publication copy. This guide documents the existing interface; it introduces no runtime change, new test result or fresh voice acceptance.

## Standalone Pi demonstration preparation

Use the Pi display, microphone, speaker and Wi-Fi. Open normal ME3; start voice when the professor is ready and wait for the greeting and Listening. The laptop and benchmark launcher are not part of the normal demonstration path.

The professor directs the interaction. Useful possibilities include a natural follow-up, a listening acknowledgment or correction during speech, a typed clarification followed by voice, inspecting captured text, Copy conversation, or an authorized saved-app/tool task. If a server was started, close its preview and stop its backend separately when needed. End naturally with Goodbye. Exact reviews and separate confirmations still apply to consequential actions.

If a service fails, inspect the status notice and stop if necessary. Resume through the microphone when ready. Do not repeat an outgoing message or other uncertain write without checking its actual outcome. The combined [architecture document](<3 - ME3_ARCHITECTURE.pdf>) and [two-page summary](<1 - ME3_PRESENTATION_SUMMARY.pdf>) provide presentation context; actual [benchmark results](Benchmarks/BENCHMARK_RESULTS.md) retain the measurement limitations.
