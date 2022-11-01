# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

import unittest

from ops.model import ActiveStatus
from ops.testing import Harness

from charm import LegoOperatorCharm


class TestCharm(unittest.TestCase):
    def setUp(self):
        self.harness = Harness(LegoOperatorCharm)
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
