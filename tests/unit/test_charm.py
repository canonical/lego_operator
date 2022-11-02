# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing
import base64
import json
import unittest
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml
from ops.model import ActiveStatus

from ops import testing

testing.SIMULATE_CAN_CONNECT = True

from ops.testing import Harness

from charm import LegoOperatorCharm
from charms.tls_certificates_interface.v1.tls_certificates import generate_csr, generate_private_key

test_lego = Path(__file__).parent / 'test_lego.crt'

@pytest.fixture(scope='function')
def harness():
    harness = Harness(LegoOperatorCharm, meta=yaml.safe_dump(
        {'name': 'lego',
         'containers':
             {'lego':
                  {'resource': 'lego-image'}},
         'provides': {'certificates': {'interface': 'tls-certificates'}}})
                      )

    harness.set_leader(True)
    setup_lego_container(harness)
    harness.begin()
    yield harness
    harness.cleanup()


def setup_lego_container(harness: Harness):
    harness.set_can_connect('lego', True)


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
    r_id = harness.add_relation('certificates', 'remote')
    harness.add_relation_unit(r_id, 'remote/0')
    csr = generate_csr(generate_private_key(), subject='foo')
    harness.update_relation_data(r_id, 'remote/0', {
        "certificate_signing_requests": json.dumps(
            [{'certificate_signing_request':
                  csr.decode().strip()
              }])
    })


def test_request(harness):
    def _check_exec_args(*args, **kwargs):
        # todo verify args/kwargs
        return Mock(wait_output=lambda: (None, None))

    harness._backend._pebble_clients['lego'].exec = _check_exec_args
    harness._backend._pebble_clients['lego'].push(
        f"/tmp/.lego/certificates/foo.crt",
        source=test_lego.read_bytes(),
        make_dirs=True)

    request_cert(harness)
