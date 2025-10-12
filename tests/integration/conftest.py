# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import json
import logging
import pathlib
import subprocess
from platform import machine

import jubilant
import pytest
from jubilant import Juju
from tenacity import Retrying, stop_after_delay, wait_fixed

MICROK8S_CLOUD_NAME = "mk8s"
MICROK8S_CONTROLLER_NAME = "mk8s-controller"


platforms = {
    "x86_64": "amd64",
    "aarch64": "arm64",
}


logger = logging.getLogger(__name__)


@pytest.fixture(scope="package")
def arch() -> str:
    """Fixture to provide the platform architecture for testing."""
    return platforms.get(machine(), "amd64")


@pytest.fixture
def platform() -> str:
    """Fixture to provide the platform architecture for testing."""
    return platforms.get(machine(), "amd64")


@pytest.fixture
def charm(platform: str) -> str:
    """Path to the charm file to use for testing."""
    # Return str instead of pathlib.Path since python-libjuju's model.deploy(), juju deploy, and
    # juju bundle files expect local charms to begin with `./` or `/` to distinguish them from
    # Charmhub charms.
    return f"./charmed-etcd_ubuntu@24.04-{platform}.charm"


@pytest.fixture(scope="module")
def juju(arch: str):
    with jubilant.temp_model() as juju:
        juju.wait_timeout = 1000
        juju.cli("set-model-constraints", f"arch={arch}")
        yield juju


@pytest.fixture(scope="module")
async def k8s_cloud(juju: Juju):
    clouds = json.loads(juju.cli("clouds", "--format", "json", include_model=False))
    for cloud, details in clouds.items():
        if "k8s" == details.get("type"):
            logger.info(f"Identified K8s cloud: {cloud}")
            yield cloud
            return

    try:
        subprocess.run(["sudo", "snap", "install", "--classic", "microk8s"], check=True)
        subprocess.run(["sudo", "snap", "install", "--classic", "kubectl"], check=True)
        subprocess.run(["sudo", "microk8s", "enable", "dns"], check=True)
        subprocess.run(["sudo", "microk8s", "enable", "hostpath-storage"], check=True)
        subprocess.run(
            ["sudo", "microk8s", "enable", "metallb:10.64.140.43-10.64.140.49"],
            check=True,
        )

        # Configure kubectl now
        subprocess.run(["mkdir", "-p", str(pathlib.Path.home() / ".kube")], check=True)
        kubeconfig = subprocess.check_output(["sudo", "microk8s", "config"])
        with open(str(pathlib.Path.home() / ".kube" / "config"), "w") as f:
            f.write(kubeconfig.decode())
        for attempt in Retrying(stop=stop_after_delay(150), wait=wait_fixed(15)):
            with attempt:
                if (
                    len(
                        subprocess.check_output(
                            "kubectl get po -A  --field-selector=status.phase!=Running",
                            shell=True,
                            stderr=subprocess.DEVNULL,
                        ).decode()
                    )
                    != 0
                ):  # We got sth different from "No resources found." in stderr
                    raise Exception()

        # Add microk8s to the kubeconfig
        juju.cli("add-k8s", MICROK8S_CLOUD_NAME)
        juju.bootstrap(MICROK8S_CLOUD_NAME, MICROK8S_CONTROLLER_NAME)

    except subprocess.CalledProcessError as e:
        pytest.exit(str(e))

    yield None

    juju.cli(
        "remove-cloud", "--client", "--controller", MICROK8S_CONTROLLER_NAME, MICROK8S_CLOUD_NAME
    )
    subprocess.run(["sudo", "snap", "remove", "--purge", "microk8s"], check=True)
    subprocess.run(["sudo", "snap", "remove", "--purge", "kubectl"], check=True)


@pytest.fixture(scope="module")
def k8s_controller(k8s_cloud: str, juju: Juju):
    controllers = json.loads(juju.cli("controllers", "--format", "json", include_model=False))
    for controller, details in controllers.get("controllers").items():
        if k8s_cloud == details.get("cloud"):
            logger.info(f"Identified K8s controller: {controller}")
            yield controller


@pytest.fixture(scope="module")
def lxd_cloud(juju: Juju):
    clouds = json.loads(juju.cli("clouds", "--format", "json", include_model=False))
    for cloud, details in clouds.items():
        if "lxd" == details.get("type"):
            logger.info(f"Identified LXD cloud: {cloud}")
            yield cloud


@pytest.fixture(scope="module")
def lxd_controller(lxd_cloud: str, juju: Juju):
    controllers = json.loads(juju.cli("controllers", "--format", "json", include_model=False))
    for controller, details in controllers.get("controllers").items():
        if lxd_cloud == details.get("cloud"):
            logger.info(f"Identified LXD controller: {controller}")
            yield controller


@pytest.fixture(scope="module")
def juju_lxd(arch: str, lxd_cloud: str, lxd_controller):
    with jubilant.temp_model(cloud=lxd_cloud, controller=lxd_controller) as juju_lxd:
        juju_lxd.wait_timeout = 1000
        juju_lxd.cli("set-model-constraints", f"arch={arch}")
        yield juju_lxd


@pytest.fixture(scope="module")
def juju_k8s(arch: str, k8s_cloud: str, k8s_controller: str):
    with jubilant.temp_model(cloud=k8s_cloud, controller=k8s_controller) as juju_k8s:
        juju_k8s.wait_timeout = 1000
        juju_k8s.cli("set-model-constraints", f"arch={arch}")
        yield juju_k8s
