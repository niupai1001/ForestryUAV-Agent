"""Workspace service ownership lasts until every application worker has drained."""
from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager

from ..contracts import Failure


@contextmanager
def workspace_writer(root):
    # Use the same OS-lock mechanism as pilot_lock, not a timeout/PID lease.
    # Keep ownership outside the directories that an offline restore replaces.
    path = root / '.forestry-service.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise Failure('WORKSPACE_IN_USE', '同一工作区已有服务持有写入权，请使用原实例或等待其退出。', 409) from error
        try:
            yield
        finally:
            # Never unlink: a second inode would allow another owner on POSIX.
            if os.name == 'nt':
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


async def finish_owned(awaitable):
    """Cancellation waits for owned work before releasing the workspace."""
    work = asyncio.create_task(awaitable)
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        while not work.done():
            try:
                await asyncio.shield(work)
            except asyncio.CancelledError:
                continue
        work.result()
        raise


async def owned_thread(function, *args):
    return await finish_owned(asyncio.to_thread(function, *args))
