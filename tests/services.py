"""Caterva2 services for tests.

This ensures that Caterva2 broker, publisher and subscriber services are
running before proceeding to tests.  It has three modes of operation:

- Standalone script: when run as a script, it starts the services as children
  and makes sure that they are available to other local programs.  If given an
  argument, it uses it as the directory to store state in; otherwise it uses
  the value in `DEFAULT_STATE_DIR`.  If the directory does not exist, it is
  created and populated with the example files from the source distribution.
  Terminating the program stops the services.

  Usage example::

      $ cd Caterva2
      $ export PYTHONPATH=.
      $ python -m tests.services &  # state in ``_caterva2``
      [3] 12345
      $ pytest
      $ kill %3

- pytest fixture with external services: when using `services()` as a fixture,
  it checks that the services are available to other local programs.  It does
  not tamper with the state directory nor stop the services when tests finish.

  Usage example: same as above (but on the pytest side).

- pytest fixture with managed services: if the environment variable
  ``CATERVA2_USE_EXTERNAL`` is set to 0, the `services()` fixture takes care
  of starting the services as children and making sure that they are available
  to other local programs.  It uses the value in `TEST_STATE_DIR` as the
  directory to store state in.  If the directory exists, it is removed first.
  Then the directory is created and populated with the example files from the
  source distribution.  When tests finish, the services are stopped.

  Usage example::

      $ cd Caterva2
      $ export PYTHONPATH=.
      $ env CATERVA2_USE_EXTERNAL=0 pytest  # state in ``_caterva2_tests``

In all cases, the ``CATERVA2_SOURCE`` environment variable is set to the path
of the source distribution.
"""

import os
import shutil
import signal
import subprocess
import sys
import time

import httpx
import pytest

from pathlib import Path


DEFAULT_STATE_DIR = '_caterva2'
TEST_STATE_DIR = DEFAULT_STATE_DIR + '_tests'
TEST_PUBLISHED_ROOT = 'foo'


def get_local_http(port, path='/'):
    def check():
        url = f'http://localhost:{port:d}{path}'
        try:
            r = httpx.get(url, timeout=0.5)
            return r.status_code == 200
        except httpx.ConnectError:
            return False
    check.__name__ = f'get_local_http_{port:d}'  # more descriptive
    return check


bro_check = get_local_http(8000, '/api/roots')
pub_check = get_local_http(8001, '/api/list')
sub_check = get_local_http(8002, '/api/roots')


class Services:
    def __init__(self):
        self.source_dir = Path(__file__).parent.parent

    def _setup(self):
        os.environ['CATERVA2_SOURCE'] = str(self.source_dir)


class ManagedServices(Services):
    def __init__(self, state_dir, reuse_state=True):
        super().__init__()
        self.state_dir = Path(state_dir).resolve()
        self.reuse_state = reuse_state

        self.data_dir = self.state_dir / 'data'

        self._procs = {}
        self._setup_done = False

    def _start_proc(self, name, *args, check=None):
        if check is not None and check():
            raise RuntimeError(
                f"check for service \"{name}\" succeeded before start"
                f" (external service running?): {check.__name__}")

        self._procs[name] = subprocess.Popen(
            [sys.executable,
             self.source_dir / 'src' / f'{name}.py',
             '--statedir=%s' % (self.state_dir / name),
             *args])

        if check is None:
            return

        start_timeout_secs = 10
        start_sleep_secs = 1
        for retry in range(int(start_timeout_secs / start_sleep_secs)):
            time.sleep(start_sleep_secs)
            if check():
                break
        else:
            raise RuntimeError(
                f"service \"{name}\" failed to become available"
                f" after {start_timeout_secs:d} seconds")

    def _setup(self):
        if self._setup_done:
            return

        super()._setup()

        if not self.reuse_state and self.state_dir.is_dir():
            shutil.rmtree(self.state_dir)
        self.state_dir.mkdir(exist_ok=True)

        if not self.data_dir.exists():
            examples_dir = self.source_dir / 'root-example'
            shutil.copytree(examples_dir, self.data_dir, symlinks=True)
        self.data_dir.mkdir(exist_ok=True)

        self._setup_done = True

    def start_all(self):
        self._setup()

        self._start_proc('bro', check=bro_check)
        self._start_proc('pub', TEST_PUBLISHED_ROOT, self.data_dir,
                         check=pub_check)
        self._start_proc('sub', check=sub_check)

    def stop_all(self):
        for proc in self._procs.values():
            try:
                os.kill(proc.pid, signal.SIGTERM)
                time.sleep(1)
                os.kill(proc.pid, signal.SIGHUP)
            except ProcessLookupError:
                pass

    def wait_for_all(self):
        for proc in self._procs.values():
            proc.wait()


class ExternalServices(Services):
    _checks = [bro_check, pub_check, sub_check]

    def start_all(self):
        super()._setup()

        failed = [check.__name__ for check in self._checks if not check()]
        if failed:
            raise RuntimeError("failed checks for external services: "
                               + ' '.join(failed))

    def stop_all(self):
        pass

    def wait_for_all(self):
        pass


@pytest.fixture(scope='session')
def services():
    srvs = (ManagedServices(TEST_STATE_DIR, reuse_state=False)
            if os.environ.get('CATERVA2_USE_EXTERNAL', '1') == '0'
            else ExternalServices())
    try:
        srvs.start_all()
        yield srvs
    finally:
        srvs.stop_all()
    srvs.wait_for_all()


def main():
    if '--help' in sys.argv:
        print(f"Usage: {sys.argv[0]} [STATE_DIRECTORY=\"{DEFAULT_STATE_DIR}\"]")
        return

    state_dir = sys.argv[1] if len(sys.argv) >= 2 else DEFAULT_STATE_DIR
    srvs = ManagedServices(state_dir, reuse_state=True)
    try:
        srvs.start_all()
        srvs.wait_for_all()
    finally:
        srvs.stop_all()


if __name__ == '__main__':
    main()
