from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest

from central_api.autoscaler import cli
from central_api.autoscaler.ec2 import (
    Ec2Fleet,
    Ec2Settings,
    Ec2Worker,
    concurrency_for_ram_gib,
)


class AwsError(Exception):
    def __init__(self, code: str):
        super().__init__("mocked AWS error")
        self.response = {"Error": {"Code": code}}


def _configured_launch_client(*run_instances_responses):
    client = Mock()
    client.describe_instances.return_value = {"Reservations": []}
    client.describe_vpcs.return_value = {"Vpcs": [{"VpcId": "vpc-default"}]}
    client.describe_security_groups.side_effect = [
        {"SecurityGroups": []},
        {"SecurityGroups": [{"GroupId": "sg-worker", "IpPermissions": []}]},
    ]
    client.create_security_group.return_value = {"GroupId": "sg-worker"}
    client.describe_subnets.return_value = {
        "Subnets": [
            {
                "SubnetId": "subnet-a",
                "VpcId": "vpc-default",
                "AvailabilityZone": "sa-east-1a",
                "State": "available",
            },
            {
                "SubnetId": "subnet-b",
                "VpcId": "vpc-default",
                "AvailabilityZone": "sa-east-1b",
                "State": "available",
            },
        ]
    }
    client.describe_images.return_value = {
        "Images": [
            {"ImageId": "ami-old", "CreationDate": "2026-01-01T00:00:00.000Z"},
            {"ImageId": "ami-new", "CreationDate": "2026-02-01T00:00:00.000Z"},
        ]
    }
    client.run_instances.side_effect = list(run_instances_responses)
    return client


def _settings(**changes) -> Ec2Settings:
    values = {
        "worker_image": "registry.example.invalid/mrbot/worker:test",
        "central_url": "http://10.0.0.5:8000",
        "allowed_ingress_cidrs": ("198.51.100.10/32",),
    }
    values.update(changes)
    return Ec2Settings(**values)


def test_settings_defaults_env_precedence_and_csv(monkeypatch):
    for name in (
        "AUTOSCALE_REGION",
        "AUTOSCALE_SUBNET_IDS",
        "AUTOSCALE_INGRESS_CIDRS",
        "AWS_DEFAULT_REGION",
    ):
        monkeypatch.delenv(name, raising=False)
    defaults = Ec2Settings.from_env()
    assert defaults.region == "sa-east-1"
    assert defaults.instance_type == "c6i.xlarge"
    assert defaults.volume_gb == 30
    assert defaults.max_instances == 4

    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")
    monkeypatch.setenv("AUTOSCALE_SUBNET_IDS", "subnet-1, subnet-2,,")
    monkeypatch.setenv("AUTOSCALE_INGRESS_CIDRS", "198.51.100.2/32, 2001:db8::2/128")
    configured = Ec2Settings.from_env()
    assert configured.region == "us-west-2"
    assert configured.subnet_ids == ("subnet-1", "subnet-2")
    assert configured.allowed_ingress_cidrs == (
        "198.51.100.2/32",
        "2001:db8::2/128",
    )


def test_settings_rejects_world_open_ingress():
    with pytest.raises(ValueError):
        Ec2Settings(allowed_ingress_cidrs=("0.0.0.0/0",))
    with pytest.raises(ValueError):
        Ec2Settings(allowed_ingress_cidrs=("::/0",))


@pytest.mark.parametrize(
    ("ram_gib", "expected"),
    # Recomendado = floor(RAM / 1,5 GB por worker), acotado al tope duro de 10.
    [(4, 2), (8, 5), (15.49, 10), (15.5, 10), (16, 10), (32, 10)],
)
def test_concurrency_is_derived_from_ram(ram_gib, expected):
    assert concurrency_for_ram_gib(ram_gib) == expected


def test_worker_concurrency_zero_is_auto_and_explicit_values_are_bounded():
    assert Ec2Settings(worker_concurrency=0).effective_worker_concurrency == 5
    assert Ec2Settings(worker_concurrency=10).effective_worker_concurrency == 10
    with pytest.raises(ValueError):
        Ec2Settings(worker_concurrency=11)


def test_instance_memory_api_and_table_fallback_are_offline():
    client = Mock()
    client.describe_instance_types.return_value = {
        "InstanceTypes": [{"MemoryInfo": {"SizeInMiB": 15872}}]
    }
    fleet = Ec2Fleet(Ec2Settings(instance_type="custom.16g"), client=client)
    assert fleet.instance_runtime() == {
        "instance_type": "custom.16g",
        "ram_gib": 15.5,
        "worker_concurrency": 10,
    }
    client.describe_instance_types.assert_called_once_with(
        InstanceTypes=["custom.16g"]
    )

    no_api = Ec2Fleet(Ec2Settings(instance_type="r5.large"), client=Mock())
    assert no_api.instance_runtime()["worker_concurrency"] == 10


def test_worker_node_requires_public_ip():
    now = datetime.now(timezone.utc)
    assert Ec2Worker("i-1", "pending", None, "10.0.0.1", now, "sa-east-1a").node is None
    worker = Ec2Worker("i-2", "running", "203.0.113.2", "10.0.0.2", now, "sa-east-1a", 8081)
    assert worker.node == "203.0.113.2:8081"


def test_user_data_has_imdsv2_and_worker_compose_restrictions():
    settings = _settings(
        registry_url="registry.example.invalid",
        registry_user="robot",
        registry_pass="fake-test-only-password",
        worker_port=8081,
        worker_concurrency=4,
        max_lifetime_minutes=90,
    )
    data = Ec2Fleet(settings, client=Mock())._user_data()
    for expected in (
        "dnf install -y docker",
        "docker login --username robot --password-stdin registry.example.invalid",
        "X-aws-ec2-metadata-token-ttl-seconds: 21600",
        "/latest/meta-data/public-ipv4",
        "shutdown -h +90",
        "--restart unless-stopped",
        "--read-only",
        "--init",
        "--shm-size=2g",
        "--cpus=2.0",
        "--memory=6g",
        "size=3g,uid=10001,gid=10001,mode=1777",
        "size=512m,uid=10001,gid=10001,mode=1777",
        "--publish 8081:8081",
        "python -u -m bot_worker",
        "--concurrency 4 --job-timeout 1800",
    ):
        assert expected in data
    assert "--password-stdin" in data


def test_user_data_scales_memory_limits_and_concurrency_with_instance_ram():
    settings = _settings(instance_type="c6i.2xlarge", worker_concurrency=0)
    data = Ec2Fleet(settings, client=Mock())._user_data()
    for expected in (
        "--memory=13g",
        "--shm-size=3g",
        "size=3g,uid=10001,gid=10001,mode=1777",
        "size=6g,uid=10001,gid=10001,mode=1777",
        "--concurrency 10 --job-timeout 1800",
    ):
        assert expected in data


def test_instance_metadata_reports_real_type_ram_slots_and_cost():
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    fleet = Ec2Fleet(Ec2Settings(instance_type="c6i.xlarge"), client=Mock())
    worker = fleet._worker_from_instance(
        {
            "InstanceId": "i-large",
            "InstanceType": "c6i.2xlarge",
            "LaunchTime": now - timedelta(hours=1),
            "State": {"Name": "running"},
            "Tags": [
                {"Key": "RamGiB", "Value": "16"},
                {"Key": "WorkerConcurrency", "Value": "10"},
            ],
        }
    )
    assert worker.instance_type == "c6i.2xlarge"
    assert worker.ram_gib == 16
    assert worker.concurrency == 10
    assert fleet.estimate_cost([worker], now) == 0.524


def test_list_managed_follows_pagination_and_project_tag_filter():
    client = Mock()
    launched_at = datetime(2026, 10, 1, tzinfo=timezone.utc)
    client.describe_instances.side_effect = [
        {
            "Reservations": [
                {"Instances": [{"InstanceId": "i-one", "State": {"Name": "running"}}]}
            ],
            "NextToken": "next-page",
        },
        {
            "Reservations": [
                {"Instances": [{"InstanceId": "i-two", "LaunchTime": launched_at,
                                 "State": {"Name": "pending"}}]}
            ]
        },
    ]
    workers = Ec2Fleet(Ec2Settings(), client=client).list_managed()
    assert [worker.instance_id for worker in workers] == ["i-one", "i-two"]
    assert client.describe_instances.call_args_list[1].kwargs["NextToken"] == "next-page"
    filters = client.describe_instances.call_args_list[0].kwargs["Filters"]
    assert {"Name": "tag:Project", "Values": ["mrbot-v3-autoscale"]} in filters


def test_wait_until_running_returns_updated_public_node():
    client = Mock()
    client.describe_instances.side_effect = [
        {
            "Reservations": [
                {"Instances": [{"InstanceId": "i-wait", "State": {"Name": "pending"}}]}
            ]
        },
        {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-wait",
                            "State": {"Name": "running"},
                            "PublicIpAddress": "203.0.113.9",
                            "PrivateIpAddress": "10.0.0.9",
                            "Placement": {"AvailabilityZone": "sa-east-1b"},
                        }
                    ]
                }
            ]
        },
    ]
    initial = Ec2Worker(
        "i-wait", "pending", None, None, datetime.now(timezone.utc), "sa-east-1b"
    )
    with patch("central_api.autoscaler.ec2.time.sleep"):
        ready = Ec2Fleet(Ec2Settings(), client=client).wait_until_running([initial], timeout=2)
    assert ready[0].state == "running"
    assert ready[0].node == "203.0.113.9:8080"


def test_ensure_security_group_revokes_open_ingress_and_keeps_allowlist():
    allow = "198.51.100.10/32"
    group = {
        "GroupId": "sg-worker",
        "Tags": [{"Key": "Project", "Value": "mrbot-v3-autoscale"}],
        "IpPermissions": [
            {
                "IpProtocol": "tcp",
                "FromPort": 0,
                "ToPort": 65535,
                "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
            },
            {
                "IpProtocol": "tcp",
                "FromPort": 8080,
                "ToPort": 8080,
                "IpRanges": [{"CidrIp": allow}],
            },
        ],
    }
    client = Mock()
    client.describe_vpcs.return_value = {"Vpcs": [{"VpcId": "vpc-default"}]}
    client.describe_security_groups.side_effect = [
        {"SecurityGroups": [group]},
        {"SecurityGroups": [group]},
    ]
    group_id = Ec2Fleet(_settings(), client=client).ensure_security_group()
    assert group_id == "sg-worker"
    client.revoke_security_group_ingress.assert_called_once()
    revoked = client.revoke_security_group_ingress.call_args.kwargs["IpPermissions"]
    assert len(revoked) == 1
    assert revoked[0]["IpRanges"][0]["CidrIp"] == "0.0.0.0/0"
    client.authorize_security_group_ingress.assert_not_called()
    client.create_tags.assert_called_once()


def test_ensure_security_group_preserves_ipv6_allowlist_idempotently():
    cidr = "2001:db8::2/128"
    group = {
        "GroupId": "sg-worker",
        "Tags": [{"Key": "Project", "Value": "mrbot-v3-autoscale"}],
        "IpPermissions": [
            {
                "IpProtocol": "tcp",
                "FromPort": 8080,
                "ToPort": 8080,
                "Ipv6Ranges": [{"CidrIpv6": cidr}],
            }
        ],
    }
    client = Mock()
    client.describe_vpcs.return_value = {"Vpcs": [{"VpcId": "vpc-default"}]}
    client.describe_security_groups.side_effect = [
        {"SecurityGroups": [group]},
        {"SecurityGroups": [group]},
    ]
    fleet = Ec2Fleet(_settings(allowed_ingress_cidrs=(cidr,)), client=client)
    assert fleet.ensure_security_group() == "sg-worker"
    client.revoke_security_group_ingress.assert_not_called()
    client.authorize_security_group_ingress.assert_not_called()


def test_launch_is_single_instance_per_request_and_sets_required_safety_options():
    instance = {
        "InstanceId": "i-launched",
        "State": {"Name": "pending"},
        "PrivateIpAddress": "10.0.0.11",
        "Placement": {"AvailabilityZone": "sa-east-1a"},
        "LaunchTime": datetime(2026, 10, 2, tzinfo=timezone.utc),
    }
    client = _configured_launch_client({"Instances": [instance]})
    fleet = Ec2Fleet(_settings(max_instances=1), client=client)
    workers = fleet.launch(3)
    assert [worker.instance_id for worker in workers] == ["i-launched"]
    client.run_instances.assert_called_once()
    request = client.run_instances.call_args.kwargs
    assert request["ImageId"] == "ami-new"
    assert request["MinCount"] == request["MaxCount"] == 1
    assert request["InstanceInitiatedShutdownBehavior"] == "terminate"
    assert request["MetadataOptions"] == {
        "HttpTokens": "required",
        "HttpPutResponseHopLimit": 2,
    }
    assert request["BlockDeviceMappings"][0]["Ebs"]["DeleteOnTermination"] is True
    interface = request["NetworkInterfaces"][0]
    assert interface["AssociatePublicIpAddress"] is True
    assert interface["SubnetId"] == "subnet-a"
    assert request["TagSpecifications"][0]["ResourceType"] == "instance"
    assert request["TagSpecifications"][1]["ResourceType"] == "volume"
    assert "registry.example.invalid/mrbot/worker:test" in request["UserData"]


def test_vcpu_limit_is_handled_gracefully_without_raising():
    client = _configured_launch_client(AwsError("VcpuLimitExceeded"))
    fleet = Ec2Fleet(_settings(), client=client)
    assert fleet.launch(1) == []
    client.run_instances.assert_called_once()


def test_insufficient_capacity_rotates_to_next_subnet():
    instance = {
        "InstanceId": "i-launched",
        "State": {"Name": "pending"},
        "Placement": {"AvailabilityZone": "sa-east-1b"},
    }
    client = _configured_launch_client(
        AwsError("InsufficientInstanceCapacity"), {"Instances": [instance]}
    )
    workers = Ec2Fleet(_settings(), client=client).launch(1)
    assert [worker.instance_id for worker in workers] == ["i-launched"]
    assert [call.kwargs["NetworkInterfaces"][0]["SubnetId"]
            for call in client.run_instances.call_args_list] == ["subnet-a", "subnet-b"]


def test_terminate_only_acts_on_instances_with_our_project_tag():
    client = Mock()
    client.describe_instances.return_value = {
        "Reservations": [
            {"Instances": [{"InstanceId": "i-managed", "State": {"Name": "running"}}]}
        ]
    }
    fleet = Ec2Fleet(Ec2Settings(), client=client)
    terminated = fleet.terminate(["i-managed", "i-unrelated"])
    assert terminated == ["i-managed"]
    filters = client.describe_instances.call_args.kwargs["Filters"]
    assert filters == [{"Name": "tag:Project", "Values": ["mrbot-v3-autoscale"]}]
    client.terminate_instances.assert_called_once_with(InstanceIds=["i-managed"])


def test_audit_filters_to_tagged_resources_and_reports_only_free_eips():
    client = Mock()
    client.describe_instances.return_value = {"Reservations": []}
    client.describe_volumes.return_value = {"Volumes": [{"VolumeId": "vol-free", "State": "available"}]}
    client.describe_addresses.return_value = {
        "Addresses": [
            {"AllocationId": "eip-free", "PublicIp": "203.0.113.4"},
            {"AllocationId": "eip-used", "PublicIp": "203.0.113.5", "InstanceId": "i-1"},
        ]
    }
    result = Ec2Fleet(Ec2Settings(), client=client).audit_orphans()
    assert result["volumes"][0]["volume_id"] == "vol-free"
    assert [row["allocation_id"] for row in result["eips"]] == ["eip-free"]
    assert {"Name": "tag:Project", "Values": ["mrbot-v3-autoscale"]} in (
        client.describe_addresses.call_args.kwargs["Filters"]
    )


def test_estimate_cost_uses_runtime_and_skips_stopped_workers():
    fleet = Ec2Fleet(Ec2Settings(instance_type="c6i.large"), client=Mock())
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    running = Ec2Worker("i-run", "running", None, None, now - timedelta(hours=2), None)
    stopped = Ec2Worker("i-stop", "stopped", None, None, now - timedelta(hours=2), None)
    assert fleet.estimate_cost([running, stopped], now) == pytest.approx(0.262)


def test_status_cli_emits_json_without_settings_or_registry_secret(capsys):
    fake_secret = "not-a-real-secret-for-this-test"
    worker = Ec2Worker(
        "i-worker", "running", "203.0.113.4", "10.0.0.4",
        datetime(2026, 10, 2, tzinfo=timezone.utc), "sa-east-1a",
    )
    fleet = Mock()
    fleet.list_managed.return_value = [worker]
    fleet.estimate_cost.return_value = 0.01
    with (
        patch.object(cli.Ec2Settings, "from_env", return_value=_settings(registry_user="robot", registry_pass=fake_secret)),
        patch.object(cli, "Ec2Fleet", return_value=fleet),
    ):
        assert cli.main(["status"]) == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["workers"][0]["node"] == "203.0.113.4:8080"
    assert payload["estimated_cost_usd"] == 0.01
    assert fake_secret not in output


def test_cleanup_orphans_deletes_only_audited_free_resources():
    client = Mock()
    client.describe_instances.return_value = {"Reservations": []}
    client.describe_volumes.return_value = {"Volumes": [
        {"VolumeId": "vol-free", "State": "available"},
        {"VolumeId": "vol-busy", "State": "in-use"},
    ]}
    client.describe_addresses.return_value = {"Addresses": [
        {"AllocationId": "eip-free", "PublicIp": "203.0.113.4"},
        {"AllocationId": "eip-used", "PublicIp": "203.0.113.5", "InstanceId": "i-1"},
    ]}
    result = Ec2Fleet(Ec2Settings(), client=client).cleanup_orphans()
    assert result == {"volumes": ["vol-free"], "eips": ["eip-free"]}
    client.delete_volume.assert_called_once_with(VolumeId="vol-free")
    client.release_address.assert_called_once_with(AllocationId="eip-free")
    # La auditoria que decide que borrar sigue filtrando por nuestro tag.
    assert {"Name": "tag:Project", "Values": ["mrbot-v3-autoscale"]} in (
        client.describe_volumes.call_args.kwargs["Filters"]
    )


def test_cleanup_orphans_tolerates_partial_failures():
    client = Mock()
    client.describe_instances.return_value = {"Reservations": []}
    client.describe_volumes.return_value = {"Volumes": [
        {"VolumeId": "vol-a", "State": "available"},
        {"VolumeId": "vol-b", "State": "available"},
    ]}
    client.describe_addresses.return_value = {"Addresses": []}
    client.delete_volume.side_effect = [RuntimeError("VolumeInUse"), None]
    result = Ec2Fleet(Ec2Settings(), client=client).cleanup_orphans()
    assert result == {"volumes": ["vol-b"]}
    assert Ec2Fleet(Ec2Settings(), client=Mock(
        describe_instances=Mock(return_value={"Reservations": []}),
        describe_volumes=Mock(return_value={"Volumes": []}),
        describe_addresses=Mock(return_value={"Addresses": []}),
    )).cleanup_orphans() == {}


def test_controller_cleanup_failure_does_not_break_termination():
    import asyncio

    from central_api.autoscaler import controller

    class Boom:
        def cleanup_orphans(self):
            raise RuntimeError("AccessDenied")

    asyncio.run(controller._cleanup_resources(Boom(), ["i-1"]))
    assert any(e["action"] == "cleanup_failed" for e in controller.event_list())


def test_volume_type_is_configurable_for_local_zones(monkeypatch):
    monkeypatch.setenv("AUTOSCALE_VOLUME_TYPE", "gp2")
    assert Ec2Settings.from_env().volume_type == "gp2"
    monkeypatch.delenv("AUTOSCALE_VOLUME_TYPE")
    assert Ec2Settings.from_env().volume_type == "gp3"
    from central_api.autoscaler.ec2 import INSTANCE_TYPE_RAM_GIB

    assert INSTANCE_TYPE_RAM_GIB["t3.xlarge"] == 16.0


def _catalog_client():
    client = Mock()
    client.describe_subnets.return_value = {"Subnets": [{"SubnetId": "s-1", "AvailabilityZone": "us-east-1-bue-1a"}]}
    client.describe_instance_type_offerings.return_value = {"InstanceTypeOfferings": [
        {"InstanceType": "c5.xlarge"}, {"InstanceType": "c5.2xlarge"}]}
    client.describe_instance_types.return_value = {"InstanceTypes": [
        {"InstanceType": "c5.2xlarge", "VCpuInfo": {"DefaultVCpus": 8}, "MemoryInfo": {"SizeInMiB": 16384}},
        {"InstanceType": "c5.xlarge", "VCpuInfo": {"DefaultVCpus": 4}, "MemoryInfo": {"SizeInMiB": 8192}}]}
    return client


def test_list_instance_types_reports_vcpu_ram_and_filters_by_zone():
    client = _catalog_client()
    fleet = Ec2Fleet(Ec2Settings(subnet_ids=("s-1",)), client=client)
    rows = fleet.list_instance_types()
    assert [r["instance_type"] for r in rows] == ["c5.xlarge", "c5.2xlarge"]
    assert rows[0] == {
        "instance_type": "c5.xlarge",
        "vcpu": 4,
        "ram_gib": 8.0,
        "suggested_concurrency": 5,
        "recommended_min": 4,
        "recommended_max": 5,
        "max_concurrency": 8,
        "gb_per_worker_min": 1.5,
        "gb_per_worker_max": 2.0,
    }
    assert rows[1]["vcpu"] == 8 and rows[1]["ram_gib"] == 16.0
    assert rows[1]["suggested_concurrency"] == 10 and rows[1]["max_concurrency"] == 10
    kwargs = client.describe_instance_type_offerings.call_args.kwargs
    assert {"Name": "location", "Values": ["us-east-1-bue-1a"]} in kwargs["Filters"]


def test_list_instance_types_falls_back_offline():
    client = Mock()
    client.describe_subnets.side_effect = RuntimeError("sin red")
    client.describe_instance_type_offerings.side_effect = RuntimeError("sin red")
    rows = Ec2Fleet(Ec2Settings(), client=client).list_instance_types()
    assert any(r["instance_type"] == "c5.xlarge" and r["vcpu"] == 4 and r["ram_gib"] == 8.0 for r in rows)


def test_user_data_uses_requested_semaphore():
    fleet = Ec2Fleet(Ec2Settings(worker_image="img:1", central_url="https://c"), client=Mock())
    assert "--concurrency 7" in fleet._user_data(instance_type="c5.xlarge", concurrency=7)
    assert "--concurrency 5" in fleet._user_data(instance_type="c5.xlarge")


def test_launch_rejects_out_of_range_semaphore():
    fleet = Ec2Fleet(Ec2Settings(worker_image="img:1", central_url="https://c"), client=Mock())
    for bad in (0, 11, -1):
        with pytest.raises(ValueError):
            fleet.launch(1, instance_type="c5.xlarge", concurrency=bad)


def test_sg_name_es_configurable_por_proyecto(monkeypatch):
    """Dos proyectos en la misma región no pueden compartir el SG: es configurable."""
    monkeypatch.setenv("AUTOSCALE_SG_NAME", "mrbot-autoscale-testing-sg")
    assert Ec2Settings.from_env().sg_name == "mrbot-autoscale-testing-sg"
    monkeypatch.delenv("AUTOSCALE_SG_NAME")
    assert Ec2Settings.from_env().sg_name == "mrbot-autoscale-worker-sg"
    with pytest.raises(ValueError):
        Ec2Settings(sg_name="   ")
    # El nombre propio se usa al buscar y al crear el grupo.
    client = Mock()
    client.describe_vpcs.return_value = {"Vpcs": [{"VpcId": "vpc-1"}]}
    # Primero la búsqueda por nombre (no existe) y luego la relectura del grupo creado.
    client.describe_security_groups.side_effect = [
        {"SecurityGroups": []},
        {"SecurityGroups": [{"GroupId": "sg-new", "IpPermissions": []}]},
    ]
    client.create_security_group.return_value = {"GroupId": "sg-new"}
    fleet = Ec2Fleet(Ec2Settings(sg_name="sg-propio"), client=client)
    assert fleet.ensure_security_group() == "sg-new"
    assert client.create_security_group.call_args.kwargs["GroupName"] == "sg-propio"
    assert {"Name": "group-name", "Values": ["sg-propio"]} in client.describe_security_groups.call_args_list[0].kwargs["Filters"]
