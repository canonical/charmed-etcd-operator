import dataclasses
import json
import logging
import os
import socket
import subprocess
import time

import boto3
import botocore.exceptions
import pytest


@dataclasses.dataclass(frozen=True)
class ConnectionInformation:
    access_key_id: str
    secret_access_key: str
    bucket: str


@pytest.fixture(scope="session")
def microceph() -> ConnectionInformation:
    """Deploy microceph with rados-gateway and provide the credentials to access it."""
    if not os.environ.get("CI") == "true":
        raise Exception("Not running on CI. Skipping microceph installation. ")
    logger.info("Setting up microceph")
    subprocess.run(["sudo", "snap", "install", "microceph"], check=True)
    subprocess.run(["sudo", "microceph", "cluster", "bootstrap"], check=True)
    subprocess.run(["sudo", "microceph", "disk", "add", "loop,4G,3"], check=True)
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:4096",
            "-keyout",
            "key.pem",
            "-out",
            "cert.pem",
            "-sha256",
            "-days",
            "365",
            "-nodes",
        ],
        check=True,
    )
    subprocess.run(
        [
            "sudo",
            "microceph",
            "enable",
            "rgw",
            "--ssl-port 445",
            "--ssl-certificate '$(base64 -w0 cert.pem)'",
            "--ssl-private-key '$(base64 -w0 key.pem)'",
        ],
        check=True,
    )
    output = subprocess.run(
        [
            "sudo",
            "microceph.radosgw-admin",
            "user",
            "create",
            "--uid",
            "test",
            "--display-name",
            "test",
        ],
        capture_output=True,
        check=True,
        encoding="utf-8",
    ).stdout
    key = json.loads(output)["keys"][0]
    key_id = key["access_key"]
    secret_key = key["secret_key"]
    logger.info("Creating microceph bucket")
    for attempt in range(3):
        try:
            boto3.client(
                "s3",
                endpoint_url="https://localhost:445",
                aws_access_key_id=key_id,
                aws_secret_access_key=secret_key,
                verify="cert.pem",
            ).create_bucket(Bucket=_BUCKET)
        except botocore.exceptions.EndpointConnectionError:
            if attempt == 2:
                raise
            # microceph is not ready yet
            logger.info("Unable to connect to microceph via S3. Retrying")
            time.sleep(1)
        else:
            break
    logger.info("Set up microceph")
    return ConnectionInformation(key_id, secret_key, _BUCKET)


_BUCKET = "testbucket"
logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def storage_config(microceph: ConnectionInformation) -> dict[str, str]:
    """Provide the configuration required by s3-integrator."""
    host_ip = socket.gethostbyname(socket.gethostname())
    return {
        "endpoint": f"https://{host_ip}:445",
        "bucket": microceph.bucket,
        "path": "etcd",
        "region": "",
        "tls-ca-chain": "$(base64 -w0 cert.pem)",
    }


@pytest.fixture(scope="session")
def storage_credentials(microceph: ConnectionInformation) -> dict[str, str]:
    """Provide the access-credentials required by s3-integrator."""
    return {
        "access-key": microceph.access_key_id,
        "secret-key": microceph.secret_access_key,
    }


@pytest.fixture(scope="function")
def s3_bucket(storage_credentials, storage_config) -> None:
    """Provide a storage bucket on the deployed microceph instance."""
    session = boto3.Session(
        aws_access_key_id=storage_credentials["access-key"],
        aws_secret_access_key=storage_credentials["secret-key"],
        region_name=storage_config["region"] if storage_config["region"] else None,
    )
    s3 = session.resource("s3", endpoint_url=storage_config["endpoint"], verify="cert.pem")
    bucket = s3.Bucket(storage_config["bucket"])
    yield bucket
