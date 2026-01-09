#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""This helpers-file could be replaced by the `data_platform_helpers`.

Prerequisite: PR https://github.com/canonical/data-platform-helpers/pull/12 has been merged
"""

import logging
import subprocess

import yaml
from jubilant import Juju
from pytest_operator.plugin import OpsTest
from tenacity import RetryError, Retrying, stop_after_attempt, wait_fixed

logger = logging.getLogger(__name__)


async def hostname_from_unit(ops_test: OpsTest, unit_name: str) -> str:
    """Get the machine hostname from a specific unit.

    Args:
        ops_test: The ops test framework instance
        unit_name: The name of the unit to get the machine

    Returns:
        The hostname of the machine.
    """
    run_command = ["exec", "--unit", unit_name, "--", "hostname"]
    _, hostname, _ = await ops_test.juju(*run_command)

    return hostname.strip()


def hostname_from_unit_jubilant(juju: Juju, unit_name: str) -> str:
    """Get the machine hostname from a specific unit.

    Args:
        juju: An instance of Jubilant's Juju class on which to run Juju commands
        unit_name: The name of the unit to get the machine

    Returns:
        The hostname of the machine.
    """
    task_result = juju.exec(command="hostname", unit=unit_name)

    return task_result.stdout


async def ip_address_from_unit(ops_test: OpsTest, unit_name: str) -> str:
    """Get the machine ip address from a specific unit.

    Args:
        ops_test: The ops test framework instance
        unit_name: The name of the unit to get the machine

    Returns:
        The ip address of the machine.
    """
    run_command = ["exec", "--unit", unit_name, "--", "hostname", "-i"]
    _, ip_address, _ = await ops_test.juju(*run_command)

    return ip_address.strip()


def ip_address_from_unit_jubilant(juju: Juju, unit_name: str) -> str:
    """Get the machine ip address from a specific unit.

    Args:
        juju: An instance of Jubilant's Juju class on which to run Juju commands
        unit_name: The name of the unit to get the machine

    Returns:
        The ip address of the machine.
    """
    task_result = juju.exec("hostname -i", unit=unit_name)

    return task_result.stdout


async def get_controller_hostname(ops_test: OpsTest) -> str:
    """Return controller machine hostname."""
    _, raw_controller, _ = await ops_test.juju("show-controller")

    controller = yaml.safe_load(raw_controller.strip())

    return [
        machine.get("instance-id")
        for machine in controller[ops_test.controller_name]["controller-machines"].values()
    ][0]


def get_controller_hostname_jubilant(juju: Juju) -> str:
    """Return controller machine hostname."""
    raw_model = juju.cli("show-model", juju.model, include_model=False)
    raw_controller = juju.cli("show-controller", include_model=False)

    model_details = yaml.safe_load(raw_model)
    controller_details = yaml.safe_load(raw_controller)
    controller_name = model_details[juju.model]["controller-name"]

    return [
        machine.get("instance-id")
        for machine in controller_details[controller_name]["controller-machines"].values()
    ][0]


def cut_network_from_unit_with_ip_change(machine_name: str) -> None:
    """Cut network from a lxc container in a way the changes the IP."""
    # apply a mask (device type `none`)
    cut_network_command = f"lxc config device add {machine_name} eth0 none"
    subprocess.check_call(cut_network_command.split())


def cut_network_from_unit_without_ip_change(machine_name: str) -> None:
    """Cut network from a lxc container (without causing the change of the unit IP address)."""
    override_command = f"lxc config device override {machine_name} eth0"
    try:
        subprocess.check_call(override_command.split())
    except subprocess.CalledProcessError:
        # Ignore if the interface was already overridden.
        pass

    limit_set_command = f"lxc config device set {machine_name} eth0 limits.egress=0kbit"
    subprocess.check_call(limit_set_command.split())
    limit_set_command = f"lxc config device set {machine_name} eth0 limits.ingress=1kbit"
    subprocess.check_call(limit_set_command.split())
    limit_set_command = f"lxc config device set {machine_name} eth0 limits.priority=10"
    subprocess.check_call(limit_set_command.split())


def restore_network_for_unit_with_ip_change(machine_name: str) -> None:
    """Restore network from a lxc container by removing mask from eth0."""
    restore_network_command = f"lxc config device remove {machine_name} eth0"
    subprocess.check_call(restore_network_command.split())


def restore_network_for_unit_without_ip_change(machine_name: str) -> None:
    """Restore network from a lxc container (without causing the change of the unit IP address)."""
    limit_set_command = f"lxc config device set {machine_name} eth0 limits.egress="
    subprocess.check_call(limit_set_command.split())
    limit_set_command = f"lxc config device set {machine_name} eth0 limits.ingress="
    subprocess.check_call(limit_set_command.split())
    limit_set_command = f"lxc config device set {machine_name} eth0 limits.priority="
    subprocess.check_call(limit_set_command.split())


def is_unit_reachable(from_host: str, to_host: str) -> bool:
    """Test network reachability between hosts."""
    try:
        for attempt in Retrying(stop=stop_after_attempt(10), wait=wait_fixed(10)):
            with attempt:
                ping = subprocess.call(
                    f"lxc exec {from_host} -- ping -c 5 {to_host}".split(),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if ping == 0:
                    return True
                else:
                    raise ValueError
    except RetryError:
        return False
