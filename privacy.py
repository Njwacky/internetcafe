"""Small inactivity timer; clock is injectable for tests."""
import time
class IdleLock:
    def __init__(self,seconds=300,clock=time.monotonic):
        self.seconds=seconds;self.clock=clock;self.last=clock()
    def touch(self):self.last=self.clock()
    def expired(self):return self.clock()-self.last>=self.seconds
