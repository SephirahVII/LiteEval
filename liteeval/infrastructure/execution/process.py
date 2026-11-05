"""Bounded subprocess execution with partial output retained on timeout."""
import asyncio
import os
import signal
import codecs

async def terminate_tree(proc):
    try:
        if os.name != 'nt':
            os.killpg(proc.pid, signal.SIGKILL)
        elif proc.returncode is None:
            killer = await asyncio.create_subprocess_exec('taskkill', '/PID', str(proc.pid), '/T', '/F',
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await killer.wait()
            if proc.returncode is None: proc.kill()
    except ProcessLookupError:
        pass
    await proc.wait()

async def run_process(command,cwd=None,env=None,timeout=60,stdin=None,limit=8_000_000,on_output=None,exchange=None):
    if exchange is not None and stdin is None:
        raise ValueError('Interactive process requires an initial request')
    creation=asyncio.create_task(asyncio.create_subprocess_exec(*command,cwd=cwd,env=env,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,start_new_session=os.name!='nt'))
    try:
        proc=await asyncio.shield(creation)
    except asyncio.CancelledError:
        proc=await creation
        await terminate_tree(proc)
        raise
    buffers=[bytearray(),bytearray()]
    async def read(stream,index):
        decoder=codecs.getincrementaldecoder('utf-8')(errors='replace');pending=''
        while True:
            data=await stream.read(65536)
            if not data:break
            if len(buffers[index])+len(data)>limit:raise ValueError('Subprocess output exceeds limit')
            buffers[index].extend(data)
            if on_output:
                pending+=decoder.decode(data)
                boundary=pending.rfind('\n')+1
                if boundary:
                    on_output('stdout' if index==0 else 'stderr',pending[:boundary]);pending=pending[boundary:]
            elif exchange is not None and index == 0:
                pending += decoder.decode(data)
                while '\n' in pending:
                    line, pending = pending.split('\n', 1)
                    response = await exchange(line)
                    if response is not None:
                        proc.stdin.write((response + '\n').encode())
                        await proc.stdin.drain()
        if on_output:
            pending+=decoder.decode(b'',final=True)
            if pending:on_output('stdout' if index==0 else 'stderr',pending)
        elif exchange is not None and index == 0 and (pending or decoder.decode(b'', final=True)):
            raise ValueError('Unterminated interactive response')
    async def kill():
        await terminate_tree(proc)
    async def write():
        if stdin is not None:
            try:
                proc.stdin.write((stdin + '\n' if exchange is not None else stdin).encode())
                await proc.stdin.drain()
                if exchange is None: proc.stdin.close()
            except (BrokenPipeError,ConnectionResetError):pass
    tasks=[asyncio.create_task(read(proc.stdout,0)),asyncio.create_task(read(proc.stderr,1)),asyncio.create_task(write()),asyncio.create_task(proc.wait())]
    completion=asyncio.gather(*tasks)
    def result(timed_out=False):
        return {'exit_code':None if timed_out else proc.returncode,
                'stdout':buffers[0].decode('utf-8',errors='replace'),
                'stderr':buffers[1].decode('utf-8',errors='replace'),'timed_out':timed_out}
    try:
        await asyncio.wait_for(asyncio.shield(completion),timeout=timeout)
        return result()
    except asyncio.TimeoutError:
        await kill()
        try:
            await asyncio.wait_for(asyncio.shield(completion),timeout=5)
        except asyncio.TimeoutError:
            pass
        return result(True)
    except BaseException:
        await kill();raise
    finally:
        for task in tasks:
            if not task.done():task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        await asyncio.gather(completion,return_exceptions=True)
