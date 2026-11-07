"""Fail-closed requirement acceptance. Stdlib only; never starts providers/audio/tools.

requirements_registry.json is the machine form of docs/requirements.md, not a
second work board. Source coverage detects removed text/clauses; human review is
still necessary to establish faithful interpretation of an assignment.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

STATES = {'NOT_STARTED', 'IN_PROGRESS', 'IMPLEMENTED', 'TESTED', 'VERIFIED',
          'BLOCKED', 'NOT_APPLICABLE'}
KINDS = {'automated', 'integration', 'pi', 'live_speech', 'external',
         'measurement', 'human', 'inspection'}
TRANSITIONS = {
    'NOT_STARTED': {'IN_PROGRESS', 'BLOCKED', 'NOT_APPLICABLE'},
    'IN_PROGRESS': {'IMPLEMENTED', 'TESTED', 'BLOCKED'},
    'IMPLEMENTED': {'TESTED', 'IN_PROGRESS', 'BLOCKED'},
    'TESTED': {'VERIFIED', 'IN_PROGRESS', 'BLOCKED'},
    'VERIFIED': {'IN_PROGRESS', 'BLOCKED'},
    'BLOCKED': {'IN_PROGRESS', 'NOT_APPLICABLE'},
    'NOT_APPLICABLE': {'IN_PROGRESS'},
}


def source_inventory(root):
    folders = ['speech_mcp', 'task_mcp', 'me3_agent', 'me3_assistant', 'me3_completion',
               'me3_core', 'me3_m7', 'benchmark_agent', 'benchmark_final', 'benchmark_m6',
               'evaluation_normal', 'tools', 'tests', 'scripts', 'config']
    paths = list(root.glob('*.py')) + list(root.glob('requirements*.txt'))
    for name in folders:
        folder = root / name
        if folder.is_dir():
            paths.extend(p for p in folder.rglob('*') if p.is_file() and p.suffix in {'.py', '.sh', '.c', '.json'})
    return {p.relative_to(root).as_posix() for p in paths if '__pycache__' not in p.parts}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('duplicate JSON key: ' + key)
            value[key] = item
        return value
    return json.loads(path.read_bytes(), object_pairs_hook=unique)


def safe(root, name):
    if not isinstance(name, str) or not name or '\\' in name:
        raise ValueError('invalid relative path')
    p = Path(name)
    if p.is_absolute() or ':' in name or any(x in {'..', '.'} for x in name.split('/')):
        raise ValueError('unsafe path: ' + name)
    p = root / p
    if not p.resolve().is_relative_to(root.resolve()) or not p.is_file():
        raise ValueError('missing/outside file: ' + name)
    return p


def source_units(text, extractor):
    """Deterministic source clauses; blank-line paragraphs preserve exact wording."""
    text = text.replace('\r\n', '\n')
    if extractor == 'assignment':
        start = text.index('> ME 3 — Full-Duplex Voice AI Agent')
        end = text.index('\nTreat the assignment wording', start)
        lines = [x[2:] for x in text[start:end].splitlines() if x.startswith('> ')]
        clauses = [x for x in lines if x.startswith(('The aim', 'The expected')) or re.match(r'\d+\.', x)]
        if len(clauses) != 8:
            raise ValueError('assignment extraction changed')
        units = {f'A{i}': clause for i, clause in enumerate(clauses)}
        for n in (8, 9):
            start = text.index(f'**R{n} — ')
            end = text.index('**R9 — ', start) if n == 8 else text.index('\nCreate a requirements traceability', start)
            units[f'I{n}'] = text[start:end].strip()
        return units
    if extractor != 'paragraphs':
        raise ValueError('unknown source extractor')
    return {f'P{i:03}': part.strip() for i, part in enumerate(
        re.split(r'\n\s*\n', text.strip()), 1) if part.strip()}


def validate(root, registry=None):
    root = Path(root).resolve()
    errors, pending, rows = [], [], []
    def need(condition, message):
        if not condition:
            errors.append(message)
        return condition
    try:
        data = read(safe(root, registry or 'docs/requirements_registry.json'))
        need(data['schema_version'] == 1, 'unsupported schema')
        need(bool(data['sources']) and bool(data['requirements']), 'empty source/requirement denominator')
        board = read(safe(root, 'PROJECT_BOARD.json'))
        candidate = read(safe(root, 'evidence/assistant/candidate-source.json'))
        hashes = candidate['source_hashes']
        need(bool(hashes), 'empty candidate manifest')
        need(source_inventory(root) <= set(hashes), 'new source files omitted from candidate manifest')
        need(candidate['revision'] == sha(json.dumps(hashes, sort_keys=True, separators=(',', ':')).encode()), 'candidate digest mismatch')
        changed = [name for name, digest in hashes.items() if sha(safe(root, name).read_bytes()) != digest]
        need(not changed, 'candidate source changed; revalidate: ' + ', '.join(changed))
        need(board['candidate']['source_revision'] == candidate['revision'], 'board/candidate revision contradiction')
        need(data['candidate_revision'] == candidate['revision'], 'registry/candidate revision contradiction')
        status_text = safe(root, 'PROJECT_STATUS.md').read_text(encoding='utf-8')
        claims = re.findall(r'^Acceptance-control status: (\S+)', status_text, flags=re.M)
        need(bool(claims) and claims[-1] == board['overall_status'] == data['reported_status'], 'status record/board/registry contradiction')
        units = {}
        for source in data['sources']:
            name = source['id']
            need(name not in units, 'duplicate source: ' + name)
            raw = safe(root, source['path']).read_bytes()
            need(sha(raw) == source['sha256'], 'source changed: ' + name)
            actual = source_units(raw.decode('utf-8-sig'), source['extractor'])
            need(source['units'] == {k: sha(v.encode()) for k, v in actual.items()}, 'source clause index changed: ' + name)
            units[name] = actual
        expected = {(s, u) for s, group in units.items() for u in group}
        covered, ids = set(), set()
        nodes = {n['id']: n for b in board['work_blocks'] for n in [b, *b['subblocks']]}
        evidence_cache = {}
        for req in data['requirements']:
            rid = req['id']
            need(rid not in ids, 'duplicate requirement: ' + rid)
            ids.add(rid)
            prefix = rid + ': '
            for field in ['statement', 'rationale', 'outcome', 'implementation_status', 'verification_status']:
                need(bool(req.get(field)), prefix + 'missing ' + field)
            need(req['classification'] in {'assignment_transcription', 'derived', 'internal'}, prefix + 'invalid authority')
            need(type(req['mandatory']) is bool, prefix + 'mandatory must be boolean')
            need(req['mandatory'] is True, prefix + 'source obligations cannot be made optional; use an authorized exception')
            state = req['verification_status']
            need(state in STATES, prefix + 'invalid state')
            need(req['implementation_status'] in STATES - {'VERIFIED'}, prefix + 'invalid implementation state')
            history = req['history']
            need(bool(history), prefix + 'missing transition history')
            last = None
            for event in history:
                need(bool(event.get('reason')) and bool(event.get('at')), prefix + 'undocumented transition')
                target = event['state']
                need(target in STATES, prefix + 'invalid historic state')
                if last and target != last:
                    need(target in TRANSITIONS.get(last, set()), prefix + 'invalid transition ' + last + ' -> ' + target)
                if target == 'VERIFIED':
                    need(bool(event.get('evidence')), prefix + 'verification transition without evidence')
                last = target
            need(last == state, prefix + 'history/current contradiction')
            refs = req['source_refs']
            need(bool(refs), prefix + 'no authoritative source')
            for ref in refs:
                pair = (ref['source'], ref['unit'])
                need(pair in expected, prefix + 'unknown source clause')
                covered.add(pair)
            need(bool(req['acceptance']), prefix + 'no acceptance criteria')
            criterion_ids = {c['id'] for c in req['acceptance']}
            need(len(criterion_ids) == len(req['acceptance']), prefix + 'duplicate criterion')
            for criterion in req['acceptance']:
                need(bool(criterion['statement']) and bool(criterion['kinds']), prefix + 'empty criterion')
                need(set(criterion['kinds']) <= KINDS, prefix + 'invalid evidence kind')
            need(set(req['needs']) <= KINDS, prefix + 'invalid physical/service flags')
            need(bool(req['work_blocks']) and all(b in nodes for b in req['work_blocks']), prefix + 'unknown work block')
            for path in req['implementation'] + req['tests']:
                safe(root, path)
            bindings = {c: set() for c in criterion_ids}
            for ev in req['evidence']:
                path = ev['path']
                raw = safe(root, path).read_bytes()
                need(sha(raw) == ev['sha256'], prefix + 'stale evidence: ' + path)
                need(bool(ev['scope']) and bool(ev['revision']), prefix + 'evidence lacks scope/revision')
                if not ev['qualifying']:
                    continue
                need(ev['kind'] in KINDS, prefix + 'invalid evidence kind')
                need(bool(ev['checks']), prefix + 'no executed check IDs')
                if path not in evidence_cache:
                    evidence_cache[path] = read(safe(root, path))
                receipt = evidence_cache[path]
                need(receipt['source_revision'] == candidate['revision'] == ev['revision'], prefix + 'stale candidate evidence')
                need(receipt['kind'] == ev['kind'], prefix + 'receipt kind contradiction')
                result_checks = {c['id']: c['status'] for c in receipt['checks']}
                need(len(result_checks) == len(receipt['checks']), prefix + 'duplicate check IDs')
                valid = need(all(result_checks.get(c) == 'PASS' for c in ev['checks']), prefix + 'missing/failed/skipped required test')
                need(bool(receipt['artifacts']), prefix + 'receipt without raw artifacts')
                for artifact in receipt['artifacts']:
                    need(sha(safe(root, artifact['path']).read_bytes()) == artifact['sha256'], prefix + 'raw artifact changed')
                for c in ev['criteria']:
                    need(c in bindings, prefix + 'unknown evidence criterion')
                    if valid and c in bindings:
                        bindings[c].add(ev['kind'])
            if state == 'VERIFIED':
                need(req['implementation_status'] == 'TESTED', prefix + 'verified without tested implementation')
                need(not req['gaps'] and not req['blockers'], prefix + 'verified with unresolved gap/blocker')
                for c in req['acceptance']:
                    need(set(c['kinds']) <= bindings[c['id']], prefix + 'criterion not evidenced: ' + c['id'])
                provided = set().union(*bindings.values()) if bindings else set()
                need(set(req['needs']) <= provided, prefix + 'missing Pi/live/service/measurement/human evidence')
            if state == 'NOT_APPLICABLE':
                ex = req.get('exception', {})
                need(bool(ex.get('rationale')) and bool(ex.get('authority_path')), prefix + 'unjustified exception')
                safe(root, ex['authority_path'])
            if state == 'BLOCKED':
                need(bool(req['blockers']), prefix + 'blocked without prerequisite')
            if req['mandatory'] and state not in {'VERIFIED', 'NOT_APPLICABLE'}:
                need(bool(req['gaps']) or bool(req['blockers']), prefix + 'unverified without outstanding check')
                pending.append(rid)
            rows.append({'id': rid, 'state': state, 'implementation': req['implementation_status'],
                         'mandatory': req['mandatory'], 'gaps': req['gaps'], 'blockers': req['blockers']})
        for req in data['requirements']:
            need(set(req['dependencies']) <= ids, req['id'] + ': unknown requirement dependency')
        need(covered == expected, 'incomplete source coverage: ' + str(sorted(expected - covered)))
        for missing in data['unresolved_sources']:
            pending.append('SOURCE: ' + missing)
        for node in nodes.values():
            if node['status'] in {'PLANNED', 'READY', 'IN_PROGRESS', 'PARTIAL', 'WAITING_PREREQUISITE', 'DEFERRED_BY_USER', 'FAILED_ACCEPTANCE'}:
                # Work nodes remain visible as acceptance obligations; a small registry cannot waive them.
                pending.append(node['id'])
        need(not (board['overall_status'] in {'PASS', '100/100'} and pending), 'board claims completion with unmet mandatory conditions')
        need(not (data['reported_status'] in {'PASS', '100/100'} and pending), 'registry claims completion with unmet mandatory conditions')
        if not pending:
            need(all(g['status'] == 'ACCEPTED' for g in board['gates']), 'incomplete final gates')
            need(board['candidate'].get('qualified_full_scope') is True, 'candidate lacks full qualification')
            need(board['overall_status'] == 'PASS', 'board has not accepted final completion')
        status = 'INVALID' if errors else ('PARTIAL_PASS' if pending else 'PASS')
        need(data['reported_status'] == status or status == 'INVALID', 'reported registry status contradicts evidence')
        return {'status': 'INVALID' if errors else status, 'integrity_valid': not errors,
                'source_coverage': {'covered': len(covered & expected), 'total': len(expected)},
                'requirement_counts': dict(Counter(r['state'] for r in rows)),
                'implementation_counts': dict(Counter(r['implementation'] for r in rows)),
                'requirements': rows, 'outstanding': pending, 'errors': errors,
                'candidate_revision': candidate['revision'], 'provider_calls': 0}
    except (KeyError, TypeError, ValueError, OSError, UnicodeError) as exc:
        return {'status': 'INVALID', 'integrity_valid': False, 'errors': errors + [str(exc)],
                'requirements': rows, 'outstanding': pending, 'provider_calls': 0}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument('--integrity-only', action='store_true', help='Allow incomplete acceptance for development; output retains PARTIAL_PASS')
    a = p.parse_args()
    result = validate(a.root)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['integrity_valid'] and (a.integrity_only or result['status'] == 'PASS') else 1)


if __name__ == '__main__':
    main()
