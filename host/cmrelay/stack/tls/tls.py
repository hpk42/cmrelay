"""The certificate: from acmetool, self-signed, or the operator's own."""

import tempfile
from pathlib import Path

from ... import paths

ACME_STATE = Path("/var/lib/acme")
# With less validity left, deploy asks acmetool to reach the network.
CERT_GRACE_SECONDS = 604800


def domains(config) -> list:
    bare = config.mail_domain_bare
    return [bare, f"mta-sts.{bare}", f"www.{bare}"]


def configure(deploy):
    {
        "acme": configure_acmetool,
        "self": configure_selfsigned,
        "external": configure_external,
    }[deploy.config.tls_cert_mode](deploy)


def configure_acmetool(deploy):
    names = domains(deploy.config)
    conf, desired = ACME_STATE / "conf", ACME_STATE / "desired"
    deploy.changes.ensure_directory("acmetool", desired)
    hook = paths.ACME_HOOKS / "nginx"
    source = "stack/tls"
    text = deploy.changes.marked(deploy.source(f"{source}/acmetool.hook").read_text())
    deploy.changes.write("acmetool", hook, text, mode=0o755)

    email = deploy.config.acme_email
    deploy.put_template(
        "acmetool", f"{source}/response-file.yaml.j2", conf / "responses", email=email
    )
    deploy.put_template("acmetool", f"{source}/target.yaml.j2", conf / "target")
    deploy.put_template(
        "acmetool", f"{source}/desired.yaml.j2", desired / names[0], domains=names
    )

    acmetool = deploy.bin("acmetool")
    for name in ("acmetool-redirector.service", "acmetool-reconcile.service"):
        deploy.put_unit("acmetool", f"{source}/{name}.j2", bin_path=acmetool)
        # Without this nothing reloads after a renewal.
        hooks = f"[Service]\nEnvironment=ACME_HOOKS_DIR={paths.ACME_HOOKS}\n"
        deploy.changes.install_dropin("acmetool", name, "10-hooks-dir.conf", hooks)
    deploy.put_unit("acmetool", f"{source}/acmetool-reconcile.timer")


def configure_selfsigned(deploy):
    cert = Path(deploy.config.tls_cert_path)
    key = Path(deploy.config.tls_key_path)
    if cert.exists():
        return
    deploy.changes.ensure_directory("dovecot", cert.parent)
    # the distribution's /etc/ssl/private keeps the mode it has
    if not key.parent.exists():
        deploy.changes.ensure_directory("dovecot", key.parent, mode=0o700)
    domain = deploy.config.mail_domain_bare
    with tempfile.TemporaryDirectory() as tmp:
        new_key, new_cert = Path(tmp) / "key", Path(tmp) / "cert"
        args = ("req", "-x509", "-noenc", "-days", "36500")
        args += ("-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256")
        args += ("-keyout", new_key, "-out", new_cert, "-subj", f"/CN={domain}")
        # end-entity only, so it cannot be used as a CA to sign others
        args += ("-addext", "basicConstraints=critical,CA:FALSE")
        args += ("-addext", "extendedKeyUsage=serverAuth,clientAuth")
        names = f"DNS:{domain},DNS:www.{domain},DNS:mta-sts.{domain}"
        deploy.bundle.run("openssl", *args, "-addext", f"subjectAltName={names}")
        # The key first: the certificate is what says both are in place.
        deploy.changes.write("nginx", key, new_key.read_bytes(), mode=0o600)
        deploy.changes.write("dovecot", cert, new_cert.read_bytes())


def configure_external(deploy):
    path_unit = "stack/tls/tls-cert-reload.path.j2"
    deploy.put_unit("external-tls", path_unit, cert_path=deploy.config.tls_cert_path)
    deploy.put_unit("external-tls", "stack/tls/tls-cert-reload.service")


def needs_reconcile(deploy) -> bool:
    """Whether acmetool reaches the network this run: only without a usable certificate."""
    cert = Path(deploy.config.tls_cert_path)
    if not cert.is_file():
        return True
    # the reconcile timer owns renewals
    args = ("x509", "-checkend", CERT_GRACE_SECONDS, "-noout", "-in", cert)
    return bool(deploy.bundle.run("openssl", *args, check=False).returncode)


def run_acmetool(deploy):
    names = ", ".join(domains(deploy.config))
    deploy.out.detail(f"reconciling certificates for {names}")
    hooks = f"--hooks={paths.ACME_HOOKS}"
    result = deploy.bundle.run("acmetool", "--batch", hooks, "reconcile", check=False)
    if result.returncode:
        deploy.out.warn(
            "acmetool could not obtain a certificate. That needs DNS"
            " already pointing at this host and the challenge port"
            " reachable from the internet:\n" + result.stderr.strip()[-2000:]
        )


def units(deploy) -> list:
    mode = deploy.config.tls_cert_mode
    return [
        # off unless the configuration asks for them
        ("acmetool-redirector.service", mode == "acme", "acmetool"),
        ("acmetool-reconcile.timer", mode == "acme", "acmetool"),
        ("tls-cert-reload.path", mode == "external", "external-tls"),
        # left as it is: a oneshot its timer starts
        ("acmetool-reconcile.service", None),
    ]
