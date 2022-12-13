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
    def _domain(self) -> Optional[str]:
        return self.model.config.get("domain")

    @property
    def _email(self) -> Optional[str]:
        return self.model.config.get("email")

    @property
    def _plugin(self) -> str:
        return "namecheap"

    @property
    def _plugin_config(self):
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
import abc
from typing import List, Dict

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
    __metaclass__ = abc.ABCMeta
    def __init__(self, *args):
        super().__init__(*args)
        self._server = "https://acme-staging-v02.api.letsencrypt.org/directory"
        self._csr_path = "/tmp/csr.pem"
        self._certs_path = "/tmp/.lego/certificates/"
        self._container_name = list(self.meta.containers.values())[0].name
        container_name_with_underscores = self._container_name.replace("-", "_")
        self.tls_certificates = TLSCertificatesProvidesV1(self, "certificates")
        pebble_ready_event = getattr(
            self.on, f"{container_name_with_underscores}_pebble_ready"
        )
        self.framework.observe(pebble_ready_event, self._on_acme_client_pebble_ready)
        self.framework.observe(
            self.tls_certificates.on.certificate_creation_request,
            self._on_certificate_creation_request,
        )

    def _on_acme_client_pebble_ready(self, event):
        self.unit.status = ActiveStatus()

    def _on_certificate_creation_request(self, event: CertificateCreationRequestEvent) -> None:
        _container = self.unit.get_container(self._container_name)
        if not self.unit.is_leader():
            return

        if not _container.can_connect():
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

        _container.push(
            path=self._csr_path, make_dirs=True, source=event.certificate_signing_request.encode()
        )

        logger.info("Received Certificate Creation Request for domain %s", subject)
        process = _container.exec(
            self._cmd, timeout=300, working_dir="/tmp", environment=self._plugin_config
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

        chain_pem = _container.pull(path=f"{self._certs_path}{subject}.crt")
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
    def _cmd(self) -> List[str]:
        """Command to run to get the certificate.

        Returns:
            list[str]: Command and args to run.
        """
        return [
            "lego",
            "--email",
            self._email,
            "--accept-tos",
            "--csr",
            self._csr_path,
            "--server",
            self._server,
            "--dns",
            self._plugin,
            "run",
        ]

    @property
    @abstractmethod
    def _email(self) -> str:
        """Account email address.
        Implement this method in your charm to return the email address of the account on the ACME server.

        Returns:
            str: email address.
        """

    @property
    @abstractmethod
    def _plugin(self) -> str:
        """DNS provider used.
        Implement this method in your charm to return your DNS provider.

        Returns:
            str: DNS provider.
        """

    @property
    @abstractmethod
    def _plugin_config(self) -> Dict[str, str]:
        """Plugin specific additional configuration for the command.
        Implement this method in your charm to return a dictionary with the plugin specific
        configuration.

        Returns:
            dict[str, str]: Plugin specific configuration.
        """