"""Fail closed on candidate or governing-policy changes before services open."""
import hashlib,json
from pathlib import Path
from onit_candidate.protocol import CandidateError
def verify_candidate(root):
    root=Path(root);package=root/'onit_candidate'
    policy_raw=(root/'evidence/governance/onit-candidate-20261026.json').read_bytes()
    if hashlib.sha256(policy_raw).hexdigest()!='c13af1252e22a86d0941713da9307458ccf87a3eb8028d01bbd905385ddfe65b':raise CandidateError('candidate_owner_policy_changed')
    policy=json.loads(policy_raw)
    if policy['candidate_engine']['role']!='sole candidate planner/task loop':raise CandidateError('candidate_owner_policy_missing')
    full=(root/'evidence/governance/onit-full-capability-20261030.json').read_bytes()
    if hashlib.sha256(full).hexdigest()!='a3f0d51d84d7c71cf2fbf2b35fd20fa59571ec2b5a2bf1e35fcc45f154e9a0e9':raise CandidateError('full_Onit_owner_policy_changed')
    if json.loads(full)['production_planner']!='Full Onit only; Router/Workflow remains historical and inactive.':raise CandidateError('full_Onit_single_planner_required')
    manifest=json.loads((package/'candidate-files.json').read_bytes())
    actual={p.name for p in package.glob('*.py')}|{'app.js'}
    if set(manifest)!=actual:raise CandidateError('candidate_source_inventory_changed')
    for name,h in manifest.items():
        p=package/name
        if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=h:raise CandidateError('candidate_source_changed')
    return manifest
