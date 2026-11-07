"""Bounded recovery of isolated silent output misses; never real audio/providers."""
import unittest
from me3_assistant.acoustic_echo import Reference

SILENCE=bytes(3840)

def seed():
    r=Reference()
    for i in range(51):r.offer(SILENCE,i*.02)
    return r

def healthy_until(r,stamp):
    # Actual callbacks, not elapsed wall time, establish continuous silence.
    while r.last_time+.02<stamp-1e-8:
        r.offer(SILENCE,r.last_time+.02)

class IsolatedIdleRecoveryTests(unittest.TestCase):
    def test_failed_live006_spacing_does_not_exhaust_session_lifetime(self):
        r=seed()
        # Preserved metadata intervals14.342819856s and2.663942410s.
        for stamp in (1.06,15.402819856,18.066762266):
            healthy_until(r,stamp)
            self.assertTrue(r.offer(SILENCE,stamp,4))
        self.assertEqual(r.idle_underflows,3)
        self.assertEqual(r.frame(r.last_time)[0],bytes(640))

    def test_many_isolated_misses_keep_total_telemetry_and_bounded_window(self):
        r=seed()
        for i in range(20):
            stamp=1.06+i*1.1;healthy_until(r,stamp)
            self.assertTrue(r.offer(SILENCE,stamp,4))
        self.assertEqual(r.idle_underflows,20)
        self.assertLessEqual(len(r.idle_underflow_times),2)

    def test_third_miss_in_one_second_still_fails_even_after_old_miss_ages_out(self):
        r=seed();r.offer(SILENCE,1.06,4)
        healthy_until(r,2.2);r.offer(SILENCE,2.2,4);r.offer(SILENCE,2.22,4)
        with self.assertRaisesRegex(ValueError,'idle_underflow_limit'):
            r.offer(SILENCE,2.24,4)
        self.assertEqual(r.idle_underflows,3)

    def test_rolling_window_does_not_reset_at_a_fixed_second_boundary(self):
        r=seed();healthy_until(r,1.98)
        r.offer(SILENCE,1.98,4);r.offer(SILENCE,2.02,4)
        with self.assertRaisesRegex(ValueError,'idle_underflow_limit'):
            r.offer(SILENCE,2.04,4)

    def test_aged_misses_do_not_authorize_active_or_recent_output_faults(self):
        for active_now in (False,True):
            with self.subTest(active_now=active_now):
                r=seed();r.offer(SILENCE,1.06,4);healthy_until(r,2.2)
                sound=b'\x01\x00'*1920
                if not active_now:r.offer(sound,2.2);r.offer(SILENCE,2.22)
                with self.assertRaisesRegex(ValueError,'playback_discontinuity'):
                    r.offer(sound if active_now else SILENCE,2.24,4)

    def test_aged_misses_do_not_authorize_gaps_bad_status_or_clock_reversal(self):
        for status,stamp in ((4,2.5),(5,2.24),(4,2.18)):
            with self.subTest(status=status,stamp=stamp):
                r=seed();r.offer(SILENCE,1.06,4);healthy_until(r,2.2);r.offer(SILENCE,2.2)
                with self.assertRaisesRegex(ValueError,'playback_discontinuity|clock_reversed'):
                    r.offer(SILENCE,stamp,status)

    def test_invalid_pcm_after_recovery_remains_fatal(self):
        r=seed();r.offer(SILENCE,1.06,4);healthy_until(r,2.2)
        with self.assertRaisesRegex(ValueError,'requires_20ms_48k_stereo'):
            r.offer(bytes(100),2.2,4)

if __name__=='__main__':unittest.main()
