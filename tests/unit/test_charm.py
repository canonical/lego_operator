# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing
import json
from functools import partial
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml
from charms.tls_certificates_interface.v1.tls_certificates import (  # type: ignore[import]
    generate_csr,
    generate_private_key,
)
from ops import testing
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.pebble import ExecError
from ops.testing import Harness

from charm import LegoOperatorCharm

testing.SIMULATE_CAN_CONNECT = True
test_lego = Path(__file__).parent / "test_lego.crt"


@pytest.fixture(scope="function")
def harness():
    harness = Harness(
        LegoOperatorCharm,
        meta=yaml.safe_dump(
            {
                "name": "lego",
                "containers": {"lego": {"resource": "lego-image"}},
                "provides": {"certificates": {"interface": "tls-certificates"}},
            }
        ),
    )

    harness.set_leader(True)
    setup_lego_container(harness)
    harness.begin()
    yield harness
    harness.cleanup()


def setup_lego_container(harness: Harness):
    harness.set_can_connect("lego", True)


def test_lego_pebble_ready(harness):
    # Check the initial Pebble plan is empty
    initial_plan = harness.get_container_pebble_plan("lego")
    assert initial_plan.to_yaml() == "{}\n"
    # Expected plan after Pebble ready with default config
    expected_plan = {}
    container = harness.model.unit.get_container("lego")
    harness.charm.on.lego_pebble_ready.emit(container)
    # Get the plan now we've run PebbleReady
    updated_plan = harness.get_container_pebble_plan("lego").to_dict()
    # Check we've got the plan we expected
    assert expected_plan == updated_plan
    # Ensure we set an ActiveStatus with no message
    assert harness.model.unit.status == ActiveStatus()


def request_cert(harness):
    r_id = harness.add_relation("certificates", "remote")
    harness.add_relation_unit(r_id, "remote/0")
    csr = generate_csr(generate_private_key(), subject="foo")
    harness.update_relation_data(
        r_id,
        "remote/0",
        {
            "certificate_signing_requests": json.dumps(
                [{"certificate_signing_request": csr.decode().strip()}]
            )
        },
    )


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
            harness._charm._plugin,
            "run",
        ],
    )
    assert kwargs == {
        "timeout": 300,
        "working_dir": "/tmp",
        "environment": harness._charm._plugin_configs,
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


def test_request(harness):
    harness._backend._pebble_clients["lego"].exec = partial(
        check_exec_args, harness, Mock(wait_output=lambda: (None, None))
    )
    harness._backend._pebble_clients["lego"].push(
        "/tmp/.lego/certificates/foo.crt", source=test_lego.read_bytes(), make_dirs=True
    )

    request_cert(harness)


def test_failing_request(harness):
    harness._backend._pebble_clients["lego"].exec = partial(
        check_exec_args,
        harness,
        Mock(**{"wait_output.side_effect": ExecError("lego", 1, "barf", "rip")}),
    )
    harness._backend._pebble_clients["lego"].push(
        "/tmp/.lego/certificates/foo.crt", source=test_lego.read_bytes(), make_dirs=True
    )

    request_cert(harness)
    assert harness.charm.unit.status == BlockedStatus(
        "Error getting certificate. Check logs for details"
    )


def test_cannot_connect(harness):
    harness.set_can_connect("lego", False)
    request_cert(harness)
    assert harness.charm.unit.status == WaitingStatus("Waiting for container to be ready")
