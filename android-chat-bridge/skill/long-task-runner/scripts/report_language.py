"""Presentation helpers only: no inference, evidence collection or status changes."""
import datetime
import json

STATUS={'passed':'Passed','failed':'Failed','not_run':'Not checked','blocked':'Could not proceed','inconclusive':'Not confirmed','not_applicable':'Not needed for this request'}
STAGES={
 'preflight':('Checking the starting conditions','This checks whether the required tools and starting conditions were recorded before work began.'),
 'build':('Building the Android app','Building turns the source files into an Android installation file. A successful build alone does not show that the app works when opened.'),
 'artifact_verification':('Checking the installation file','This checks the produced file and its identity so later tests can be tied to the same app.'),
 'install':('Installing the app','This checks whether Android accepted the installation file. Installation and opening the app are separate checks.'),
 'launch':('Opening the app','This checks whether the app starts on Android. Its features still need to be tried to establish that they work.'),
 'automated_tests':('Running automated checks','These checks exercise the cases chosen for the test suite. Their results apply to those cases, not every possible use of the app.'),
 'functional':('Trying the requested features','This checks the behavior the user asked for. The recorded result below explains what was actually tried.'),
 'visual':('Inspecting the screen','This checks the visible interface, such as whether controls can be seen and used. A successful command alone is not a visual inspection.'),
 'performance':('Measuring speed and responsiveness','This checks measured responsiveness when that is part of the request. Looking smooth in a screenshot is not a performance measurement.'),
 'crash_logs':('Collecting crash information','These logs help show whether the app crashed during the observed test period. Missing logs do not mean there were no crashes.'),
 'cleanup':('Stopping the test resources','This records whether resources owned by the trial were cleaned up. Ending a model response by itself does not establish that its test processes stopped.')}
STATE_EXPLANATION={
 'passed':'The recorded check passed.',
 'failed':'The recorded check failed; its explanation and evidence are retained below.',
 'not_run':'No completed check is recorded here, so this section does not establish a result.',
 'blocked':'The check could not proceed under the recorded conditions.',
 'inconclusive':'The available evidence does not establish a pass for this check.',
 'not_applicable':'This check was recorded as unnecessary for this request.'}
HISTORY={
 'recorded':'The saved entries are available. This does not mean every decision was captured.',
 'partial':'Some of the explanation is missing. The available entries are retained, and missing reasons should not be guessed.',
 'unavailable':'The decision record could not be read or confirmed. The app results are reported separately.',
 'not_requested':'A decision history was not requested for this run.'}
ORIGIN={'agent_statement':'the agent’s recorded statement','user_instruction':'the user’s instruction','controller_receipt':'the supervising process’s test record','reviewer_annotation':'a later reviewer’s comment'}
MODE={'contemporaneous':'recorded as the work happened','checkpoint':'saved at a checkpoint','retrospective':'documented afterward'}

def safe(value):
    result=str(value).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
    for char in '\\`*_[]()#!|':result=result.replace(char,'\\'+char)
    return result.replace('\n',' ')

def details(value):
    return ['<details>','<summary>Technical record</summary>','','```json',json.dumps(value,sort_keys=True,ensure_ascii=True,indent=2).replace('`','\\u0060').replace('<','\\u003c').replace('>','\\u003e'),'```','','</details>','']

def time_ms(value):
    try:return datetime.datetime.fromtimestamp(value/1000,datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    except (ValueError,OverflowError,OSError):return 'a timestamp outside the displayable date range (see the technical record)'
