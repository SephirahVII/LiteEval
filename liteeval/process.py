"""Bounded subprocess execution with partial output retained on timeout."""
import asyncio
import os
import signal

async def run_process(command,cwd=None,env=None,timeout=60,stdin=None,limit=8_000_000):
    proc=await asyncio.create_subprocess_exec(*command,cwd=cwd,env=env,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,start_new_session=os.name!='nt')
    buffers=[bytearray(),bytearray()]
    async def read(stream,index):
        while True:
            data=await stream.read(65536)
            if not data:break
            if len(buffers[index])+len(data)>limit:raise ValueError('Subprocess output exceeds limit')
            buffers[index].extend(data)
    async def kill():
        try:
            if os.name!='nt':os.killpg(proc.pid,signal.SIGKILL)
            elif proc.returncode is None:proc.kill()
        except ProcessLookupError:pass
        await proc.wait()
    async def write():
        if stdin is not None:
            try:
                proc.stdin.write(stdin.encode());await proc.stdin.drain();proc.stdin.close()
            except (BrokenPipeError,ConnectionResetError):pass
    tasks=[asyncio.create_task(read(proc.stdout,0)),asyncio.create_task(read(proc.stderr,1)),asyncio.create_task(write()),asyncio.create_task(proc.wait())]
    def result(timed_out=False):
        return {'exit_code':None if timed_out else proc.returncode,
                'stdout':buffers[0].decode('utf-8',errors='replace'),
                'stderr':buffers[1].decode('utf-8',errors='replace'),'timed_out':timed_out}
    try:
        await asyncio.wait_for(asyncio.gather(*tasks),timeout=timeout)
        return result()
    except asyncio.TimeoutError:
        await kill();return result(True)
    except BaseException:
        await kill();raise
    finally:
        for task in tasks:
            if not task.done():task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
