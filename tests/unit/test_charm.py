# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import yaml
from charms.acme_client_operator.v0.acme_client import AcmeClient  # type: ignore[import]
from charms.tls_certificates_interface.v1.tls_certificates import (  # type: ignore[import]
    generate_csr,
    generate_private_key,
)
from ops import testing
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.pebble import ExecError
from ops.testing import Harness

testing.SIMULATE_CAN_CONNECT = True
test_cert = Path(__file__).parent / "test_lego.crt"
TLS_LIB_PATH = "charms.tls_certificates_interface.v1.tls_certificates"


class MockExec:
    def __init__(self, *args, **kwargs):
        if "raise_exec_error" in kwargs:
            self.raise_exec_error = True
        else:
            self.raise_exec_error = False

    def exec(self, *args, **kwargs):
        pass

    def wait_output(self, *args, **kwargs):
        if self.raise_exec_error:
            raise ExecError(command="lego", exit_code=1, stdout="", stderr="")
        return "stdout", "stderr"


class AcmeTestCharm(AcmeClient):
    def __init__(self, *args):
        """Uses the Orc8rBase library to manage events."""
        super().__init__(*args, plugin="namecheap")

    @property
    def _plugin_config(self):
        return None


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
        self.r_id = self.harness.add_relation("certificates", "remote")
        self.harness.add_relation_unit(self.r_id, "remote/0")

    def test_given_empty_pebble_plan_when_pebble_ready_and_generic_config_is_not_set_then_status_is_blocked(
        self,
    ):
        container = self.harness.model.unit.get_container("lego")
        self.harness.charm.on.lego_pebble_ready.emit(container)
        self.assertEqual(
            self.harness.model.unit.status, BlockedStatus("Email address was not provided.")
        )

    def test_given_email_and_server_when_update_config_is_called_and_email_is_invalid_then_an_error_is_raised(
        self,
    ):
        with self.assertRaises(ValueError):
            self.harness.charm.update_generic_acme_config(
                email="invalid email", server="https://acme-v02.api.letsencrypt.org/directory"
            )

    def test_given_email_and_server_when_update_config_is_called_and_server_is_invalid_then_an_error_is_raised(
        self,
    ):
        with self.assertRaises(ValueError):
            self.harness.charm.update_generic_acme_config(
                email="example@email.com", server="not a valid URL"
            )

    def test_given_empty_pebble_plan_when_pebble_ready_and_generic_config_is_set_then_status_is_active(
        self,
    ):
        self.harness.charm.update_generic_acme_config(
            email="example@email.com", server="https://acme-v02.api.letsencrypt.org/directory"
        )
        container = self.harness.model.unit.get_container("lego")
        self.harness.charm.on.lego_pebble_ready.emit(container)
        self.assertEqual(self.harness.model.unit.status, ActiveStatus())

    @patch("ops.model.Container.exec", new=MockExec)
    @patch(
        f"{TLS_LIB_PATH}.TLSCertificatesProvidesV1.set_relation_certificate",
    )
    def test_given_cmd_when_certificate_creation_request_then_certificate_is_set_in_relation(
        self, mock_set_relation_certificate
    ):
        container = self.harness.model.unit.get_container("lego")
        container.push(
            "/tmp/.lego/certificates/foo.crt", source=test_cert.read_bytes(), make_dirs=True
        )
        csr = self.add_csr_to_remote_unit_relation_data()
        with open(test_cert, "r") as file:
            expected_certs = (file.read()).split("\n\n")
        mock_set_relation_certificate.assert_called_with(
            certificate=expected_certs[0],
            certificate_signing_request=csr,
            ca=expected_certs[-1],
            chain=list(reversed(expected_certs)),
            relation_id=self.r_id,
        )
        print(self.harness.charm.unit.status)

    @patch("ops.model.Container.exec", new_callable=Mock)
    def test_given_command_execution_fails_when_certificate_creation_request_then_request_fails_and_status_is_blocked(
        self, patch_exec
    ):
        patch_exec.return_value = MockExec(raise_exec_error=True)
        container = self.harness.model.unit.get_container("lego")
        container.push(
            "/tmp/.lego/certificates/foo.crt", source=test_cert.read_bytes(), make_dirs=True
        )

        self.add_csr_to_remote_unit_relation_data()
        assert self.harness.charm.unit.status == BlockedStatus(
            "Error getting certificate. Check logs for details"
        )

    def test_given_cannot_connect_to_container_when_certificate_creation_request_then_request_fails_and_status_is_waiting(  # noqa: E501
        self,
    ):
        self.harness.set_can_connect("lego", False)
        self.add_csr_to_remote_unit_relation_data()
        assert self.harness.charm.unit.status == WaitingStatus("Waiting for container to be ready")

    def add_csr_to_remote_unit_relation_data(self) -> str:
        """Add a CSR to the remote unit relation data.

        Returns: The CSR as a string.
        """
        csr = generate_csr(generate_private_key(), subject="foo")
        self.harness.update_relation_data(
            self.r_id,
            "remote/0",
            {
                "certificate_signing_requests": json.dumps(
                    [{"certificate_signing_request": csr.decode().strip()}]
                )
            },
        )
        return csr.decode().strip()

    @staticmethod
    def check_exec_args(harness, return_value, *args, **kwargs):
        assert args == (
            [
                "lego",
                "--email",
                harness._charm._email,
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
            "environment": harness._charm._plugin_config,
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
