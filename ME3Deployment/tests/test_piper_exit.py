"""Deterministic reproduction of the early-worker-exit stdin race."""
import unittest
from unittest.mock import patch
class EarlyExitTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_worker_reports_exit_error_even_when_pipe_resets(self):
        from test_piper import WorkerTests
        with patch('asyncio.StreamWriter.drain',side_effect=ConnectionResetError('injected early exit')):await WorkerTests().exercise('error')
    async def test_zero_exit_still_validates_malformed_pcm_after_pipe_reset(self):
        from test_piper import WorkerTests
        with patch('asyncio.StreamWriter.drain',side_effect=ConnectionResetError('injected early exit')):await WorkerTests().exercise('odd')
