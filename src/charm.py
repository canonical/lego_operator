#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk

"""Charm the service.

Refer to the following post for a quick-start guide that will help you
develop a new k8s charm using the Operator Framework:

    https://discourse.charmhub.io/t/4208
"""

import logging
from typing import Dict

from charms.tls_certificates_interface.v1.tls_certificates import (  # type: ignore[import]
    CertificateCreationRequestEvent,
    TLSCertificatesProvidesV1,
)
from cryptography import x509
from cryptography.x509.oid import NameOID
from ops.charm import CharmBase
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.pebble import ExecError

logger = logging.getLogger(__name__)


class LegoOperatorCharm(CharmBase):
    """Charm the service."""

    def __init__(self, *args):
        super().__init__(*args)
        self._container = self.unit.get_container("lego")
        self._email = "ghislain.bourgeois@canonical.com"
        self._server = "https://acme-staging-v02.api.letsencrypt.org/directory"
        self._plugin = "namecheap"
        self._secrets = {
            "NAMECHEAP_API_USER": "",
            "NAMECHEAP_API_KEY": "",
        }
        self.tls_certificates = TLSCertificatesProvidesV1(self, "certificates")
        self.framework.observe(self.on.lego_pebble_ready, self._on_lego_pebble_ready)
        self.framework.observe(
            self.tls_certificates.on.certificate_creation_request,
            self._on_certificate_creation_request,
        )

    def _on_lego_pebble_ready(self, event):
        self.unit.status = ActiveStatus()

    def _on_certificate_creation_request(self, event: CertificateCreationRequestEvent) -> None:
        logger.info("Received Certificate Creation Request")
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
            path="/tmp/csr.pem", make_dirs=True, source=event.certificate_signing_request.encode()
        )

        logger.info("Getting certificate for domain %s", subject)
        lego_cmd = [
            "lego",
            "--email",
            self._email,
            "--accept-tos",
            "--csr",
            "/tmp/csr.pem",
            "--server",
            self._server,
            "--dns",
            self._plugin,
            "run",
        ]

        process = self._container.exec(
            lego_cmd, timeout=300, working_dir="/tmp", environment=self._plugin_configs
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

        chain_pem = self._container.pull(path=f"/tmp/.lego/certificates/{subject}.crt")
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
    def _plugin_configs(self) -> Dict[str, str]:
        return self._secrets


if __name__ == "__main__":
    main(LegoOperatorCharm)
