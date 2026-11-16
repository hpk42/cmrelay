"""A reader that goes away."""

import subprocess
import sys

from conftest import REPO


def test_a_reader_that_goes_away_does_not_stop_the_run():
    """`cmrelay upgrade | head` must still finish the upgrade."""
    code = "for _ in range(5000): out.print('x' * 100)"
    source = f"import sys\nfrom cmrelay.cli.output import Out\nout = Out()\n{code}"
    argv = [sys.executable, "-B", "-c", f"{source}\nsys.stderr.write('done')"]
    env = {"PYTHONPATH": str(REPO / "host")}
    pipes = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    proc = subprocess.Popen(argv, env=env, **pipes)
    proc.stdout.read(10)
    proc.stdout.close()
    assert proc.stderr.read() == "done"
    assert proc.wait(timeout=20) == 0
