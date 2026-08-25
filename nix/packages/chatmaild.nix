# chatmaild, built from the vendored relay commit rather than pip-installed
# into a venv on the target.
#
# cmdeploy's ChatmailVenvDeployer stays untouched; cmrelay just builds the
# same source a different way, against an interpreter pinned by flake.lock.

{
  lib,
  buildPythonPackage,
  setuptools,
  iniconfig,
  filelock,
  psutil,
  requests,
  crypt-r,
}:

buildPythonPackage {
  pname = "chatmaild";
  version = "0.3";
  pyproject = true;

  # The relay submodule is the source of truth; only chatmaild/ is needed.
  src = ../../cmdeploy-relay/chatmaild;

  build-system = [ setuptools ];

  dependencies = [
    iniconfig
    filelock
    psutil
    requests
    crypt-r
  ];

  # chatmaild declares a pytest11 entry point for its own test plugin.
  # Nothing at runtime imports it, and pytest has no business in a relay's
  # closure, so the import check names the modules the units actually run.
  pythonImportsCheck = [
    "chatmaild.config"
    "chatmaild.doveauth"
    "chatmaild.metadata"
  ];

  # The suite needs a live dovecot and pytest plugins that are not in the
  # closure; cmlxc runs it against a deployed relay instead.
  doCheck = false;

  meta = {
    description = "chatmail relay daemons: doveauth, metadata, expire, fsreport";
    license = lib.licenses.mit;
  };
}
