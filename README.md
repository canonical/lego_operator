# lego-operator

## Description

LEGO operator implements the provider side of the `tls-certificates-interface`
to provide signed certificates from an ACME servers, using LEGO
(https://go-acme.github.io/lego).

## Usage

Deploy `lego-operator`:

`juju deploy lego-operator`

Relate it to a `tls-certificates-requirer` charm:

`juju relate lego-operator:certificates tls-certificates-requirer`

## Relations

`certificates`: `tls-certificates-interface` provider

## OCI Images

`goacme/lego`

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines on enhancements to this
charm following best practice guidelines, and
[CONTRIBUTING.md](https://github.com/ghislainbourgeois/lego-operator/blob/main/CONTRIBUTING.md) for developer
guidance.
