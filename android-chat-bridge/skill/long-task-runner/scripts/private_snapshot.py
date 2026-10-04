"""Private bounded immutable pairs and atomic pointers; no execution authority."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

def require(ok, reason):
    if not ok:raise ValueError(reason)

def sha(data):return hashlib.sha256(data).hexdigest()

def encode(value):return (json.dumps(value,sort_keys=True,ensure_ascii=True,allow_nan=False,separators=(',',':'))+'\n').encode()

@contextmanager
def locked(root):
    root=Path(root)
    require(root.is_absolute() and str(root)==os.path.normpath(str(root)) and not any(x.is_symlink() for x in (root,*root.parents)), 'absolute non-symlink root required')
    m=root.stat();require(stat.S_ISDIR(m.st_mode) and m.st_uid==os.getuid() and stat.S_IMODE(m.st_mode)==0o700,'private owned root required')
    fd=os.open(root/'report.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        m=os.fstat(fd);require(stat.S_ISREG(m.st_mode) and m.st_uid==os.getuid() and m.st_nlink==1 and not m.st_mode&0o077,'invalid lock')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield root
    finally:os.close(fd)

def read_file(path, maximum):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        m=os.fstat(fd);require(stat.S_ISREG(m.st_mode) and m.st_uid==os.getuid() and m.st_nlink==1 and not m.st_mode&0o077 and m.st_size<=maximum,'invalid private file')
        with os.fdopen(fd,'rb',closefd=False) as f:data=f.read(maximum+1)
        require(len(data)==m.st_size and len(data)<=maximum,'file changed or oversized')
        return data
    finally:os.close(fd)

def atomic(root, name, data):
    fd,temp=tempfile.mkstemp(prefix='.latest-',dir=root)
    with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
    os.replace(temp,root/name)
    fd=os.open(root,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)

def pair(root, raw, readable, identity, limit=64, maximum=1048576, storage_limit=None):
    """Caller holds locked(root). The file names are fixed, never supplied by JSON."""
    require(len(raw)<=maximum and len(readable)<=4*maximum,'snapshot exceeds limit')
    digest=sha(raw);name='snapshot-'+digest;dest=root/name
    content={'report.json':raw,'report.md':readable}
    if dest.exists() or dest.is_symlink():
        require(not dest.is_symlink() and dest.is_dir(),'invalid existing snapshot')
        for leaf,data in content.items():require(read_file(dest/leaf,4*maximum)==data,'existing snapshot changed')
    else:
        require(sum(p.name.startswith(('snapshot-','.report-')) for p in root.iterdir())<limit,'snapshot retention budget reached')
        if storage_limit is not None:
            total=0
            for p in root.rglob('*'):
                require(not p.is_symlink(),'symlink in snapshot store')
                if p.is_file():total+=p.stat().st_size
            require(total+len(raw)+len(readable)+4096<=storage_limit,'snapshot byte budget reached')
        staging=Path(tempfile.mkdtemp(prefix='.report-',dir=root))
        for leaf,data in content.items():
            fd=os.open(staging/leaf,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        fd=os.open(staging,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
        os.rename(staging,dest)
    pointer={'runId':identity,'snapshot':name,'reportSha256':digest,'markdownSha256':sha(readable)}
    atomic(root,'latest.json',encode(pointer))
    return pointer

def load(root, pointer=None, maximum=1048576):
    if pointer is None:pointer=json.loads(read_file(root/'latest.json',4096))
    require(type(pointer) is dict and set(pointer)=={'runId','snapshot','reportSha256','markdownSha256'},'invalid snapshot pointer')
    for key in ('reportSha256','markdownSha256'):
        import re
        require(type(pointer[key]) is str and re.fullmatch('[0-9a-f]{64}',pointer[key]),'invalid snapshot digest')
    require(pointer['snapshot']=='snapshot-'+pointer['reportSha256'],'invalid snapshot name')
    path=root/pointer['snapshot'];require(not path.is_symlink() and path.is_dir(),'invalid snapshot directory')
    raw=read_file(path/'report.json',maximum);readable=read_file(path/'report.md',4*maximum)
    require(sha(raw)==pointer['reportSha256'] and sha(readable)==pointer['markdownSha256'],'snapshot digest mismatch')
    value=json.loads(raw)
    require(encode(value)==raw,'snapshot JSON is not canonical')
    return value,readable,pointer
