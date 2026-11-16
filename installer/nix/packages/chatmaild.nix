# chatmaild, copied from the chatmail relay repository

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
  version = (lib.importTOML ../../../host/chatmaild/pyproject.toml).project.version;
  pyproject = true;

  src = ../../../host/chatmaild;

  build-system = [ setuptools ];

  dependencies = [
    iniconfig
    filelock
    psutil
    requests
    crypt-r
  ];

  # the modules the units run
  pythonImportsCheck = [
    "chatmaild.config"
    "chatmaild.doveauth"
    "chatmaild.metadata"
  ];

  # the flake's pytest check runs the suite, and the closure leaves it out
  doCheck = false;
  postInstall = "rm -r $out/lib/python*/site-packages/chatmaild/tests";

  meta = {
    description = "chatmail relay daemons: doveauth, metadata, lastlogin, expire, fsreport, quota-expire";
    license = lib.licenses.mit;
  };
}
