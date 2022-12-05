# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing
import json
import unittest
from functools import partial
from pathlib import Path
from unittest.mock import Mock

import yaml
from charms.acme_client_operator.v0.acme_client import AcmeClient  # type: ignore[import]
from charms.tls_certificates_interface.v1.tls_certificates import (  # type: ignore[import]
    TLSCertificatesProvidesV1,
    generate_csr,
    generate_private_key,
)
from ops import testing
from ops.charm import CharmBase
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.pebble import ExecError
from ops.testing import Harness

testing.SIMULATE_CAN_CONNECT = True
test_lego = Path(__file__).parent / "test_lego.crt"


class AcmeTestCharm(CharmBase):
    def __init__(self, *args):
        """Uses the Orc8rBase library to manage events."""
        self._server = "https://acme-staging-v02.api.letsencrypt.org/directory"
        super().__init__(*args)
        lego_cmd = [
            "lego",
            "--email",
            self.email,
            "--accept-tos",
            "--csr",
            "/tmp/csr.pem",
            "--server",
            self._server,
            "--dns",
            "namecheap",
            "run",
        ]
        self._acme_client_operator = AcmeClient(self, lego_cmd, "/tmp/csr.pem", {})
        self.tls_certificates = TLSCertificatesProvidesV1(self, "certificates")
        self.framework.observe(
            self.tls_certificates.on.certificate_creation_request,
            self._acme_client_operator.on_certificate_creation_request,
        )

    @property
    def email(self) -> str:
        return "example@email.com"

    @property
    def domain(self) -> str:
        return "example.com"

    @property
    def additional_config(self):
        return {}


class TestCharm(unittest.TestCase):
    def setUp(self):
        self.harness = Harness(
            AcmeTestCharm,
            meta=yaml.safe_dump(
                {
                    "name": "lego",
                    "containers": {"lego": {"resource": "lego-image"}},
                    "provides": {"certificates": {"interface": "tls-certificates"}},
                }
            ),
        )

        self.harness.set_leader(True)
        self.harness.set_can_connect("lego", True)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    def test_lego_pebble_ready(self):
        # Check the initial Pebble plan is empty
        initial_plan = self.harness.get_container_pebble_plan("lego")
        self.assertEqual(initial_plan.to_yaml(), "{}\n")
        # Expected plan after Pebble ready with default config
        expected_plan = {}
        container = self.harness.model.unit.get_container("lego")
        self.harness.charm.on.lego_pebble_ready.emit(container)
        # Get the plan now we've run PebbleReady
        updated_plan = self.harness.get_container_pebble_plan("lego").to_dict()
        # Check we've got the plan we expected
        self.assertEqual(expected_plan, updated_plan)
        # Ensure we set an ActiveStatus with no message
        self.assertEqual(self.harness.model.unit.status, ActiveStatus())

    def request_cert(self):
        r_id = self.harness.add_relation("certificates", "remote")
        self.harness.add_relation_unit(r_id, "remote/0")
        csr = generate_csr(generate_private_key(), subject="foo")
        self.harness.update_relation_data(
            r_id,
            "remote/0",
            {
                "certificate_signing_requests": json.dumps(
                    [{"certificate_signing_request": csr.decode().strip()}]
                )
            },
        )

    @staticmethod
    def check_exec_args(harness, return_value, *args, **kwargs):
        assert args == (
            [
                "lego",
                "--email",
                harness._charm.email,
                "--accept-tos",
                "--csr",
                "/tmp/csr.pem",
                "--server",
                harness._charm._server,
                "--dns",
                "namecheap",
                "run",
            ],
        )

        assert kwargs == {
            "timeout": 300,
            "working_dir": "/tmp",
            "environment": harness._charm.additional_config,
            "combine_stderr": False,
            "encoding": "utf-8",
            "group": None,
            "group_id": None,
            "user": None,
            "user_id": None,
            "stderr": None,
            "stdin": None,
            "stdout": None,
        }

        return return_value

    def test_request(self):
        self.harness._backend._pebble_clients["lego"].exec = partial(
            self.check_exec_args, self.harness, Mock(wait_output=lambda: (None, None))
        )
        self.harness._backend._pebble_clients["lego"].push(
            "/tmp/.lego/certificates/foo.crt", source=test_lego.read_bytes(), make_dirs=True
        )

        self.request_cert()

    def test_failing_request(self):
        self.harness._backend._pebble_clients["lego"].exec = partial(
            self.check_exec_args,
            self.harness,
            Mock(**{"wait_output.side_effect": ExecError("lego", 1, "barf", "rip")}),
        )
        self.harness._backend._pebble_clients["lego"].push(
            "/tmp/.lego/certificates/foo.crt", source=test_lego.read_bytes(), make_dirs=True
        )

        self.request_cert()
        assert self.harness.charm.unit.status == BlockedStatus(
            "Error getting certificate. Check logs for details"
        )

    def test_cannot_connect(self):
        self.harness.set_can_connect("lego", False)
        self.request_cert()
        assert self.harness.charm.unit.status == WaitingStatus("Waiting for container to be ready")
