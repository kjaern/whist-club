"""Use an isolated temporary Whist home for tests, before importing the app."""
import os
import tempfile

_test_home = tempfile.TemporaryDirectory(prefix='whist-test-')
os.environ['WHIST_HOME'] = _test_home.name
from whistapp.cli import initialize
initialize('admin', 'a$$word')
