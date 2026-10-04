#!/usr/bin/python3
"""Operator-only reviewed installation; never invoked by model tool arguments."""
import hashlib,json,os,shutil,subprocess,sys,time
from pathlib import Path
HERE=Path(__file__).resolve().parent
TARGET=Path('/CONFIGURE/android-chat-bridge')
PLUGIN=Path('/CONFIGURE/extensions/android-chat-bridge')
CONFIG=Path('/CONFIGURE/openclaw.json')
SKILL=Path('/CONFIGURE/skills/long-task-runner')
CLI=['/usr/local/bin/node','/CONFIGURE/openclaw.mjs']
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def need(x,m):
 if not x:raise RuntimeError(m)
def command(args,env=None,timeout=60):
 p=subprocess.run(args,capture_output=True,text=True,env=env,timeout=timeout)
 need(p.returncode==0,(p.stdout+p.stderr)[-4000:])
 return p.stdout
need(os.getuid()==502,'run only as the configured service account')
manifest=json.loads((HERE/'transfer-manifest.json').read_text())
for name,value in manifest.items():need(sha(HERE/name)==value,'incoming hash differs: '+name)
need(not PLUGIN.exists(),'existing plugin installation requires review')
if TARGET.exists():
 need(not (TARGET/'jobs').exists() and not (TARGET/'installation.json').exists(),'prior live jobs/installation require review')
 previous={'bridge.py': '576105b977b9e32408f0fd5ce890f28719c5562525a92d573080d716a4801c56', 'emulator_adapter.py': '955a302bb480752417654d8fc644a33b885542fcd8f485411be03c986517f883', 'report.py': '9cfe4cafbbf554e0ea6045f862989d5dce7d3d112d8ce63dca39b1a18aa9a2d3', 'test_bridge.py': '4bc6937260bcd03ea7f84707493e5c2e9758e2c5552d0afbb8714674fb0aeeb8', 'test_emulator_adapter.py': '4411e25d84772929998fc9ddd482f6a5d3e4a919973ebf5c9016b0406785bcd4', 'test_report.py': '55102e3f4472485910ebc62738541ddc1bf97fadef6dd300b24f4bdce5c2efdd'}
 for n,h in previous.items():need(sha(TARGET/n)==h,'partial install file drift: '+n)
need(sha(CONFIG)=='89dcc7905889a8fafc09fb53d13ea567a67afb34524a6d8cc73e43d1ad715a3c','config drift')
prior=json.loads((HERE/'prior-skill-files.json').read_text())
for name,value in prior.items():need(sha(SKILL/name)==value,'installed skill drift: '+name)
TARGET.mkdir(mode=0o700,exist_ok=True)
for name in ('bridge.py','emulator_adapter.py','report.py','test_bridge.py','test_emulator_adapter.py','test_report.py'):
 shutil.copyfile(HERE/name,TARGET/name)
 os.chmod(TARGET/name,0o600)
# Trusted tests use only mocks; generated app code is never run on macOS.
result=command(['/usr/bin/python3','-m','unittest','discover','-s',str(TARGET),'-q'])
(TARGET/'local-tests.txt').write_text(result or 'Python fake-only suite passed; unittest details were emitted on stderr.\n')
sys.path.insert(0,str(TARGET))
import bridge
baseline=bridge.preflight()
(TARGET/'installation-baseline.json').write_text(json.dumps(baseline,indent=2))
PLUGIN.mkdir(mode=0o700)
for name in ('index.js','package.json','openclaw.plugin.json','test_plugin.mjs'):
 shutil.copyfile(HERE/'plugin'/name,PLUGIN/name)
 os.chmod(PLUGIN/name,0o600)
command(['/usr/local/bin/node','--test',str(PLUGIN/'test_plugin.mjs')])
before_tools=command(CLI+['gateway','call','tools.effective','--params',json.dumps({'agentId':'main','sessionKey':'agent:main:al-bird-20261003'}),'--json'])
(TARGET/'tools-before.json').write_text(before_tools)
original=CONFIG.read_bytes();cfg=json.loads(original)
(TARGET/'openclaw.before.json').write_bytes(original);os.chmod(TARGET/'openclaw.before.json',0o600)
# Tool factory exposes this one capability only to the trusted main-session identity.
# Extend existing additive lists, preserving all current policy and other tools.
for block in (cfg['tools'],cfg['tools']['sandbox']['tools']):
 need('android_project' not in block.get('alsoAllow',[]),'tool already configured')
 block.setdefault('alsoAllow',[]).append('android_project')
cfg['plugins']['allow'].append('android-chat-bridge')
cfg['plugins']['entries']['android-chat-bridge']={'enabled':True,'config':{}}
candidate=TARGET/'openclaw.candidate.json';candidate.write_text(json.dumps(cfg,indent=2)+'\n');candidate.chmod(0o600)
env=os.environ.copy();env['OPENCLAW_CONFIG_PATH']=str(candidate)
validation=command(CLI+['config','validate','--json'],env=env)
(TARGET/'config-validation.json').write_text(validation)
# Install the skill only after all previous file hashes match. Back up exact old bytes.
shutil.copyfile(SKILL/'SKILL.md',TARGET/'SKILL.before.md')
shutil.copyfile(HERE/'skill/long-task-runner/SKILL.md',SKILL/'SKILL.md')
need(sha(CONFIG)==hashlib.sha256(original).hexdigest(),'config changed during install')
tmp=CONFIG.with_name('openclaw.android-chat-bridge.new');tmp.write_bytes(candidate.read_bytes());tmp.chmod(CONFIG.stat().st_mode & 0o777);tmp.replace(CONFIG)
receipt={'installedAt':time.time(),'configBefore':hashlib.sha256(original).hexdigest(),'configAfter':sha(CONFIG),'skillAfter':sha(SKILL/'SKILL.md'),'pluginFiles':{p.name:sha(p) for p in PLUGIN.iterdir() if p.is_file()},'controllerFiles':{n:sha(TARGET/n) for n in ('bridge.py','emulator_adapter.py','report.py')},'configChanges':['tools.alsoAllow + android_project','tools.sandbox.tools.alsoAllow + android_project','plugins.allow + android-chat-bridge','plugins.entries.android-chat-bridge enabled with empty config and no conversation hooks'],'baseline':baseline}
(TARGET/'installation.json').write_text(json.dumps(receipt,indent=2))
print('AM_INSTALLED',json.dumps(receipt))
