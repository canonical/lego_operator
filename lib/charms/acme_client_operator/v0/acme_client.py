"""# acme_client Library.
This library is designed to enable developers to easily create new charms for implementations of the ACME protocol.
This library contains all the logic necessary to get certificates from an ACME server..

## Getting Started
To get started using the library, you need to fetch the library using `charmcraft`.
```shell
charmcraft fetch-lib charms.acme_client_operator.v0.acme_client
```
You will also need to add the following library to the charm's `requirements.txt` file:
- jsonschema
- cryptography

Then, to use the library in an example charm, you can do the following:
```python
from charms.acme_client_operator.v0.acme_client import AcmeClient
from ops.main import main
class ExampleAcmeCharm(AcmeClient):
    def __init__(self, *args):
        super().__init__(*args)
        self._server = "https://acme-staging-v02.api.letsencrypt.org/directory"
    @property
    def cmd(self):
        return [
            "lego",
            "--email",
            self._email,
            "--accept-tos",
            "--csr",
            "/tmp/csr.pem",
            "--server",
            self._server,
            "--dns",
            "namecheap",
            "run",
        ]

    @property
    def certs_path(self):
        return "/tmp/.lego/certificates/"

    @property
    def plugin_config(self):
        return None
```
Charms that leverage this library also need to specify a `provides` relation in their
`metadata.yaml` file. For example:
```yaml
provides:
  certificates:
    interface: tls-certificates
```
"""

# The unique Charmhub library identifier, never change it
LIBID = "b3c9913b68dc42b89dfd0e77ac57236d"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

import logging
from abc import abstractmethod
from charms.tls_certificates_interface.v1.tls_certificates import (  # type: ignore[import]
    TLSCertificatesProvidesV1,
    CertificateCreationRequestEvent,
)
from cryptography import x509
from cryptography.x509.oid import NameOID
from ops.charm import CharmBase
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.pebble import ExecError

logger = logging.getLogger(__name__)


class AcmeClient(CharmBase):
    """Base charm for charms that use the ACME protocol to get certificates.
    This charm implements the tls_certificates interface as a provider."""

    def __init__(self, *args):
        super().__init__(*args)
        self._csr_path = "/tmp/csr.pem"
        self._container_name = self.service_name = self.meta.name
        self._container = self.unit.get_container(self._container_name)
        service_name_with_underscores = self.service_name.replace("-", "_")
        self.tls_certificates = TLSCertificatesProvidesV1(self, "certificates")
        pebble_ready_event = getattr(
            self.on, f"{service_name_with_underscores}_pebble_ready"
        )
        self.framework.observe(pebble_ready_event, self._on_acme_client_pebble_ready)
        self.framework.observe(
            self.tls_certificates.on.certificate_creation_request,
            self._on_certificate_creation_request,
        )

    def _on_acme_client_pebble_ready(self, event):
        self.unit.status = ActiveStatus()

    def _on_certificate_creation_request(self, event: CertificateCreationRequestEvent) -> None:
        if not self.unit.is_leader():
            return

        if not self._container.can_connect():
            self.unit.status = WaitingStatus("Waiting for container to be ready")
            event.defer()
            return

        try:
            csr = x509.load_pem_x509_csr(event.certificate_signing_request.encode())
            subject_value = csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
            if isinstance(subject_value, bytes):
                subject = subject_value.decode()
            else:
                subject = subject_value
        except Exception:
            logger.exception("Bad CSR received, aborting")
            return

        self._container.push(
            path=self._csr_path, make_dirs=True, source=event.certificate_signing_request.encode()
        )

        logger.info("Received Certificate Creation Request for domain %s", subject)
        process = self._container.exec(
            self.cmd, timeout=300, working_dir="/tmp", environment=self.plugin_config
        )
        try:
            stdout, error = process.wait_output()
            logger.info(f"Return message: {stdout}, {error}")
        except ExecError as e:
            self.unit.status = BlockedStatus("Error getting certificate. Check logs for details")
            logger.error("Exited with code %d. Stderr:", e.exit_code)
            for line in e.stderr.splitlines():  # type: ignore
                logger.error("    %s", line)
            return

        chain_pem = self._container.pull(path=f"{self.certs_path}{subject}.crt")
        certs = []
        for cert in chain_pem.read().split("\n\n"):
            certs.append(cert)
        self.tls_certificates.set_relation_certificate(
            certificate=certs[0],
            certificate_signing_request=event.certificate_signing_request,
            ca=certs[-1],
            chain=list(reversed(certs)),
            relation_id=event.relation_id,
        )

    @property
    @abstractmethod
    def cmd(self) -> list[str]:
        """Command to run to get the certificate.
        Implement this method in your charm to return the command that will be used to get the
        certificate.
        Example
        ```
        @property
        def cmd(self):
            return [
                "lego",
                "--email",
                self._email,
                "--accept-tos",
                "--csr",
                "/tmp/csr.pem",
                "--server",
                self._server,
                "--dns",
                "namecheap",
                "run",
            ]
        ```

        Returns:
            list[str]: Command and args to run.
        """

    @property
    @abstractmethod
    def certs_path(self) -> str:
        """Path to the certificates.
        Implement this method in your charm to return the path to the obtained certificates.
        Example
        ```
        @property
        def certs_path(self):
            return "/tmp/.lego/certificates/"
        ```

        Returns:
            str: Path to the certificates.
        """

    @property
    @abstractmethod
    def plugin_config(self) -> dict[str, str]:
        """Plugin specific additional configuration for the command.
        Implement this method in your charm to return a dictionary with the plugin specific
        configuration.

        Returns:
            dict[str, str]: Plugin specific configuration.
        """
