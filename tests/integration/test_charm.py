#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import json
import logging

import jubilant
import pytest
import requests
from jubilant import Juju

from literals import INTERNAL_USER, INTERNAL_USER_PASSWORD_CONFIG, METRICS_PORT, PEER_RELATION
from statuses import CharmStatuses

from .helpers import (
    APP_NAME,
    COS_CHANNEL,
    COS_RELATION_NAME,
    GRAFANA_AGENT_APP_NAME,
    GRAFANA_APP_NAME,
    LOKI_APP_NAME,
    PROMETHEUS_APP_NAME,
    fast_forward,
    get_cluster_endpoints_jubilant,
    get_cluster_members,
    get_key,
    get_leader_unit_ip,
    get_leader_unit_name_jubilant,
    get_secret_by_label_jubilant,
    get_unit_relation_data,
    put_key,
    set_password_jubilant,
)

logger = logging.getLogger(__name__)

NUM_UNITS = 3
TEST_KEY = "test_key"
TEST_VALUE = "42"
ADMIN = "admin"


# TODO jubilant: move common fixtures to conftest.py when all modules migrated
@pytest.fixture(scope="module")
def juju(arch: str):
    with jubilant.temp_model() as juju:
        juju.wait_timeout = 1000
        juju.cli("set-model-constraints", f"arch={arch}")
        yield juju


@pytest.fixture(scope="module")
def k8s_cloud(juju: Juju):
    clouds = json.loads(juju.cli("clouds", "--format", "json", include_model=False))
    for cloud, details in clouds.items():
        if "k8s" == details.get("type"):
            logger.info(f"Identified K8s cloud: {cloud}")
            yield cloud


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


@pytest.mark.abort_on_fail
def test_build_and_deploy(charm: str, juju_lxd: Juju) -> None:
    """Build the charm-under-test and deploy it with three units.

    The initial cluster should be formed and accessible.
    """  # Deploy the charm and wait for active/idle status
    juju_lxd.deploy(charm, num_units=NUM_UNITS)
    juju_lxd.wait(lambda status: jubilant.all_active(status, APP_NAME))

    # check if all units have been added to the cluster
    endpoints = get_cluster_endpoints_jubilant(juju_lxd, APP_NAME)
    secret = get_secret_by_label_jubilant(juju_lxd, label=f"{PEER_RELATION}.{APP_NAME}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    cluster_members = get_cluster_members(endpoints, user=INTERNAL_USER, password=password)
    assert len(cluster_members) == NUM_UNITS

    # make sure data can be written to the cluster
    assert (
        put_key(
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=TEST_KEY,
            value=TEST_VALUE,
        )
        == "OK"
    )
    assert get_key(endpoints, user=INTERNAL_USER, password=password, key=TEST_KEY) == TEST_VALUE


@pytest.mark.abort_on_fail
def test_authentication(juju_lxd: Juju) -> None:
    """Assert authentication is enabled by default."""
    endpoints = get_cluster_endpoints_jubilant(juju_lxd, APP_NAME)

    # check that reading/writing data without credentials fails
    assert get_key(endpoints, key=TEST_KEY) != TEST_VALUE
    assert put_key(endpoints, key=TEST_KEY, value=TEST_VALUE) != "OK"


@pytest.mark.abort_on_fail
def test_update_admin_password(juju_lxd: Juju) -> None:
    """Assert the admin password is updated when adding a user secret to the config."""
    endpoints = get_cluster_endpoints_jubilant(juju_lxd, APP_NAME)

    # create a user secret and grant it to the application
    new_password = "some-password"
    set_password_jubilant(juju_lxd, new_password)

    # wait for config-changed hook to finish executing
    juju_lxd.wait(lambda status: jubilant.all_agents_idle(status, APP_NAME), timeout=1200)

    # perform read operation with the updated password
    assert (
        get_key(endpoints, user=INTERNAL_USER, password=new_password, key=TEST_KEY) == TEST_VALUE
    )

    # update the config again and remove the option `admin-password`
    juju_lxd.config(app=APP_NAME, reset=[INTERNAL_USER_PASSWORD_CONFIG])

    # wait for config-changed hook to finish executing
    juju_lxd.wait(lambda status: jubilant.all_agents_idle(status, APP_NAME), timeout=1200)

    # make sure we can still read data with the previously set password
    assert (
        get_key(endpoints, user=INTERNAL_USER, password=new_password, key=TEST_KEY) == TEST_VALUE
    )


@pytest.mark.abort_on_fail
async def test_user_secret_permissions(juju_lxd: jubilant.Juju) -> None:
    """If a user secret is not granted, ensure we can process updated permissions."""
    endpoints = get_cluster_endpoints_jubilant(juju_lxd, APP_NAME)

    logger.info("Creating new user secret")
    secret_name = "my_secret"
    new_password = "even-newer-password"
    secret_id = juju_lxd.add_secret(name=secret_name, content={INTERNAL_USER: new_password})

    logger.info("Updating configuration with the new secret - but without access")
    juju_lxd.config(app=APP_NAME, values={INTERNAL_USER_PASSWORD_CONFIG: secret_id})

    def secret_access_error(status: jubilant.Status, app: str) -> bool:
        return (
            CharmStatuses.SECRET_ACCESS_ERROR.value.message == status.apps[app].app_status.message
        )

    juju_lxd.wait(
        lambda status: secret_access_error(status, APP_NAME),
        timeout=1200,
    )

    logger.info("Secret access will be granted now - wait for updated password")
    # deferred `config_changed` event will be retried before `update_status`
    with fast_forward(juju_lxd):
        juju_lxd.grant_secret(identifier=secret_name, app=APP_NAME)

    juju_lxd.wait(
        lambda status: jubilant.all_active(status, APP_NAME),
        timeout=1200,
    )

    # perform read operation with the updated password
    assert (
        get_key(endpoints, user=INTERNAL_USER, password=new_password, key=TEST_KEY) == TEST_VALUE
    ), "password update failed"

    logger.info("Password update successful after secret was granted")


@pytest.mark.abort_on_fail
async def test_etcd_metrics_endpoint(juju_lxd: Juju):
    # direct metrics scrape
    leader_unit_ip = get_leader_unit_ip(juju_lxd, app=APP_NAME)
    endpoint = f"http://{leader_unit_ip}:{METRICS_PORT}/metrics"
    resp = requests.get(endpoint)
    text = resp.content.decode("utf-8")
    assert "etcd_server_has_leader" in text
    assert len(text.splitlines()) > 50


@pytest.mark.abort_on_fail
def test_etcd_metrics_cos_relation(juju_lxd: Juju, juju_k8s: Juju, k8s_controller: str):
    # deploy COS essentials for grafana-agent
    juju_k8s.deploy("cos-lite", trust=True)
    juju_k8s.wait(jubilant.all_active)
    juju_k8s.wait(jubilant.all_agents_idle)

    juju_k8s_model = juju_k8s.model.split(":")[1]

    # offer COS interfaces to be cross-model related with the machine model
    juju_k8s.offer(
        app=f"{juju_k8s_model}.{LOKI_APP_NAME}", endpoint="logging", controller=k8s_controller
    )
    juju_k8s.offer(
        app=f"{juju_k8s_model}.{PROMETHEUS_APP_NAME}",
        endpoint="receive-remote-write",
        controller=k8s_controller,
    )
    juju_k8s.offer(
        app=f"{juju_k8s_model}.{GRAFANA_APP_NAME}",
        endpoint="grafana-dashboard",
        controller=k8s_controller,
    )
    juju_k8s.wait(jubilant.all_agents_idle)

    # consume the offers on the machine model
    juju_lxd.consume(
        model_and_app=f"{juju_k8s_model}.{GRAFANA_APP_NAME}",
        controller=k8s_controller,
        owner=ADMIN,
    )
    juju_lxd.consume(
        model_and_app=f"{juju_k8s_model}.{LOKI_APP_NAME}", controller=k8s_controller, owner=ADMIN
    )
    juju_lxd.consume(
        model_and_app=f"{juju_k8s_model}.{PROMETHEUS_APP_NAME}",
        controller=k8s_controller,
        owner=ADMIN,
    )

    # deploy grafana-agent and integrate
    juju_lxd.deploy(GRAFANA_AGENT_APP_NAME, channel=COS_CHANNEL)
    juju_lxd.integrate(APP_NAME, GRAFANA_AGENT_APP_NAME)
    juju_lxd.wait(
        lambda status: jubilant.all_agents_idle(status, GRAFANA_AGENT_APP_NAME, APP_NAME),
        timeout=1200,
    )
    juju_lxd.integrate(GRAFANA_AGENT_APP_NAME, GRAFANA_APP_NAME)
    juju_lxd.integrate(GRAFANA_AGENT_APP_NAME, LOKI_APP_NAME)
    juju_lxd.integrate(GRAFANA_AGENT_APP_NAME, PROMETHEUS_APP_NAME)
    juju_lxd.wait(
        lambda status: jubilant.all_agents_idle(status, GRAFANA_AGENT_APP_NAME, APP_NAME),
        timeout=1200,
    )

    # get relation data sent to grafana-agent
    cos_leader_name = get_leader_unit_name_jubilant(juju_lxd, GRAFANA_AGENT_APP_NAME)
    leader_name = get_leader_unit_name_jubilant(juju_lxd, APP_NAME)
    relation_data = get_unit_relation_data(
        juju_lxd, cos_leader_name, leader_name, COS_RELATION_NAME, "config"
    )
    if not isinstance(relation_data, dict):
        relation_data = json.loads(relation_data)

    # assert that right targets are set in grafana-agent
    scrape_job = relation_data["metrics_scrape_jobs"][0]
    assert scrape_job["static_configs"][0]["targets"][0].endswith(f":{METRICS_PORT}")

    # assert that etcd metrics show up in prometheus
    result = juju_k8s.run(action="show-proxied-endpoints", unit="traefik/0")
    proxied_endpoints = json.loads(result.results["proxied-endpoints"])
    prometheus_url = proxied_endpoints["prometheus/0"]["url"]
    prometheus_endpoint = f"{prometheus_url}/api/v1/label/__name__/values"

    prometheus_metrics_raw = requests.get(prometheus_endpoint)
    prometheus_metrics_raw.raise_for_status()
    all_metrics = prometheus_metrics_raw.json()["data"]
    etcd_metrics = [m for m in all_metrics if "etcd" in m]
    assert etcd_metrics, "No etcd-related metrics found in Prometheus"
    assert (
        "etcd_server_has_leader" in etcd_metrics
        or "etcd_server_leader_changes_seen_total" in etcd_metrics
    )
