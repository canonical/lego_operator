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

from ops.charm import CharmBase
from ops.framework import StoredState
from ops.main import main
from ops.model import ActiveStatus

logger = logging.getLogger(__name__)


class LegoOperatorCharm(CharmBase):
    """Charm the service."""

    _stored = StoredState()

    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(self.on.lego_pebble_ready, self._on_lego_pebble_ready)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self._stored.set_default(things=[])

    def _on_lego_pebble_ready(self, event):
        """Define and start a workload using the Pebble API.

        TEMPLATE-TODO: change this example to suit your needs.
        You'll need to specify the right entrypoint and environment
        configuration for your specific workload. Tip: you can see the
        standard entrypoint of an existing container using docker inspect

        Learn more about Pebble layers at https://github.com/canonical/pebble
        """
        # Get a reference the container attribute on the PebbleReadyEvent
        container = event.workload
        # Define an initial Pebble layer configuration
        pebble_layer = {
            "summary": "lego layer",
            "description": "pebble config layer for lego",
            "services": {
                "lego": {
                    "override": "replace",
                    "summary": "lego",
                    "command": "lego",
                    "startup": "disabled",
                    "environment": {"thing": self.model.config["thing"]},
                }
            },
        }
        # Add initial Pebble config layer using the Pebble API
        container.add_layer("lego", pebble_layer, combine=True)
        # Autostart any services that were defined with startup: enabled
        container.autostart()
        # Learn more about statuses in the SDK docs:
        # https://juju.is/docs/sdk/constructs#heading--statuses
        self.unit.status = ActiveStatus()

    def _on_config_changed(self, _):
        """Just an example to show how to deal with changed configuration.

        TEMPLATE-TODO: change this example to suit your needs.
        If you don't need to handle config, you can remove this method,
        the hook created in __init__.py for it, the corresponding test,
        and the config.py file.

        Learn more about config at https://juju.is/docs/sdk/config
        """
        current = self.config["thing"]
        if current not in self._stored.things:
            logger.debug("found a new thing: %r", current)
            self._stored.things.append(current)


if __name__ == "__main__":
    main(LegoOperatorCharm)
