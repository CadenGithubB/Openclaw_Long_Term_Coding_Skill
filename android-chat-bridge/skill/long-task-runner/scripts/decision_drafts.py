#!/usr/bin/env python3
"""Bounded worker draft validation and explicit parent capture; no inference or execution."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import decision_history as history
import private_snapshot as store
MAX_BYTES=16384
MAX_DRAFTS=4
MAX_DRAFT_BYTES=2048

def pairs(items):
    out={}
    for key,value in items:
        if key in out:raise ValueError('duplicate JSON key')
        out[key]=value
    return out

def validate(raw,criterion_ids):
    history.require(type(raw) is bytes and len(raw)<=MAX_BYTES,'draft input exceeds 16 KiB')
    history.require(type(criterion_ids) is list and 0<len(criterion_ids)<=32 and all(history.ident(x) for x in criterion_ids) and len(set(criterion_ids))==len(criterion_ids),'invalid admitted criteria')
    drafts=json.loads(raw.decode('utf-8'),object_pairs_hook=pairs)
    history.require(type(drafts) is list and len(drafts)<=MAX_DRAFTS,'expected array of at most four drafts')
    for draft in drafts:
        history.validate_draft(draft,{'criterionIds':criterion_ids})
        history.require(draft['eventType'] in ('decision_recorded','decision_amended'),'worker may submit decisions or amendments only')
        history.require(not draft['payload']['evidenceRefs'],'worker evidenceRefs must be empty; parent admits receipts')
        history.require(len(store.encode(draft))<=MAX_DRAFT_BYTES,'draft exceeds 2 KiB; keep the stated reason brief')
    history.require(len({x['decisionId'] for x in drafts})==len(drafts),'duplicate decision IDs in batch')
    return drafts

def capture(root,scope,raw,*,criterion_ids,attempt_id,submission_id,capture_mode='checkpoint'):
    """Parent supplies exact retained UTF-8 draft bytes, scope and source ID.

    Caller retains these bytes under submission_id before invoking. Their SHA is
    the source reference. They are agent claims, never controller observations.
    Validation runs again here; worker validator output is not authoritative.
    Filesystem errors propagate so the caller can report unavailable coverage.
    """
    with store.locked(root):
        journal,_=history._load(root)
        history.scope_check(journal["metadata"],scope)
    try:drafts=validate(raw,criterion_ids)
    except (ValueError,UnicodeError,TypeError,RecursionError) as error:
        with store.locked(root):
            journal,_=history._load(root)
            history.scope_check(journal['metadata'],scope)
            history._partial(root,journal,'worker_draft_invalid')
        return {'status':'partial','recorded':0,'error':str(error)[:256]}
    ref={'id':submission_id,'sha256':hashlib.sha256(raw).hexdigest()}
    registry={submission_id:{'kind':'agent_message','sha256':ref['sha256'],'synthetic':False}}
    results=[]
    try:
        for i,draft in enumerate(drafts):
            result=history.record(root,scope,draft,attempt_id=attempt_id,submission_id=submission_id,ordinal=i,origin='agent_statement',capture_mode=capture_mode,source_ref=ref,registry=registry,expected_binding={k:None for k in history.BINDINGS})
            if result['status']!='recorded':return {'status':'partial','recorded':len(results),'reason':result}
            results.append(result)
    except (ValueError,TypeError) as error:
        with store.locked(root):
            journal,_=history._load(root);history._partial(root,journal,'worker_draft_admission_failed')
        return {'status':'partial','recorded':len(results),'error':str(error)[:256]}
    return {'status':'recorded','recorded':len(results),'sourceRef':ref,'events':[r['event']['eventId'] for r in results]}

def main():
    parser=argparse.ArgumentParser(description='Validate worker decision drafts from stdin; writes no files and invokes no model.')
    parser.add_argument('--criterion',action='append',required=True)
    args=parser.parse_args()
    try:
        raw=sys.stdin.buffer.read(MAX_BYTES+1);drafts=validate(raw,args.criterion)
        value={'valid':True,'drafts':len(drafts),'sha256':hashlib.sha256(raw).hexdigest()};code=0
    except (ValueError,UnicodeError,TypeError,RecursionError) as error:
        value={'valid':False,'error':str(error)[:256]};code=2
    print(json.dumps(value,sort_keys=True));return code
if __name__=='__main__':raise SystemExit(main())
