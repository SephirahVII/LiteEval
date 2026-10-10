"""Per-attempt workspaces and optional hardened Docker containers."""
import asyncio
import hashlib
import difflib
import os
from pathlib import Path,PurePosixPath
import shutil
import stat
import tempfile
from .process import run_process


def relative_path(value):
    path=PurePosixPath(value)
    if not value or path.is_absolute() or '..' in path.parts or '\\' in value or ':' in value:
        raise ValueError('Expected a safe relative workspace path')
    return str(path)

def snapshot_bytes(root,name,limit=1_000_000):
    """On POSIX, open every component without following symlinks, including races."""
    parts=PurePosixPath(relative_path(name)).parts
    if not parts:raise ValueError('File path required')
    if os.open in os.supports_dir_fd and hasattr(os,'O_NOFOLLOW'):
        parent=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            for part in parts[:-1]:
                child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
                os.close(parent);parent=child
            fd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
            with os.fdopen(fd,'rb') as stream:
                info=os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size>limit:raise ValueError('File exceeds snapshot limit or is not regular')
                data=stream.read(limit+1)
        finally:os.close(parent)
    else:
        path=root.joinpath(*parts)
        path.resolve().relative_to(root.resolve())
        if path.is_symlink() or not path.is_file():raise ValueError('Expected a regular file')
        with path.open('rb') as stream:data=stream.read(limit+1)
    if len(data)>limit:raise ValueError('File exceeds snapshot limit')
    return data

class NoEnvironment:
    async def create(self,case):return self
    async def snapshot(self):return {}
    async def destroy(self,handle=None):pass

class WorkspaceEnvironment:
    """Fresh directory per attempt. This is workspace isolation, not a host security sandbox."""
    def __init__(self,config,root=None,labels=None):
        self.config=config;self.root=root;self.labels=labels or {};self.path=None
    async def create(self,case):
        if self.root:Path(self.root).mkdir(parents=True,exist_ok=True)
        self.path=Path(tempfile.mkdtemp(prefix='liteeval-',dir=self.root))
        try:
            files={**self.config.get('seed_files',{}),**case.input.get('files',{})}
            if len(files)>1000:raise ValueError('Too many seed files')
            self.initial_files=dict(files)
            size=0
            for name,content in files.items():
                path=self.path/relative_path(name)
                if not isinstance(content,str):raise ValueError('Seed files must contain UTF-8 text')
                size+=len(content.encode())
                if size>5_000_000:raise ValueError('Seed files exceed 5 MB')
                path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content)
            return self
        except BaseException:
            await self.destroy();raise
    async def execute(self,command,timeout=60,stdin=None,env=None):
        return await run_process(command,cwd=self.path,timeout=timeout,stdin=stdin,env=env)
    async def snapshot(self):
        tests=[]
        for item in self.config.get('validation_tests',[]):
            command=item['command']
            if not isinstance(command,list) or not command or not all(isinstance(v,str) for v in command):raise ValueError('Tests require argv array')
            result=await self.execute(command,timeout=min(float(item.get('timeout_seconds',30)),120),env={k:os.environ[k] for k in ('PATH','SYSTEMROOT','WINDIR') if k in os.environ})
            tests.append({'name':item['name'],**result})
        files={};total=0;truncated=False
        for p in sorted(self.path.rglob('*')):
            if p.is_symlink() or not p.is_file() or '.git' in p.relative_to(self.path).parts:continue
            if len(files)>=1000 or total>=5_000_000:truncated=True;break
            try:data=snapshot_bytes(self.path,p.relative_to(self.path).as_posix(),min(1_000_000,5_000_000-total))
            except (OSError,ValueError):truncated=True;continue
            total+=len(data)
            text=None
            try:text=data.decode('utf-8')
            except UnicodeDecodeError:pass
            files[p.relative_to(self.path).as_posix()]={'sha256':hashlib.sha256(data).hexdigest(),'size_bytes':len(data),'text':text}
        patch=[]
        for name in sorted(set(self.initial_files)|set(files)):
            before=self.initial_files.get(name,'')
            after=files.get(name,{}).get('text') or ''
            if before!=after:patch.extend(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile='a/'+name,tofile='b/'+name))
        return {'files':files,'tests':tests,'patch_diff':''.join(patch),'truncated':truncated,'isolation':'workspace'}
    async def destroy(self,handle=None):
        if self.path:shutil.rmtree(self.path,ignore_errors=True)

class DockerEnvironment(WorkspaceEnvironment):
    def __init__(self,config,root=None,labels=None):
        super().__init__(config,root,labels);self.container=None
    def create_command(self):
        network=self.config.get('network','none')
        if network not in {'none','bridge'}:raise ValueError('Docker network must be none or bridge')
        uid=(os.getuid() or 1000) if hasattr(os,'getuid') else 1000;gid=(os.getgid() or 1000) if hasattr(os,'getgid') else 1000
        # Never accept arbitrary Docker flags or mount paths from clients.
        args=['docker','create','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
              '--pids-limit','128','--memory',str(self.config.get('memory','1g')),'--cpus',str(self.config.get('cpus',1)),
              '--network',network,'--user',f'{uid}:{gid}','--tmpfs','/tmp:rw,nosuid,nodev,size=128m',
              '--mount',f'type=bind,src={self.path},dst=/workspace','--workdir','/workspace']
        for k,v in self.labels.items():args+=['--label',f'liteeval.{k}={v}']
        args+=['--entrypoint','sh',self.config['image'],'-c','while :; do sleep 3600; done']
        return args
    async def create(self,case):
        if not shutil.which('docker'):raise RuntimeError('Docker CLI is not installed on Worker')
        await super().create(case)
        if hasattr(os,'getuid') and os.getuid()==0:
            for path in [self.path,*self.path.rglob('*')]:os.chown(path,1000,1000)
        try:
            result=await run_process(self.create_command(),timeout=30)
            if result['exit_code']!=0:raise RuntimeError('Docker create failed: '+result['stderr'])
            self.container=result['stdout'].strip()
            result=await run_process(['docker','start',self.container],timeout=30)
            if result['exit_code']!=0:raise RuntimeError('Docker start failed: '+result['stderr'])
            return self
        except BaseException:
            await self.destroy();raise
    async def execute(self,command,timeout=60,stdin=None,env=None):
        args=['docker','exec','-i']
        # Env refs resolved by trusted Worker; keys rather than values appear on the host argv.
        process_env=dict(os.environ)
        for name,value in (env or {}).items():
            if name=='PATH':continue
            args+=['-e',name];process_env[name]=value
        args+=[self.container,*command]
        return await run_process(args,timeout=timeout,stdin=stdin,env=process_env)
    async def snapshot(self):
        result=await super().snapshot();result['isolation']='docker';return result
    async def destroy(self,handle=None):
        if self.container:
            await run_process(['docker','rm','-f',self.container],timeout=30)
            self.container=None
        await super().destroy()

    @staticmethod
    async def cleanup_stale(experiment_id):
        if not shutil.which('docker'):return
        result=await run_process(['docker','ps','-aq','--filter',f'label=liteeval.experiment={experiment_id}'],timeout=30)
        ids=result['stdout'].split()
        if ids:await run_process(['docker','rm','-f',*ids],timeout=30)

def make_environment(config,root=None,labels=None):
    kind=config.get('type','none')
    if kind=='none':return NoEnvironment()
    if kind=='workspace':return WorkspaceEnvironment(config,root,labels)
    if kind=='docker':return DockerEnvironment(config,root,labels)
    raise ValueError('Unknown environment')
