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
            while True:
                try:
                    if os.name == 'nt':
                        import msvcrt
                        self.handle.seek(0)
                        msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
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
