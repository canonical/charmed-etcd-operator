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
CONCIERGE_MODEL_NAME = "testing"

logger = logging.getLogger(__name__)


@pytest.fixture(scope="package")
def arch() -> str:
    """Fixture to provide the platform architecture for testing."""
    platforms = {
        "x86_64": "amd64",
        "aarch64": "arm64",
    }
    return platforms.get(machine(), "amd64")


@pytest.fixture
def charm(arch: str) -> str:
    """Path to the charm file to use for testing."""
    # Return str instead of pathlib.Path since python-libjuju's model.deploy(), juju deploy, and
    # juju bundle files expect local charms to begin with `./` or `/` to distinguish them from
    # Charmhub charms.
    return f"./charmed-etcd_ubuntu@24.04-{arch}.charm"


@pytest.fixture(scope="module")
def juju(arch: str):
    with jubilant.temp_model() as juju:
        juju.wait_timeout = 1000
        juju.cli("set-model-constraints", f"arch={arch}")
        yield juju


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
async def k8s_cloud(arch: str, lxd_controller: str, juju: Juju):
    """Provision a microk8s cloud, if a k8s cloud isn't already present, and return the name. Do so only if running on amd64.

    This is because arm64 isn't supported by the cos-lite charms for which we are provisioning this k8s cloud.
    """
    if "amd64" != arch:
        pytest.skip("k8s cloud provisioning for COS-lite integration tests only done on amd64")

    clouds = json.loads(juju.cli("clouds", "--format", "json", include_model=False))
    for cloud, details in clouds.items():
        if "k8s" == details.get("type"):
            logger.info(f"Identified existing k8s cloud: {cloud}")
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

        # add this microk8s as a juju k8s cloud, by explicitly providing its config
        # this is done to bypass the issue with juju 3.9 necessitating strictly confined microk8s
        config = kubeconfig.decode()
        juju.cli(
            "add-k8s",
            MICROK8S_CLOUD_NAME,
            "--client",
            "--controller",
            lxd_controller,
            stdin=config,
            include_model=False,
        )

    except subprocess.CalledProcessError as e:
        pytest.exit(str(e))

    yield MICROK8S_CLOUD_NAME

    models = json.loads(juju.cli("models", "--format", "json", include_model=False))
    for model in models["models"]:
        if MICROK8S_CLOUD_NAME == model.get("cloud"):
            logger.info(f"Destroying model {model.get('name')}...")
            juju.destroy_model(model=model.get("name"), destroy_storage=True, force=True)

    juju.cli(
        "remove-k8s",
        "--client",
        MICROK8S_CLOUD_NAME,
        "--controller",
        lxd_controller,
        include_model=False,
    )
    subprocess.run(["sudo", "snap", "remove", "--purge", "microk8s"], check=True)
    subprocess.run(["sudo", "snap", "remove", "--purge", "kubectl"], check=True)


@pytest.fixture(scope="module")
def juju_lxd_model(arch: str, lxd_cloud: str, lxd_controller: str, juju: Juju):
    # if concierge model ("testing") is found, such as on CI, continue with this. Else setup temp model.
    models = json.loads(juju.cli("models", "--format", "json", include_model=False))
    for model in models["models"]:
        if CONCIERGE_MODEL_NAME == model["short-name"]:
            juju_lxd = jubilant.Juju(model=f"{lxd_controller}:testing", wait_timeout=1000)
            juju_lxd.cli("set-model-constraints", f"arch={arch}")
            yield juju_lxd
            return

    with jubilant.temp_model(cloud=lxd_cloud, controller=lxd_controller) as juju_lxd:
        juju_lxd.wait_timeout = 1000
        juju_lxd.cli("set-model-constraints", f"arch={arch}")
        yield juju_lxd


@pytest.fixture(scope="module")
def juju_k8s_model(arch: str, k8s_cloud: str, lxd_controller: str):
    with jubilant.temp_model(cloud=k8s_cloud, controller=lxd_controller) as juju_k8s:
        juju_k8s.wait_timeout = 1000
        juju_k8s.cli("set-model-constraints", f"arch={arch}")
        yield juju_k8s
