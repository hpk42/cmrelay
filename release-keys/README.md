# Release keys

Every `*.pem` in this directory is a public key `cmrelay upgrade` will accept
on a host running this release. `cmrelay version` prints their fingerprints,
and `upgrade` prints the one that actually verified a candidate. The
directory is empty until the first signed release, so until then every
upgrade needs `--allow-unsigned`.

Two keys belong here.

**`ci.pem`** is the key the release workflow signs with. Its private half is a
repository secret used by a job with required reviewers, so a tag push alone
cannot mint a signature. The trust root is therefore GitHub plus whoever can
cause the release workflow to run. That is weaker than a key held offline and
much stronger than nothing: it defends against a compromised or impersonated
package index, a man in the middle, a mirror serving an altered file, and an
operator who fetched the wrong thing over a bad link.

**`offline.pem`** is the recovery key. Its private half is never uploaded
anywhere: not to GitHub, not into a secret, not into this repository, and not
into the same backup as the CI secret. It signs nothing routinely. It exists
so that losing the GitHub account does not mean losing every deployed relay's
upgrade path, and it has to ship from the first signed release, because a
trusted key can only be added by a signed release and a signed release needs
a transport that still works.

The two recover each other. If the offline key is lost, an ordinary CI-signed
release carries a new one; if the CI key is compromised, an offline-signed
release drops it from this directory.

Generate a keypair, and sign with it, using `scripts/cmrelay-sign`.
