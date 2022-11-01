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

from charms.tls_certificates_interface.v1.tls_certificates import (  # type: ignore[import]
    CertificateCreationRequestEvent,
    TLSCertificatesProvidesV1,
)
from cryptography import x509
from cryptography.x509.oid import NameOID
from ops.charm import CharmBase
from ops.main import main
from ops.model import ActiveStatus, WaitingStatus
from ops.pebble import ExecError

logger = logging.getLogger(__name__)


class LegoOperatorCharm(CharmBase):
    """Charm the service."""

    def __init__(self, *args):
        super().__init__(*args)
        self._container = self.unit.get_container("lego")
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
            csr = x509.load_pem_x509_csr(event.certificate_signing_request)
            subject = csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
        except IndexError:
            logger.error("Bad CSR received, aborting")
            return

        self._container.push(path="/tmp/csr.pem", source=event.certificate_signing_request)

        logger.info("Getting certificate for domain %s", subject)
        lego_cmd = [
            "lego",
            "--email",
            "ghislain.bourgeois@canonical.com",
            "--accept-tos",
            "--csr",
            "/tmp/csr.pem",
            "--server",
            "https://acme-staging-v02.api.letsencrypt.org/directory",
            "--dns",
            "namecheap",
            "run",
        ]

        process = self._container.exec(lego_cmd, timeout=300, working_dir="/tmp")
        try:
            stdout, error = process.wait_output()
            logger.info(f"Return message: {stdout}, {error}")
        except ExecError as e:
            logger.error("Exited with code %d. Stderr:", e.exit_code)
            for line in e.stderr.splitlines():  # type: ignore
                logger.error("    %s", line)

        certificate = self._container.pull(path="/tmp/.lego/cert.pem")
        ca_certificate = None

        self.tls_certificates.set_relation_certificate(
            certificate=certificate,
            certificate_signing_request=event.certificate_signing_request,
            ca=ca_certificate,
            chain=[ca_certificate, certificate],
            relation_id=event.relation_id,
        )


if __name__ == "__main__":
    main(LegoOperatorCharm)
