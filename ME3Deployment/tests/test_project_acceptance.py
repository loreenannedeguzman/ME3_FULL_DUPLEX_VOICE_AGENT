"""Adversarial fixtures never edit the actual project or its evidence."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('project_acceptance', Path(__file__).resolve().parents[1] / 'tools/project_acceptance.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


class Acceptance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.put('code.py', 'print(1)\n')
        hashes = {'code.py': control.sha((self.root / 'code.py').read_bytes())}
        self.revision = control.sha(json.dumps(hashes, sort_keys=True, separators=(',', ':')).encode())
        self.put('evidence/assistant/candidate-source.json', {'revision': self.revision, 'source_hashes': hashes})
        self.put('source.txt', 'Do the actual task.\n\nVerify the outcome.\n')
        units = control.source_units((self.root / 'source.txt').read_text(), 'paragraphs')
        self.board = {'overall_status': 'PASS', 'candidate': {'source_revision': self.revision, 'qualified_full_scope': True},
                      'work_blocks': [{'id': 'WB-00', 'status': 'ACCEPTED', 'subblocks': []}],
                      'gates': [{'status': 'ACCEPTED'}]}
        self.put('raw.json', {'actual': 'result'})
        self.receipt = {'source_revision': self.revision, 'kind': 'integration', 'checks': [{'id': 'execute', 'status': 'PASS'}],
                        'artifacts': [{'path': 'raw.json', 'sha256': control.sha((self.root / 'raw.json').read_bytes())}]}
        self.put('receipt.json', self.receipt)
        ev = {'path': 'receipt.json', 'sha256': control.sha((self.root / 'receipt.json').read_bytes()),
              'revision': self.revision, 'scope': 'actual finite task', 'qualifying': True,
              'kind': 'integration', 'checks': ['execute'], 'criteria': ['C1']}
        self.req = {'id': 'R1', 'statement': 'Do and verify the task', 'rationale': 'Required outcome', 'outcome': 'Actual result',
                    'classification': 'assignment_transcription', 'mandatory': True,
                    'implementation_status': 'TESTED', 'verification_status': 'VERIFIED',
                    'history': [{'at': '2026-10-25T00:00:00+08:00', 'state': 'VERIFIED', 'reason': 'Initial imported acceptance', 'evidence': ['receipt.json']}],
                    'source_refs': [{'source': 'S', 'unit': u} for u in units],
                    'acceptance': [{'id': 'C1', 'statement': 'Execute and verify', 'kinds': ['integration']}],
                    'needs': [], 'work_blocks': ['WB-00'], 'implementation': ['code.py'], 'tests': ['code.py'],
                    'evidence': [ev], 'gaps': [], 'blockers': [], 'dependencies': []}
        self.data = {'schema_version': 1, 'candidate_revision': self.revision, 'reported_status': 'PASS', 'unresolved_sources': [],
                     'sources': [{'id': 'S', 'path': 'source.txt', 'sha256': control.sha((self.root / 'source.txt').read_bytes()),
                                  'extractor': 'paragraphs', 'units': {k: control.sha(v.encode()) for k, v in units.items()}}],
                     'requirements': [self.req]}

    def put(self, path, value):
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(value if isinstance(value, str) else json.dumps(value), encoding='utf-8')

    def check(self):
        self.put('PROJECT_BOARD.json', self.board)
        self.put('PROJECT_STATUS.md', 'Acceptance-control status: ' + self.board['overall_status'] + '\n')
        self.put('docs/requirements_registry.json', self.data)
        return control.validate(self.root)

    def reject(self, fragment):
        result = self.check()
        self.assertEqual(result['status'], 'INVALID', result)
        self.assertIn(fragment, ' '.join(result['errors']), result)

    def refresh_receipt(self):
        self.put('receipt.json', self.receipt)
        self.req['evidence'][0]['sha256'] = control.sha((self.root / 'receipt.json').read_bytes())

    def test_consistent_passing_state(self):
        result = self.check()
        self.assertEqual(result['status'], 'PASS', result)
        self.assertTrue(result['integrity_valid'])
        self.assertEqual(result['source_coverage'], {'covered': 2, 'total': 2})

    def test_missing_evidence(self):
        (self.root / 'receipt.json').unlink()
        self.reject('missing/outside file')

    def test_unverified_mandatory_requirement_cannot_pass(self):
        self.req['verification_status'] = 'TESTED'
        self.req['history'][0]['state'] = 'TESTED'
        self.req['gaps'] = ['Real acceptance still outstanding']
        self.reject('claims completion')

    def test_skipped_required_check(self):
        self.receipt['checks'][0]['status'] = 'SKIP'
        self.refresh_receipt()
        self.reject('skipped required test')

    def test_failed_required_check(self):
        self.receipt['checks'][0]['status'] = 'FAIL'
        self.refresh_receipt()
        self.reject('skipped required test')

    def test_missing_check_not_inferred_pass(self):
        self.receipt['checks'] = []
        self.refresh_receipt()
        self.reject('skipped required test')

    def test_stale_evidence_bytes(self):
        self.put('receipt.json', '{}')
        self.reject('stale evidence')

    def test_stale_evidence_revision(self):
        self.receipt['source_revision'] = 'a' * 64
        self.refresh_receipt()
        self.reject('stale candidate')

    def test_changed_source_needs_revalidation(self):
        self.put('code.py', 'print(2)\n')
        self.reject('source changed; revalidate')

    def test_new_source_file_needs_manifest_update(self):
        self.put('new.py', 'print(2)')
        self.reject('new source files omitted')

    def test_contradictory_board_revision(self):
        self.board['candidate']['source_revision'] = 'old'
        self.reject('board/candidate revision')

    def test_empty_denominator(self):
        self.data['requirements'] = []
        self.reject('empty source/requirement denominator')

    def test_removed_source_clause(self):
        self.req['source_refs'].pop()
        self.reject('incomplete source coverage')

    def test_changed_assignment(self):
        self.put('source.txt', 'Changed wording')
        self.reject('source changed')

    def test_missing_acceptance(self):
        self.req['acceptance'] = []
        self.reject('no acceptance criteria')

    def test_optional_flag_cannot_remove_obligation(self):
        self.req['mandatory'] = False
        self.reject('cannot be made optional')

    def test_unjustified_exception(self):
        self.req['verification_status'] = 'NOT_APPLICABLE'
        self.req['history'][0]['state'] = 'NOT_APPLICABLE'
        self.req['exception'] = {}
        self.reject('unjustified exception')

    def test_live_requirement_cannot_use_unit_test(self):
        self.req['needs'] = ['pi', 'live_speech']
        self.reject('missing Pi/live')

    def test_fixture_does_not_become_hosted_or_human_evidence(self):
        self.req['needs'] = ['external', 'human']
        self.reject('missing Pi/live')

    def test_raw_result_cannot_disappear(self):
        self.put('raw.json', 'changed')
        self.reject('raw artifact changed')

    def test_duplicate_check_cannot_mask_failure(self):
        self.receipt['checks'] = [{'id': 'execute', 'status': 'FAIL'}, {'id': 'execute', 'status': 'PASS'}]
        self.refresh_receipt()
        self.reject('duplicate check')

    def test_invalid_promotion_and_history_contradiction(self):
        self.req['history'].insert(0, {'at': 'earlier', 'state': 'NOT_STARTED', 'reason': 'baseline'})
        self.reject('invalid transition')

    def test_unresolved_original_handout_blocks_full_acceptance(self):
        self.data['unresolved_sources'] = ['Independent original handout missing']
        self.reject('claims completion')

    def test_pending_work_board_cannot_be_hidden(self):
        self.board['work_blocks'][0]['subblocks'] = [{'id': 'WB-00.1', 'status': 'PLANNED'}]
        self.reject('claims completion')

    def test_full_scope_gate_required(self):
        self.board['candidate']['qualified_full_scope'] = False
        self.reject('lacks full qualification')

    def test_partial_state_is_integrity_valid_but_not_pass(self):
        self.req['verification_status'] = self.req['history'][0]['state'] = 'BLOCKED'
        self.req['blockers'] = ['Hardware unavailable']
        self.data['reported_status'] = self.board['overall_status'] = 'PARTIAL_PASS'
        result = self.check()
        self.assertTrue(result['integrity_valid'], result)
        self.assertEqual(result['status'], 'PARTIAL_PASS')
        self.assertIn('R1', result['outstanding'])

    def test_paths_cannot_escape_project(self):
        self.req['evidence'][0]['path'] = '../outside.json'
        self.reject('unsafe path')

    def test_duplicate_json_keys_fail_closed(self):
        self.put('PROJECT_BOARD.json', '{"overall_status":"PASS","overall_status":"FAIL"}')
        self.put('docs/requirements_registry.json', self.data)
        self.assertIn('duplicate JSON', str(control.validate(self.root)['errors']))


if __name__ == '__main__':
    unittest.main()
