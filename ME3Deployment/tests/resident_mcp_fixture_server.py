"""Actual stdio MCP, synthetic worker only. No ASR/hosted synthesis calls."""
from pathlib import Path
import speech_mcp.piper_resident as resident
resident.WORKER=Path(__file__).with_name('resident_fixture_worker.py')
from speech_mcp.server import main
if __name__=='__main__':main()
