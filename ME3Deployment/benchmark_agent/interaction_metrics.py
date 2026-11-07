"""Additional final-agent metrics; callbacks are software proxies, never acoustics.

TOR is the fraction of observed opportunities in which the agent takes the
floor. For smooth completed turns this is desirable; during continuation it
is premature. Missing responses and censored observations remain explicit.
"""
from benchmark_final.analyze import ns


def interaction_metrics(plans, trials, events):
    normal = [t for t in trials if t['kind'] == 'normal']
    started = [t for t in normal if t['started']]
    closed = [t for t in started if t.get('operator') is not None and t['state'] != 'incomplete']
    responded = [t for t in closed if t['audio_observed']]
    pending = [t for t in started if t not in closed]
    by_id = {t['trial_id']: t for t in trials}
    continuation = []
    for trial_id, plan in plans.items():
        if plan['kind'] not in {'split_complete', 'split_replace'}:
            continue
        trial = by_id[trial_id]
        own = sorted((e for e in events if e.get('trial_id') == trial_id), key=ns)
        onsets = [e for e in own if e['event'] == 'bf_speech_onset']
        endpoints = [e for e in own if e['event'] == 'bf_endpoint']
        cues = [e for e in own if e['event'] == 'completion_agent_backchannel']
        # One opportunity per scripted continuing turn, regardless of cue count.
        # A terminal operator report and two observed speech segments are needed
        # to distinguish a completed observation from a truncated recording.
        scorable = bool(trial['started'] and trial.get('operator') is not None
                        and trial['state'] != 'incomplete'
                        and len(onsets) >= 2 and len(endpoints) >= 2
                        and ns(endpoints[-1]) >= ns(onsets[-1]))
        held_cues = [e for e in cues if scorable
                     and ns(onsets[0]) <= ns(e) < ns(endpoints[-1])]
        early = bool(scorable and any(
            e['event'] == 'bf_audio_submission' and e.get('nonzero')
            and e.get('kind') == 'response'
            and ns(onsets[0]) <= ns(e) < ns(endpoints[-1]) for e in own))
        continuation.append({'trial_id': trial_id, 'started': trial['started'],
                             'scorable': scorable, 'cue_events': len(cues),
                             'cue_on_held_floor': bool(held_cues),
                             'premature_takeover': early if scorable else None})
    scored = [t for t in continuation if t['scorable']]
    cued = [t for t in scored if t['cue_on_held_floor']]
    def ratio(a, b):
        return a / b if b else None
    def takeover(group):
        n = sum(t['premature_takeover'] for t in group)
        return {'status': 'MEASURED_SOFTWARE_PROXY' if group else 'NOT_MEASURED',
                'opportunities': len(group), 'premature_takeovers': n,
                'TOR': ratio(n, len(group))}
    return {
        'smooth_turn_taking_TOR': {
            'method': 'TO=1 for substantive response callback on an operator-closed normal trial, including semantically failed responses. TO=0 for a closed trial without that callback. Started trials lacking an operator report are censored, not assumed silent.',
            'status': 'MEASURED_SOFTWARE_PROXY' if closed else 'NOT_MEASURED',
            'planned': len(normal), 'started': len(started),
            'unstarted': len(normal) - len(started), 'closed_observations': len(closed),
            'response_takeovers': len(responded),
            'no_response_outcomes': len(closed) - len(responded),
            'TOR': ratio(len(responded), len(closed)),
            'no_response_rate': ratio(len(closed) - len(responded), len(closed)),
            'censored_started': len(pending),
            'response_callbacks_in_censored_trials': sum(t['audio_observed'] for t in pending),
            'operator_audible_responses': sum(t['operator'].get('audible') is True for t in closed),
            'physical_acoustic_TOR': 'NOT_MEASURED'},
        'pause_TOR': {**takeover(scored),
                      'method': 'One opportunity per terminally reported scripted split/continuation trial with two onsets and endpoints. TO=1 if a substantive callback occurs before the final continuation endpoint; listener cues are excluded.',
                      'unscored_started': sum(t['started'] and not t['scorable'] for t in continuation),
                      'unstarted': sum(not t['started'] for t in continuation)},
        'backchannel_TOR': {**takeover(cued),
                            'method': 'Same continuing-turn definition, restricted to trials with a cue on the held floor. Multiple cues do not multiply the denominator. Measures floor stealing in a cued trial, not causal attribution to the cue.',
                            'cue_events_in_scored_cued_trials': sum(t['cue_events'] for t in cued),
                            'JSD': {'status': 'NOT_MEASURED', 'reason': 'No aligned genuine human reference timing.'}},
        'continuation_observations': continuation}
