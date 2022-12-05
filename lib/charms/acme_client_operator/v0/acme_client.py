"""# acme_client Library.
This library is designed to enable developers to easily create new charms for implementations of the Acme protocol.
This library contains all the logic necessary to get certificates from a certificate provider using the acme protocol.
The constructor takes the following:
- Reference to the parent charm (CharmBase)
- The implementation specific command (str)
- Additional configuration that contains environment variables (dict)
## Getting Started
To get started using the library, you just need to fetch the library using `charmcraft`.
```shell
cd some-charm
charmcraft fetch-lib charms.acme_client_operator.v0.acme_client
```
Then, to initialise the library:
```python
from charms.acme_client_operator.v0.acme_client import AcmeClient
from charms.observability_libs.v0.kubernetes_service_patch import KubernetesServicePatch
from ops.charm import CharmBase
from ops.main import main
from charms.tls_certificates_interface.v1.tls_certificates import (  # type: ignore[import]
    CertificateCreationRequestEvent,
    TLSCertificatesProvidesV1,
)
class ExampleAcmeCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        self.tls_certificates = TLSCertificatesProvidesV1(self, "certificates")
        self.framework.observe(
            self.tls_certificates.on.certificate_creation_request,
            self._acme_client_operator.on_certificate_creation_request,
        )
        lego_cmd = f"lego --email example@email.com --dns namecheap --domains example.com"
        self._acme_client_operator = AcmeClient(self, lego_cmd, self.additional_config)
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
from typing import Dict

from charms.tls_certificates_interface.v1.tls_certificates import (  # type: ignore[import]
    CertificateCreationRequestEvent,
)
from cryptography import x509
from cryptography.x509.oid import NameOID
from ops.charm import CharmBase
from ops.framework import Object
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.pebble import ExecError

logger = logging.getLogger(__name__)

LEGO_CERTS_PATH = "/tmp/.lego/certificates/"
class AcmeClient(Object):
    """TODO"""

    def __init__(
        self,
        charm: CharmBase,
        cmd: str,
        csr_path: str,
        additional_config: Dict[str, str] = {},
    ):
        super().__init__(charm, None)
        self._charm = charm
        self._csr_path = csr_path
        self._container_name = self.service_name = self._charm.meta.name
        self._container = self._charm.unit.get_container(self._container_name)
        service_name_with_underscores = self.service_name.replace("-", "_")
        self._cmd = cmd
        pebble_ready_event = getattr(
            self._charm.on, f"{service_name_with_underscores}_pebble_ready"
        )
        self.framework.observe(pebble_ready_event, self._on_acme_client_pebble_ready)
        self._additional_config = additional_config

    def _on_acme_client_pebble_ready(self, event):
        self._charm.unit.status = ActiveStatus()

    def on_certificate_creation_request(self, event: CertificateCreationRequestEvent) -> None:
        logger.info("Received Certificate Creation Request")
        if not self._charm.unit.is_leader():
            return

        if not self._container.can_connect():
            self._charm.unit.status = WaitingStatus("Waiting for container to be ready")
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

        logger.info("Getting certificate for domain %s", subject)
        process = self._container.exec(
            self._cmd, timeout=300, working_dir="/tmp", environment=self._additional_config
        )
        try:
            stdout, error = process.wait_output()
            logger.info(f"Return message: {stdout}, {error}")
        except ExecError as e:
            self._charm.unit.status = BlockedStatus("Error getting certificate. Check logs for details")
            logger.error("Exited with code %d. Stderr:", e.exit_code)
            for line in e.stderr.splitlines():  # type: ignore
                logger.error("    %s", line)
            return

        chain_pem = self._container.pull(path=f"{LEGO_CERTS_PATH}{subject}.crt")
        certs = []
        for cert in chain_pem.read().split("\n\n"):
            certs.append(cert)

        self._charm.tls_certificates.set_relation_certificate(
            certificate=certs[0],
            certificate_signing_request=event.certificate_signing_request,
            ca=certs[-1],
            chain=list(reversed(certs)),
            relation_id=event.relation_id,
        )
