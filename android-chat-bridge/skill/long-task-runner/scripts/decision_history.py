"""Inert parent-owned decision documentation. Never starts work or evaluates an agent.

Drafts are statements, not execution authority. Registry/bindings and provenance
are supplied by the existing trusted parent, not a worker-selected tool interface.
"""
import datetime
import json
import re
from pathlib import Path
import private_snapshot as store

EVENT_BYTES=4096
EVENT_LIMIT=256
JOURNAL_BYTES=1048576
RETAINED_BYTES=16*1048576
ORIGINS=('agent_statement','user_instruction','controller_receipt','reviewer_annotation')
MODES=('contemporaneous','checkpoint','retrospective')
TYPES=('decision_recorded','decision_amended','outcome_observed','review_annotation')
STATUSES=('passed','failed','inconclusive','not_run','blocked','not_applicable')
BINDINGS=('sourceIdentitySha256','apkSha256','environmentSha256')
require=store.require

def exact(value,fields,name):require(type(value) is dict and set(value)==set(fields),'invalid '+name)
def ident(x):return type(x) is str and re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_.-]{0,95}',x) is not None
def digest(x):return type(x) is str and re.fullmatch('[0-9a-f]{64}',x) is not None
def text(x,n=1000):return type(x) is str and 0<len(x)<=n and not any(ord(c)<32 and c not in '\n\t' for c in x)

def strings(xs,n=8):require(type(xs) is list and len(xs)<=n and all(text(x,400) for x in xs),'invalid bounded narrative list')
def reference(ref):
    exact(ref,('id','sha256'),'reference');require(ident(ref['id']) and digest(ref['sha256']),'invalid reference identity')
def references(refs):
    require(type(refs) is list and len(refs)<=16,'invalid reference list')
    for ref in refs:reference(ref)
    require(len({r['id'] for r in refs})==len(refs),'duplicate reference')

def scope_check(meta,scope):
    exact(scope,('projectId','runId','contractSha256'),'scope')
    require(all(meta[k]==scope[k] for k in scope),'journal scope mismatch')

def validate_draft(draft,meta):
    exact(draft,('eventType','decisionId','supersedes','payload'),'draft')
    kind=draft['eventType'];require(kind in TYPES and ident(draft['decisionId']),'invalid decision type/ID')
    require(draft['supersedes'] is None or ident(draft['supersedes']),'invalid supersession')
    p=draft['payload'];common=('criterionIds','evidenceRefs')
    if kind=='decision_recorded':
        exact(p,(*common,'title','category','problem','chosenApproach','statedRationale','rationaleAvailability','documentedAlternatives','expectedOutcome','knownTradeoffs','openQuestions'),'decision payload')
        require(text(p['title'],160) and ident(p['category']) and all(text(p[k]) for k in ('problem','chosenApproach','expectedOutcome')),'invalid decision narrative')
        require((p['rationaleAvailability']=='not_recorded' and p['statedRationale'] is None) or
                (p['rationaleAvailability']=='recorded' and text(p['statedRationale'],1000)),'invalid stated rationale')
        for k in ('documentedAlternatives','knownTradeoffs','openQuestions'):strings(p[k])
    elif kind=='decision_amended':
        exact(p,(*common,'change','statedReason'),'amendment payload')
        require(text(p['change']) and (p['statedReason'] is None or text(p['statedReason'])),'invalid amendment')
    elif kind=='outcome_observed':
        exact(p,(*common,'observation','status','application',*BINDINGS),'observation payload')
        require(text(p['observation']) and p['status'] in STATUSES and type(p['application']) is bool,'invalid observation')
        require(all(p[k] is None or digest(p[k]) for k in BINDINGS),'invalid observation binding')
    else:
        exact(p,(*common,'comment','author'),'review payload');require(text(p['comment']) and text(p['author'],128),'invalid review')
    require(type(p['criterionIds']) is list and len(p['criterionIds'])<=32 and
            all(type(x) is str and x in meta['criterionIds'] for x in p['criterionIds']) and
            len(set(p['criterionIds']))==len(p['criterionIds']),'foreign or invalid criterion')
    references(p['evidenceRefs'])
    return draft

def _safe(value):
    result=str(value).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
    for char in '\\`*_[]()#!|':result=result.replace(char,'\\'+char)
    return result.replace('\n',' ')

def current(journal):
    replaced={e['supersedes'] for e in journal['events'] if e['supersedes']}
    return [e for e in journal['events'] if e['eventType']=='decision_recorded' and e['decisionId'] not in replaced]

def _render_legacy(journal):
    lines=['# Decision history', '', 'Documentation for later review. No actions or automatic lessons are generated.',
           '', 'Project: '+_safe(journal['metadata']['projectId'])+'; run: '+_safe(journal['metadata']['runId']),
           '', 'Synthetic fixture: '+str(journal['metadata']['synthetic']).lower(), '', '## Current decisions', '']
    for e in current(journal):
        p=e['payload'];lines.append('- '+_safe(e['decisionId'])+': '+_safe(p['chosenApproach'])+' — stated reason: '+_safe(p['statedRationale'] or 'not_recorded')+' ('+_safe(e['origin'])+', '+_safe(e['captureMode'])+')')
    if not current(journal):lines.append('No material decision was recorded.')
    lines+=['','## Full chronology','']
    for e in journal['events']:
        lines+=['### '+str(e['sequence'])+' · '+_safe(e['eventType'])+' · '+_safe(e['decisionId']), '',
                'Recorded: '+e['recordedAt']+'; origin: '+_safe(e['origin'])+'; capture: '+_safe(e['captureMode'])+'.',
                'Source: '+_safe(json.dumps(e['sourceRef'],sort_keys=True))+'; supersedes: '+_safe(e['supersedes'] or 'none')+'.','']
        for key in sorted(e['payload']):
            lines.append('- '+_safe(key)+': '+_safe(json.dumps(e['payload'][key],ensure_ascii=False,sort_keys=True)))
        lines+=['','Receipt linkage: '+_safe(json.dumps(e['receiptLinkage'],sort_keys=True)),
                'Capture gaps: '+_safe(', '.join(e['captureGaps']) or 'none recorded'), '']
    lines+=['A linked statement proves what was stated, not application behavior. A matching receipt is a parent attestation, not independent truth or an agent grade.',
            'These records do not change acceptance, lifecycle, retries, cancellation or cleanup.', '']
    return '\n'.join(lines).encode()

def render(journal):
    from report_language import details, ORIGIN, MODE, STATUS, STATE_EXPLANATION
    lines=['# Decision history','','## How to read this history','','This record explains what was chosen, the reasons actually recorded, and what later checks found. An expected result is a plan; a test observation is separate evidence about what happened.','']
    if journal['metadata']['synthetic']:lines+=['This is an example using synthetic records, not evidence of a real application run.','']
    lines+=['## Current recorded choices','','These are the original choices that have not been replaced by another decision. Later amendments and observations remain in the chronology, so read them before assuming an original plan is still unchanged.','']
    for e in current(journal):
        p=e['payload'];lines+=['**'+_safe(p['title'])+'** — '+_safe(p['chosenApproach']),'',
                              'Recorded reason: '+(_safe(p['statedRationale']) if p['statedRationale'] is not None else 'No reason was recorded; it would be a guess to supply one.'),'']
    if not current(journal):lines+=['No material decision was recorded. This does not establish that no decisions were made.','']
    lines+=['## What happened, in order','','Entries appear in the order they were saved. Each one distinguishes an original choice, a later change, a test observation or a requested review comment.','']
    for e in journal['events']:
        p=e['payload'];kind=e['eventType']
        title={'decision_recorded':'Choice: '+p.get('title',''),'decision_amended':'A recorded change','outcome_observed':'A later check','review_annotation':'A later review comment'}[kind]
        lines+=['### '+str(e['sequence'])+'. '+_safe(title),'',
                'This entry comes from '+ORIGIN[e['origin']]+' and was '+MODE[e['captureMode']]+'.','']
        if kind=='decision_recorded':
            lines+=['**Problem being addressed:** '+_safe(p['problem']),'','**What was chosen:** '+_safe(p['chosenApproach']),'',
                    '**Why:** '+(_safe(p['statedRationale']) if p['statedRationale'] is not None else 'No reason was recorded. The record does not infer one from the outcome.'),'',
                    '**What was expected:** '+_safe(p['expectedOutcome'])+' This is the intended result, not a claim that a test passed.','']
            for key,label in [('documentedAlternatives','Other approaches recorded'),('knownTradeoffs','Trade-offs recorded'),('openQuestions','Questions left open')]:
                if p[key]:lines+=['**'+label+':**','']+['- '+_safe(x) for x in p[key]]+['']
            if e['supersedes']:lines+=['This replaces an earlier recorded choice; the original remains in this chronology.','']
        elif kind=='decision_amended':
            lines+=['**What changed:** '+_safe(p['change']),'','**Why:** '+(_safe(p['statedReason']) if p['statedReason'] is not None else 'No reason for this change was recorded.'),'']
        elif kind=='outcome_observed':
            matched=e['receiptLinkage']['classification']=='matched_parent_receipt'
            lines+=[('This observation matches the supervising process’s admitted test record.' if matched else 'This is an unconfirmed observation: it did not match the required test evidence.'),'',
                    '**Recorded observation:** '+_safe(p['observation']),'','**Reported result:** '+STATUS[p['status']]+'.'+(' '+STATE_EXPLANATION[p['status']] if matched else ''),'']
            if not matched:lines+=['The reported result remains a claim; this entry does not establish a pass or failure for the application.','']
        else:lines+=['This comment was added during a requested review. It does not rewrite what the agent originally said or what a test observed.','','**Comment:** '+_safe(p['comment']),'']
        if e['captureGaps']:lines+=['Some supporting information is missing for this entry. The gaps are retained in the technical record rather than filled in with a guessed explanation.','']
        lines+=details(e)
    lines+=['## What this record can establish','','A saved statement shows what was stated. A matching test record connects an observation to evidence supplied by the supervising process; it is not independent proof, a grade, or permission to change the result. Reading this history starts no work.','']
    lines+=details(journal['metadata'])
    return '\n'.join(lines).encode()

def _load(root,pointer=None):
    j,md,p=store.load(root,pointer,JOURNAL_BYTES)
    fields=('schemaVersion','metadata','events')
    if 'readableVersion' in j:
        exact(j,(*fields,'readableVersion'),'journal');require(type(j['readableVersion']) is int and j['readableVersion']==2,'invalid readable version')
    else:exact(j,fields,'journal')
    validate_metadata(j['metadata'])
    require(type(j['schemaVersion']) is int and j['schemaVersion']==1 and type(j['events']) is list and len(j['events'])<=EVENT_LIMIT,'invalid journal')
    require(p['runId']==j['metadata']['runId'],'pointer run mismatch')
    prior=None;keys=set()
    for n,event in enumerate(j['events'],1):
        require(type(event) is dict,'invalid event')
        require(event['sequence']==n and event['previousEventSha256']==prior and event['submissionKey'] not in keys,'invalid event chain')
        raw={k:v for k,v in event.items() if k!='eventSha256'}
        require(store.sha(store.encode(raw))==event['eventSha256'] and len(store.encode(event))<=EVENT_BYTES,'event integrity mismatch')
        keys.add(event['submissionKey']);prior=event['eventSha256']
    require(md==(render(j) if j.get('readableVersion')==2 else _render_legacy(j)),'history rendering mismatch')
    return j,p

def validate_metadata(metadata):
    exact(metadata,('projectId','runId','contractSha256','criterionIds','versions','synthetic'),'journal metadata')
    require(ident(metadata['projectId']) and ident(metadata['runId']) and digest(metadata['contractSha256']),'invalid registration')
    require(type(metadata['criterionIds']) is list and 1<=len(metadata['criterionIds'])<=32 and all(ident(x) for x in metadata['criterionIds']) and len(set(metadata['criterionIds']))==len(metadata['criterionIds']),'invalid criteria')
    exact(metadata['versions'],('skillSha256','modelDigest','toolchainSha256'),'version pins')
    require(all(x is None or digest(x) for x in metadata['versions'].values()) and type(metadata['synthetic']) is bool,'invalid pins')

def initialize(root,metadata):
    validate_metadata(metadata)
    with store.locked(root) as root:
        if (root/'latest.json').exists():
            old,p=_load(root);require(old['metadata']==metadata,'journal registration conflict');return p
        j={'schemaVersion':1,'readableVersion':2,'metadata':metadata,'events':[]}
        return store.pair(root,store.encode(j),render(j),metadata['runId'],EVENT_LIMIT+1,JOURNAL_BYTES,RETAINED_BYTES)

def _partial(root,j,reason):
    path=root/'coverage.json';gaps=[]
    if path.exists():gaps=json.loads(store.read_file(path,4096))['reasons']
    if reason not in gaps and len(gaps)<8:gaps.append(reason)
    value={'status':'partial','lastSequence':len(j['events']),'reasons':gaps}
    store.atomic(root,'coverage.json',store.encode(value));return value

def record(root,scope,draft,*,attempt_id,submission_id,ordinal,origin,capture_mode,source_ref,registry,expected_binding,review_requested=False):
    """The trusted parent supplies provenance/registry; drafts cannot set them."""
    require(attempt_id is None or ident(attempt_id),'invalid attempt')
    require(ident(submission_id) and type(ordinal) is int and 0<=ordinal<=65535,'invalid submission identity')
    require(origin in ORIGINS and capture_mode in MODES,'invalid provenance')
    if source_ref is not None:reference(source_ref);require(source_ref['id']==submission_id,'submission/source mismatch')
    require(type(registry) is dict and len(registry)<=128,'invalid registry')
    exact(expected_binding,BINDINGS,'expected binding');require(all(x is None or digest(x) for x in expected_binding.values()),'invalid expected binding')
    with store.locked(root) as root:
        j,pointer=_load(root);meta=j['metadata'];scope_check(meta,scope);validate_draft(draft,meta)
        require((draft['eventType']=='review_annotation')==(origin=='reviewer_annotation'),'review provenance mismatch')
        if origin=='reviewer_annotation':require(review_requested is True,'review was not requested')
        key=store.sha(store.encode([scope,attempt_id,submission_id,ordinal]))
        submission={'draft':draft,'attemptId':attempt_id,'origin':origin,'captureMode':capture_mode,'sourceRef':source_ref}
        fingerprint=store.sha(store.encode(submission))
        for e in j['events']:
            if e['submissionKey']==key:
                require(e['submissionSha256']==fingerprint,'conflicting repeated submission')
                return {'status':'recorded','event':e,'pointer':pointer,'duplicate':True}
        decisions={e['decisionId']:e for e in j['events'] if e['eventType']=='decision_recorded'}
        if draft['eventType']=='decision_recorded':
            require(draft['decisionId'] not in decisions,'decision ID already exists')
            if draft['supersedes'] is not None:
                require(draft['supersedes']!=draft['decisionId'] and draft['supersedes'] in {e['decisionId'] for e in current(j)},'invalid or noncurrent supersession')
        else:require(draft['decisionId'] in decisions and draft['supersedes'] is None,'unknown decision or invalid amendment')
        refs=draft['payload']['evidenceRefs'];checks=[];matched=False
        for ref in refs:
            entry=registry.get(ref['id']);issues=[]
            if type(entry) is not dict or entry.get('sha256')!=ref['sha256']:issues.append('missing_or_digest_mismatched_receipt')
            else:
                if entry.get('synthetic') is not False:issues.append('synthetic_or_unknown_evidence')
                if entry.get('kind')!='controller_receipt':issues.append('statement_not_observation')
                if draft['eventType']=='outcome_observed':
                    payload=draft['payload']
                    if entry.get('status')!=payload['status']:issues.append('receipt_status_mismatch')
                    if payload['application'] and any(not digest(payload[k]) or payload[k]!=expected_binding[k] or payload[k]!=entry.get(k) for k in BINDINGS):issues.append('source_apk_environment_mismatch')
            checks.append({'id':ref['id'],'status':'unverified' if issues else 'matched_parent_receipt','issues':issues})
            matched=matched or not issues
        gaps=[]
        if source_ref is None:gaps.append('source_not_recorded')
        else:
            source=registry.get(source_ref['id']);expected_kind={'agent_statement':'agent_message','user_instruction':'user_message','controller_receipt':'controller_receipt','reviewer_annotation':'review_message'}[origin]
            if type(source) is not dict or source.get('sha256')!=source_ref['sha256'] or source.get('kind')!=expected_kind:gaps.append('source_reference_unconfirmed')
        if draft['eventType']=='decision_recorded' and draft['payload']['statedRationale'] is None:gaps.append('rationale_not_recorded')
        event={'schemaVersion':1,**scope,'attemptId':attempt_id,'eventId':'event-'+key[:32],
               'sequence':len(j['events'])+1,'recordedAt':datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00','Z'),
               **draft,'origin':origin,'captureMode':capture_mode,'sourceRef':source_ref,
               'submissionKey':key,'submissionSha256':fingerprint,'previousEventSha256':j['events'][-1]['eventSha256'] if j['events'] else None,
               'receiptLinkage':{'classification':'matched_parent_receipt' if origin=='controller_receipt' and draft['eventType']=='outcome_observed' and matched and all(x['status']=='matched_parent_receipt' for x in checks) and not gaps else 'claim_or_unverified','references':checks},'captureGaps':gaps}
        event['eventSha256']=store.sha(store.encode(event))
        if len(store.encode(event))>EVENT_BYTES:return _partial(root,j,'event_byte_limit')
        if len(j['events'])>=EVENT_LIMIT:return _partial(root,j,'event_count_limit')
        candidate={**j,'readableVersion':2,'events':j['events']+[event]};raw=store.encode(candidate)
        if len(raw)>JOURNAL_BYTES:return _partial(root,j,'journal_byte_limit')
        try:pointer=store.pair(root,raw,render(candidate),meta['runId'],EVENT_LIMIT+1,JOURNAL_BYTES,RETAINED_BYTES)
        except ValueError as error:
            if str(error)=='snapshot retention budget reached':return _partial(root,j,'snapshot_retention_limit')
            if str(error)=='snapshot byte budget reached':return _partial(root,j,'retained_byte_limit')
            raise
        return {'status':'recorded','event':event,'pointer':pointer,'duplicate':False}

def descriptor(root,scope):
    with store.locked(root) as root:
        j,p=_load(root);scope_check(j['metadata'],scope)
        coverage=json.loads(store.read_file(root/'coverage.json',4096)) if (root/'coverage.json').exists() else None
        gaps=any(e['captureGaps'] for e in j['events'])
        return {'extensionVersion':1,**scope,'availability':'partial' if coverage or gaps else 'recorded',
                'reason':'; '.join(coverage['reasons']) if coverage else 'Recorded gaps are shown in the chronology.' if gaps else 'Recorded entries available; this is not a claim of complete decision capture.',
                'headSequence':len(j['events']),'headSha256':j['events'][-1]['eventSha256'] if j['events'] else None,'pointer':p}

def projection(report_root,ref,run_id,contract_sha):
    """Load only a fixed sidecar's pinned revision, never a model-selected path."""
    exact(ref,('extensionVersion','projectId','runId','contractSha256','availability','reason','headSequence','headSha256','pointer'),'history extension')
    require(type(ref['extensionVersion']) is int and ref['extensionVersion']==1 and ref['runId']==run_id and ref['contractSha256']==contract_sha and ident(ref['projectId']),'history report binding mismatch')
    require(ref['availability'] in ('recorded','partial','unavailable','not_requested') and text(ref['reason'],1000),'invalid history availability')
    require(len(store.encode(ref))<=4096,'history projection input too large')
    if ref['availability'] in ('unavailable','not_requested'):
        require(ref['pointer'] is None and ref['headSequence'] is None and ref['headSha256'] is None,'unavailable history cannot claim a head')
        return {**ref,'summary':[],'readablePath':None}
    require(type(ref['pointer']) is dict,'recorded history requires a pinned pointer')
    with store.locked(Path(report_root)/'decision-history') as root:
        j,p=_load(root,ref['pointer']);scope_check(j['metadata'],{k:ref[k] for k in ('projectId','runId','contractSha256')})
        require(type(ref['headSequence']) is int and ref['headSequence']==len(j['events']) and ref['headSha256']==(j['events'][-1]['eventSha256'] if j['events'] else None),'history head mismatch')
        summary=[{'decisionId':e['decisionId'],'choice':e['payload']['chosenApproach'][:300],
                  'statedReason':(e['payload']['statedRationale'] or 'not_recorded')[:300],
                  'origin':e['origin'],'captureMode':e['captureMode']} for e in current(j)[:8]]
        return {**ref,'synthetic':j['metadata']['synthetic'],'summary':summary,
                'summaryTruncated':len(current(j))>8,'readablePath':'../decision-history/'+p['snapshot']+'/report.md'}
