# crypt(3) for Python 3.13+, required by chatmaild, not in nixpkgs

{
  lib,
  buildPythonPackage,
  fetchPypi,
  setuptools,
  libxcrypt,
}:

buildPythonPackage rec {
  pname = "crypt-r";
  version = "3.13.1";
  pyproject = true;

  src = fetchPypi {
    pname = "crypt_r";
    inherit version;
    hash = "sha256-W9Rq1R9bf+Xw7PSdi6LK/W+9Q6o7te+8qmtE32NAwaY=";
  };

  build-system = [ setuptools ];
  buildInputs = [ libxcrypt ];

  pythonImportsCheck = [ "crypt_r" ];

  meta = {
    description = "Standalone version of the deprecated crypt stdlib module";
    license = lib.licenses.psfl;
  };
}
