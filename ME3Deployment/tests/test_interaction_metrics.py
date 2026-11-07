import unittest
from benchmark_agent.interaction_metrics import interaction_metrics


def trial(name, kind='normal', started=True, report=True, audio=False, state='fail'):
    return {'trial_id': name, 'kind': kind, 'started': started,
            'operator': {'audible': audio} if report else None,
            'audio_observed': audio, 'state': state}


def event(name, at, trial_id='S', **kw):
    return {'event': name, 'monotonic_ns': int(at * 1e9), 'trial_id': trial_id, **kw}


class InteractionMetricsTests(unittest.TestCase):
    def test_missing_response_is_zero_but_unstarted_and_incomplete_are_not(self):
        ts = [trial('a', audio=True), trial('b'),
              trial('c', report=False), trial('d', started=False, report=False)]
        out = interaction_metrics({}, ts, [])['smooth_turn_taking_TOR']
        self.assertEqual((out['planned'], out['started'], out['closed_observations']), (4, 3, 2))
        self.assertEqual(out['TOR'], .5)
        self.assertEqual(out['no_response_outcomes'], 1)
        self.assertEqual(out['censored_started'], 1)
        self.assertEqual(out['unstarted'], 1)

    def test_semantic_failure_with_audio_remains_response_takeover(self):
        out = interaction_metrics({}, [trial('a', audio=True, state='fail')], [])
        self.assertEqual(out['smooth_turn_taking_TOR']['TOR'], 1)
        self.assertEqual(out['smooth_turn_taking_TOR']['physical_acoustic_TOR'], 'NOT_MEASURED')

    def continuation(self, early=False, report=True, last_endpoint=True):
        es = [event('bf_speech_onset', 1), event('bf_endpoint', 2),
              event('completion_agent_backchannel', 2.5),
              event('completion_agent_backchannel', 2.8), event('bf_speech_onset', 4)]
        if last_endpoint: es.append(event('bf_endpoint', 5))
        es.append(event('bf_audio_submission', 3 if early else 6, kind='response', nonzero=True))
        return interaction_metrics({'S': {'kind': 'split_complete'}},
                                   [trial('S', 'split_complete', report=report, audio=True)], es)

    def test_multiple_cues_one_opportunity_no_early_takeover(self):
        out = self.continuation()
        self.assertEqual(out['backchannel_TOR']['opportunities'], 1)
        self.assertEqual(out['backchannel_TOR']['cue_events_in_scored_cued_trials'], 2)
        self.assertEqual(out['backchannel_TOR']['TOR'], 0)
        self.assertEqual(out['pause_TOR']['TOR'], 0)
        self.assertEqual(out['backchannel_TOR']['JSD']['status'], 'NOT_MEASURED')

    def test_substantive_speech_during_pause_counts_premature_takeover(self):
        out = self.continuation(early=True)
        self.assertEqual(out['backchannel_TOR']['TOR'], 1)
        self.assertEqual(out['pause_TOR']['TOR'], 1)

    def test_unfinished_continuation_cannot_be_zero_filled(self):
        for kwargs in [{'report': False}, {'last_endpoint': False}]:
            out = self.continuation(**kwargs)
            self.assertIsNone(out['backchannel_TOR']['TOR'])
            self.assertEqual(out['pause_TOR']['unscored_started'], 1)

    def test_listener_audio_is_not_substantive_takeover(self):
        es = [event('bf_speech_onset', 1), event('bf_endpoint', 2),
              event('completion_agent_backchannel', 2.5),
              event('bf_audio_submission', 2.6, kind='backchannel', nonzero=True),
              event('bf_speech_onset', 4), event('bf_endpoint', 5)]
        out = interaction_metrics({'S': {'kind': 'split_complete'}},
                                  [trial('S', 'split_complete')], es)
        self.assertEqual(out['backchannel_TOR']['TOR'], 0)

    def test_no_opportunities_is_unmeasured_not_perfect(self):
        out = interaction_metrics({}, [], [])
        for key in ['smooth_turn_taking_TOR', 'pause_TOR', 'backchannel_TOR']:
            self.assertEqual(out[key]['status'], 'NOT_MEASURED')
            self.assertIsNone(out[key]['TOR'])

    def test_censored_audio_is_visible_without_closing_trial(self):
        out = interaction_metrics({}, [trial('a', report=False, audio=True)], [])['smooth_turn_taking_TOR']
        self.assertEqual(out['response_callbacks_in_censored_trials'], 1)
        self.assertEqual(out['closed_observations'], 0)
        self.assertIsNone(out['TOR'])

    def test_explicit_incomplete_report_is_censored(self):
        out = interaction_metrics({}, [trial('a', state='incomplete')], [])['smooth_turn_taking_TOR']
        self.assertEqual(out['censored_started'], 1)
        self.assertEqual(out['closed_observations'], 0)
