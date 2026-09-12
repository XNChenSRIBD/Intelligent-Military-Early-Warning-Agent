"""One model caller across online and replay processes sharing the catalog."""

import asyncio
import errno
import os


class SharedModelLock:
    def __init__(self, path):
        self.path = path
        self.local = asyncio.Lock()
        self.handle = None

    async def __aenter__(self):
        await self.local.acquire()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.handle = self.path.open('a+b')
            if os.name == 'nt' and self.path.stat().st_size == 0:
                self.handle.write(b'0')
                self.handle.flush()
            if os.name != 'nt':
                import fcntl
                # Stay in the kernel's wait queue instead of leaving a polling
                # gap in which the active process can immediately take over.
                pending = asyncio.get_running_loop().run_in_executor(
                    None, fcntl.flock, self.handle.fileno(), fcntl.LOCK_EX)
                try:
                    await asyncio.shield(pending)
                except asyncio.CancelledError:
                    # A thread waiting in flock cannot be cancelled. Finish its
                    # acquisition before the outer cleanup closes the handle.
                    while not pending.done():
                        try:
                            await asyncio.shield(pending)
                        except asyncio.CancelledError:
                            continue
                        except Exception:
                            break
                    if not pending.cancelled():
                        pending.exception()
                    raise
                return self
            while True:
                try:
                    import msvcrt
                    self.handle.seek(0)
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                    return self
                except OSError as error:
                    if error.errno not in (errno.EAGAIN, errno.EACCES) and getattr(error, 'winerror', None) != 33:
                        raise
                    await asyncio.sleep(0.5)
        except BaseException:
            if self.handle:
                self.handle.close()
                self.handle = None
            self.local.release()
            raise

    async def __aexit__(self, *_):
        try:
            if os.name == 'nt':
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None
            self.local.release()
