"""Provisiona workers efímeros de MrBot sobre EC2.

Las credenciales estándar de AWS las resuelve boto3 desde su cadena habitual
(env, perfiles, roles, etc.). El password del registry se incrusta en user-data
para que cloud-init pueda hacer `docker login --password-stdin`; esto es una
limitación conocida: user-data queda consultable por principals con acceso a
la instancia/API de EC2. Nunca registrar ni imprimir user-data ni settings.
"""

from __future__ import annotations

import ipaddress
import os
import shlex
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from math import floor
from typing import Any, Iterable, Sequence


INSTANCE_TYPE_RAM_GIB: dict[str, float] = {
    "c6i.large": 4.0,
    "c6i.xlarge": 8.0,
    "c6i.2xlarge": 16.0,
    "c6i.4xlarge": 32.0,
    "m6i.large": 8.0,
    "m6i.xlarge": 16.0,
    "m6i.2xlarge": 32.0,
    "r6i.large": 16.0,
    "r6i.xlarge": 32.0,
    "r5.large": 16.0,
    "r5.xlarge": 32.0,
    "r5.2xlarge": 64.0,
}


def concurrency_for_ram_gib(gib: float) -> int:
    """Return worker slots for RAM in GiB, using a 15.5 GiB tolerance."""
    return 10 if float(gib) >= 15.5 else 5


@dataclass(frozen=True)
class Ec2Settings:
    """Configuración inyectable por entorno para el autoscaler EC2."""

    region: str = "sa-east-1"
    instance_type: str = "c6i.xlarge"
    volume_gb: int = 30
    subnet_ids: tuple[str, ...] = ()
    project_tag: str = "mrbot-v3-autoscale"
    worker_image: str = ""
    registry_url: str = ""
    registry_user: str = ""
    registry_pass: str = ""
    central_url: str = ""
    worker_port: int = 8080
    worker_concurrency: int = 0
    large_instance_type: str = ""
    allowed_ingress_cidrs: tuple[str, ...] = ()
    max_instances: int = 4
    max_lifetime_minutes: int = 120

    def __post_init__(self) -> None:
        object.__setattr__(self, "subnet_ids", tuple(self.subnet_ids))
        object.__setattr__(
            self, "allowed_ingress_cidrs", tuple(self.allowed_ingress_cidrs)
        )
        if not self.region.strip():
            raise ValueError("region no puede estar vacío")
        if not self.project_tag.strip():
            raise ValueError("project_tag no puede estar vacío")
        if self.volume_gb < 1:
            raise ValueError("volume_gb debe ser positivo")
        if not 1 <= self.worker_port <= 65535:
            raise ValueError("worker_port debe estar entre 1 y 65535")
        if self.worker_concurrency != 0 and not 1 <= self.worker_concurrency <= 10:
            raise ValueError("worker_concurrency debe ser 0 (auto) o estar entre 1 y 10")
        if self.max_instances < 0:
            raise ValueError("max_instances no puede ser negativo")
        if self.max_lifetime_minutes < 1:
            raise ValueError("max_lifetime_minutes debe ser positivo")
        if bool(self.registry_user) != bool(self.registry_pass):
            raise ValueError("registry_user y registry_pass deben configurarse juntos")
        normalized_cidrs = []
        for value in self.allowed_ingress_cidrs:
            try:
                network = ipaddress.ip_network(value, strict=False)
            except ValueError as exc:
                raise ValueError(f"CIDR de ingress inválido: {value!r}") from exc
            if network.prefixlen == 0:
                raise ValueError("no se permite ingress global (0.0.0.0/0 ni ::/0)")
            normalized_cidrs.append(str(network))
        object.__setattr__(self, "allowed_ingress_cidrs", tuple(normalized_cidrs))

    @classmethod
    def from_env(cls, prefix: str = "AUTOSCALE_") -> "Ec2Settings":
        """Crea settings desde variables ``AUTOSCALE_*`` sin exponer secretos."""

        def value(name: str, default: str = "") -> str:
            return os.environ.get(f"{prefix}{name}", default)

        def csv(name: str) -> tuple[str, ...]:
            return tuple(part.strip() for part in value(name).split(",") if part.strip())

        return cls(
            region=value("REGION") or os.environ.get("AWS_DEFAULT_REGION", "sa-east-1"),
            instance_type=value("INSTANCE_TYPE", "c6i.xlarge"),
            volume_gb=int(value("VOLUME_GB", "30")),
            subnet_ids=csv("SUBNET_IDS"),
            project_tag=value("PROJECT_TAG", "mrbot-v3-autoscale"),
            worker_image=value("WORKER_IMAGE"),
            registry_url=value("REGISTRY_URL"),
            registry_user=value("REGISTRY_USER"),
            registry_pass=value("REGISTRY_PASS"),
            central_url=value("CENTRAL_URL"),
            worker_port=int(value("WORKER_PORT", "8080")),
            worker_concurrency=int(value("WORKER_CONCURRENCY", "0")),
            large_instance_type=value("INSTANCE_TYPE_LARGE"),
            allowed_ingress_cidrs=csv("INGRESS_CIDRS"),
            max_instances=int(value("MAX_INSTANCES", "4")),
            max_lifetime_minutes=int(value("MAX_LIFETIME_MINUTES", "120")),
        )

    def concurrency_for_type(self, instance_type: str, ram_gib: float | None = None) -> int:
        if self.worker_concurrency:
            return self.worker_concurrency
        ram = ram_gib if ram_gib is not None else INSTANCE_TYPE_RAM_GIB.get(instance_type, 8.0)
        return concurrency_for_ram_gib(ram)

    @property
    def effective_worker_concurrency(self) -> int:
        return self.concurrency_for_type(self.instance_type)


@dataclass
class Ec2Worker:
    instance_id: str
    state: str
    public_ip: str | None
    private_ip: str | None
    launched_at: datetime
    az: str | None
    worker_port: int = 8080
    instance_type: str = ""
    ram_gib: float = 8.0
    concurrency: int = 5

    @property
    def node(self) -> str | None:
        """Dirección inventariable que consume el controller, sin esquema."""
        return f"{self.public_ip}:{self.worker_port}" if self.public_ip else None


class Ec2Fleet:
    """Cliente EC2 de la flota autoscaler.

    ``client`` permite inyectar un cliente boto3 falso en tests. No se crea ni
    se contacta ningún recurso AWS hasta que se invoque un método de la flota.
    """

    _INSTANCE_STATES = ("pending", "running", "stopping", "stopped")
    _AUDIT_STATES = ("pending", "running", "stopping", "stopped", "shutting-down")
    _ON_DEMAND_USD_HOUR_SA_EAST_1 = {
        "c6i.large": 0.131,
        "c6i.xlarge": 0.262,
        "c6i.2xlarge": 0.524,
    }

    def __init__(self, settings: Ec2Settings, client: Any = None):
        self.settings = settings
        self._ram_cache: dict[str, float] = {}
        if client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover - depends on optional extra
                raise RuntimeError(
                    "boto3 no está instalado; instale central-api[autoscaler]"
                ) from exc
            client = boto3.client("ec2", region_name=settings.region)
        self.client = client

    def ensure_security_group(self) -> str:
        """Crea/reconcilia el SG propio, sin SSH y con ingress estrictamente allowlisted."""
        vpc_id = self._default_vpc_id()
        groups = self._call_pages(
            "describe_security_groups",
            Filters=[
                {"Name": "group-name", "Values": ["mrbot-autoscale-worker-sg"]},
                {"Name": "vpc-id", "Values": [vpc_id]},
            ],
            result_key="SecurityGroups",
        )
        if groups:
            group = groups[0]
            group_id = group["GroupId"]
            tags = {tag.get("Key"): tag.get("Value") for tag in group.get("Tags", [])}
            if tags.get("Project") not in (None, self.settings.project_tag):
                raise RuntimeError(
                    "mrbot-autoscale-worker-sg existe pero pertenece a otro Project"
                )
        else:
            response = self.client.create_security_group(
                GroupName="mrbot-autoscale-worker-sg",
                Description="Managed MrBot autoscaler worker ingress",
                VpcId=vpc_id,
            )
            group_id = response["GroupId"]

        self.client.create_tags(
            Resources=[group_id],
            Tags=[
                {"Key": "Name", "Value": "mrbot-autoscale-worker-sg"},
                {"Key": "Project", "Value": self.settings.project_tag},
                {"Key": "Role", "Value": "worker-security-group"},
                {"Key": "ManagedBy", "Value": "mrbot-central"},
            ],
        )
        self._reconcile_ingress(group_id)
        return group_id

    def launch(self, count: int, instance_type: str | None = None) -> list[Ec2Worker]:
        """Lanza hasta ``count`` workers, respetando el máximo y cuota/capacidad."""
        if count <= 0 or self.settings.max_instances == 0:
            return []
        if not self.settings.worker_image:
            raise ValueError("AUTOSCALE_WORKER_IMAGE es obligatorio para lanzar workers")
        if not self.settings.central_url:
            raise ValueError("AUTOSCALE_CENTRAL_URL es obligatorio para lanzar workers")
        if self.settings.registry_url and bool(self.settings.registry_user) != bool(
            self.settings.registry_pass
        ):
            raise ValueError("registry_user y registry_pass deben configurarse juntos")

        launch_type = instance_type or self.settings.instance_type
        ram_gib, concurrency = self._instance_runtime(launch_type)
        existing = self.list_managed(states=("pending", "running"))
        remaining = min(count, max(0, self.settings.max_instances - len(existing)))
        if remaining == 0:
            return []

        security_group_id = self.ensure_security_group()
        vpc_id = self._default_vpc_id()
        subnets = self._worker_subnets(vpc_id)
        if not subnets:
            raise RuntimeError("no hay subnets disponibles en el VPC default")
        image_id = self._latest_al2023_ami()

        launched: list[Ec2Worker] = []
        subnet_index = len(existing) % len(subnets)
        for _ in range(remaining):
            worker_launched = False
            attempted_subnets: set[str] = set()
            # InsufficientInstanceCapacity puede ser zonal: rota cada subnet/AZ
            # como máximo una vez para este worker. La cuota agotada corta el lote.
            while len(attempted_subnets) < len(subnets):
                subnet = subnets[subnet_index % len(subnets)]
                subnet_index = (subnet_index + 1) % len(subnets)
                subnet_id = subnet["SubnetId"]
                if subnet_id in attempted_subnets:
                    continue
                attempted_subnets.add(subnet_id)
                instance_name = f"mrbot-worker-{uuid.uuid4().hex[:10]}"
                try:
                    response = self.client.run_instances(
                        ImageId=image_id,
                        InstanceType=launch_type,
                        MinCount=1,
                        MaxCount=1,
                        UserData=self._user_data(instance_type=launch_type),
                        InstanceInitiatedShutdownBehavior="terminate",
                        BlockDeviceMappings=[
                            {
                                "DeviceName": "/dev/xvda",
                                "Ebs": {
                                    "VolumeSize": self.settings.volume_gb,
                                    "VolumeType": "gp3",
                                    "DeleteOnTermination": True,
                                },
                            }
                        ],
                        MetadataOptions={
                            "HttpTokens": "required",
                            "HttpPutResponseHopLimit": 2,
                        },
                        NetworkInterfaces=[
                            {
                                "DeviceIndex": 0,
                                "SubnetId": subnet_id,
                                "Groups": [security_group_id],
                                "AssociatePublicIpAddress": True,
                                "DeleteOnTermination": True,
                            }
                        ],
                        TagSpecifications=[
                            {
                                "ResourceType": "instance",
                                "Tags": self._resource_tags(
                                    instance_name,
                                    ram_gib=ram_gib,
                                    concurrency=concurrency,
                                ),
                            },
                            {
                                "ResourceType": "volume",
                                "Tags": self._resource_tags(instance_name),
                            },
                        ],
                    )
                except Exception as exc:
                    code = self._error_code(exc)
                    if code == "VcpuLimitExceeded":
                        break
                    if code == "InsufficientInstanceCapacity":
                        continue
                    raise

                for instance in response.get("Instances", []):
                    launched.append(self._worker_from_instance(instance))
                worker_launched = True
                break
            if not worker_launched:
                break
        return launched

    def wait_until_running(
        self, workers: Sequence[Ec2Worker], timeout: int = 300
    ) -> list[Ec2Worker]:
        """Espera a que los workers estén running y tengan IP pública asignada."""
        workers = list(workers)
        if not workers:
            return []
        deadline = time.monotonic() + max(0, timeout)
        latest: dict[str, Ec2Worker] = {worker.instance_id: worker for worker in workers}
        while True:
            ids = list(latest)
            response = self.client.describe_instances(InstanceIds=ids)
            for reservation in response.get("Reservations", []):
                for instance in reservation.get("Instances", []):
                    worker = self._worker_from_instance(instance)
                    latest[worker.instance_id] = worker
            result = [latest[worker.instance_id] for worker in workers]
            if all(worker.state == "running" and worker.public_ip for worker in result):
                return result
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                missing = [
                    worker.instance_id
                    for worker in result
                    if worker.state != "running" or not worker.public_ip
                ]
                raise TimeoutError(
                    "workers no llegaron a running con IP pública antes del timeout: "
                    + ", ".join(missing)
                )
            time.sleep(min(5.0, remaining))

    def list_managed(
        self, states: tuple[str, ...] = ("pending", "running")
    ) -> list[Ec2Worker]:
        filters = [{"Name": "tag:Project", "Values": [self.settings.project_tag]}]
        if states:
            filters.append({"Name": "instance-state-name", "Values": list(states)})
        reservations = self._call_pages(
            "describe_instances", Filters=filters, result_key="Reservations"
        )
        return [
            self._worker_from_instance(instance)
            for reservation in reservations
            for instance in reservation.get("Instances", [])
        ]

    def terminate(self, instance_ids: Iterable[str]) -> list[str]:
        """Termina solo IDs que AWS confirma que tienen nuestro tag Project."""
        requested = list(dict.fromkeys(instance_id for instance_id in instance_ids if instance_id))
        if not requested:
            return []
        response = self.client.describe_instances(
            InstanceIds=requested,
            Filters=[{"Name": "tag:Project", "Values": [self.settings.project_tag]}],
        )
        managed_ids = [
            instance["InstanceId"]
            for reservation in response.get("Reservations", [])
            for instance in reservation.get("Instances", [])
            if instance.get("State", {}).get("Name") not in ("terminated", "shutting-down")
        ]
        if not managed_ids:
            return []
        self.client.terminate_instances(InstanceIds=managed_ids)
        return managed_ids

    def terminate_all(self) -> list[str]:
        workers = self.list_managed(states=self._INSTANCE_STATES)
        return self.terminate(worker.instance_id for worker in workers)

    def audit_orphans(self) -> dict[str, list[dict[str, Any]]]:
        """Lista recursos disponibles/no terminados exclusivamente con nuestro tag."""
        instances = self._call_pages(
            "describe_instances",
            Filters=[
                {"Name": "tag:Project", "Values": [self.settings.project_tag]},
                {"Name": "instance-state-name", "Values": list(self._AUDIT_STATES)},
            ],
            result_key="Reservations",
        )
        instance_rows = [
            {
                "instance_id": instance["InstanceId"],
                "state": instance.get("State", {}).get("Name"),
                "az": instance.get("Placement", {}).get("AvailabilityZone"),
                "public_ip": instance.get("PublicIpAddress"),
                "private_ip": instance.get("PrivateIpAddress"),
            }
            for reservation in instances
            for instance in reservation.get("Instances", [])
        ]
        volumes = self._call_pages(
            "describe_volumes",
            Filters=[
                {"Name": "tag:Project", "Values": [self.settings.project_tag]},
                {"Name": "status", "Values": ["available"]},
            ],
            result_key="Volumes",
        )
        addresses = self._call_pages(
            "describe_addresses",
            Filters=[{"Name": "tag:Project", "Values": [self.settings.project_tag]}],
            result_key="Addresses",
        )
        return {
            "instances": instance_rows,
            "volumes": [
                {
                    "volume_id": volume["VolumeId"],
                    "state": volume.get("State"),
                    "size_gb": volume.get("Size"),
                    "availability_zone": volume.get("AvailabilityZone"),
                }
                for volume in volumes
                if volume.get("State") == "available"
            ],
            "eips": [
                {
                    "allocation_id": address.get("AllocationId"),
                    "public_ip": address.get("PublicIp"),
                    "domain": address.get("Domain"),
                }
                for address in addresses
                if not address.get("AssociationId") and not address.get("InstanceId")
            ],
        }

    def estimate_cost(
        self, workers: Iterable[Ec2Worker], now: datetime
    ) -> float:
        """Costo on-demand orientativo en USD, con fallback al precio xlarge."""
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        total = 0.0
        fallback = self._ON_DEMAND_USD_HOUR_SA_EAST_1["c6i.xlarge"]
        for worker in workers:
            if worker.state not in ("pending", "running", "stopping"):
                continue
            rate = self._ON_DEMAND_USD_HOUR_SA_EAST_1.get(
                worker.instance_type or self.settings.instance_type, fallback
            )
            launched_at = worker.launched_at
            if launched_at.tzinfo is None:
                launched_at = launched_at.replace(tzinfo=timezone.utc)
            seconds = max(60.0, (now - launched_at).total_seconds())
            total += rate * seconds / 3600.0
        return round(total, 6)

    def _default_vpc_id(self) -> str:
        vpcs = self._call_pages(
            "describe_vpcs",
            Filters=[{"Name": "is-default", "Values": ["true"]}],
            result_key="Vpcs",
        )
        if not vpcs:
            raise RuntimeError("no existe un VPC default en la región configurada")
        return vpcs[0]["VpcId"]

    def _worker_subnets(self, vpc_id: str) -> list[dict[str, str]]:
        if self.settings.subnet_ids:
            response = self.client.describe_subnets(SubnetIds=list(self.settings.subnet_ids))
            subnets = response.get("Subnets", [])
            found_ids = {subnet.get("SubnetId") for subnet in subnets}
            missing = set(self.settings.subnet_ids) - found_ids
            if missing:
                raise ValueError("subnets inexistentes: " + ", ".join(sorted(missing)))
            wrong_vpc = [
                subnet.get("SubnetId")
                for subnet in subnets
                if subnet.get("VpcId") != vpc_id
            ]
            if wrong_vpc:
                raise ValueError("todas las subnets deben pertenecer al VPC default")
        else:
            response = self.client.describe_subnets(
                Filters=[
                    {"Name": "vpc-id", "Values": [vpc_id]},
                    {"Name": "state", "Values": ["available"]},
                ]
            )
            subnets = response.get("Subnets", [])
        return sorted(
            (
                {
                    "SubnetId": subnet["SubnetId"],
                    "AvailabilityZone": subnet.get("AvailabilityZone", ""),
                }
                for subnet in subnets
                if subnet.get("State", "available") == "available"
            ),
            key=lambda subnet: (subnet["AvailabilityZone"], subnet["SubnetId"]),
        )

    def _latest_al2023_ami(self) -> str:
        images = self._call_pages(
            "describe_images",
            Owners=["amazon"],
            Filters=[
                {"Name": "name", "Values": ["al2023-ami-2023*-x86_64"]},
                {"Name": "state", "Values": ["available"]},
                {"Name": "root-device-type", "Values": ["ebs"]},
                {"Name": "virtualization-type", "Values": ["hvm"]},
            ],
            result_key="Images",
        )
        if not images:
            raise RuntimeError("no se encontró una AMI Amazon Linux 2023 x86_64 disponible")
        images.sort(key=lambda image: image.get("CreationDate", ""), reverse=True)
        return images[0]["ImageId"]

    def _resource_tags(
        self, name: str, *, ram_gib: float | None = None, concurrency: int | None = None
    ) -> list[dict[str, str]]:
        tags = [
            {"Key": "Project", "Value": self.settings.project_tag},
            {"Key": "Role", "Value": "worker"},
            {"Key": "Name", "Value": name},
            {"Key": "ManagedBy", "Value": "mrbot-central"},
        ]
        if ram_gib is not None:
            tags.append({"Key": "RamGiB", "Value": str(ram_gib)})
        if concurrency is not None:
            tags.append({"Key": "WorkerConcurrency", "Value": str(concurrency)})
        return tags

    def _user_data(self, instance_type: str | None = None) -> str:
        """Cloud-init worker runtime matching worker.compose.yml resource limits."""
        settings = self.settings
        instance_type = instance_type or settings.instance_type
        if settings.registry_user and not settings.registry_url:
            raise ValueError("registry_url es obligatorio al configurar registry credentials")
        login = ""
        if settings.registry_url:
            if not settings.registry_user or not settings.registry_pass:
                raise ValueError("registry_user y registry_pass son obligatorios con registry_url")
            # El secreto es parte del user-data (limitación documentada arriba), pero
            # viaja por stdin a docker y nunca se interpola en argumentos de proceso.
            login = (
                f"printf '%s' {shlex.quote(settings.registry_pass)} | "
                f"docker login --username {shlex.quote(settings.registry_user)} "
                f"--password-stdin {shlex.quote(settings.registry_url)}\n"
            )
        image = shlex.quote(settings.worker_image)
        central_url = shlex.quote(settings.central_url)
        port = settings.worker_port
        ram_gib, concurrency = self._instance_runtime(instance_type)
        memory_gib = max(1, floor(ram_gib * 0.85))
        shm_gib = max(1, floor(ram_gib * 0.2 + 0.5))
        work_gib = max(1, floor(ram_gib * 0.375))
        lifetime = settings.max_lifetime_minutes
        return f"""#!/bin/bash
set -euo pipefail
dnf install -y docker
systemctl enable --now docker
shutdown -h +{lifetime}
token=$(curl -fsS -X PUT http://169.254.169.254/latest/api/token \\
  -H 'X-aws-ec2-metadata-token-ttl-seconds: 21600')
public_ip=$(curl -fsS -H \"X-aws-ec2-metadata-token: $token\" \\
  http://169.254.169.254/latest/meta-data/public-ipv4)
{login}docker run -d --name mrbot-worker --init --restart unless-stopped \\
  --read-only --shm-size={shm_gib}g --cpus=2.0 --memory={memory_gib}g \\
  --tmpfs /dev/shm:rw,nosuid,nodev,size={shm_gib}g,uid=10001,gid=10001,mode=1777 \\
  --tmpfs /work:rw,nosuid,nodev,size={work_gib}g,uid=10001,gid=10001,mode=1777 \\
  --tmpfs /tmp:rw,nosuid,nodev,size=512m,uid=10001,gid=10001,mode=1777 \\
  --publish {port}:{port} {image} \\
  python -u -m bot_worker --central-url {central_url} \\
  --advertised-url \"http://$public_ip:{port}\" \\
  --concurrency {concurrency} --job-timeout 1800
"""

    def _reconcile_ingress(self, group_id: str) -> None:
        groups = self.client.describe_security_groups(GroupIds=[group_id]).get(
            "SecurityGroups", []
        )
        if not groups:
            raise RuntimeError("no se pudo volver a leer el security group creado")
        desired = set(self.settings.allowed_ingress_cidrs)
        safe_existing: set[str] = set()
        revoke: list[dict[str, Any]] = []
        for permission in groups[0].get("IpPermissions", []):
            cidrs = {item.get("CidrIp") for item in permission.get("IpRanges", [])}
            ipv6 = {item.get("CidrIpv6") for item in permission.get("Ipv6Ranges", [])}
            sources = cidrs | ipv6
            is_exact_tcp_port = (
                permission.get("IpProtocol") == "tcp"
                and permission.get("FromPort") == self.settings.worker_port
                and permission.get("ToPort") == self.settings.worker_port
                and not permission.get("UserIdGroupPairs")
                and not permission.get("PrefixListIds")
                and bool(sources)
                and sources <= desired
            )
            if is_exact_tcp_port:
                safe_existing.update(sources)
            else:
                revoke.append(permission)
        if revoke:
            self.client.revoke_security_group_ingress(
                GroupId=group_id, IpPermissions=revoke
            )
        missing = desired - safe_existing
        if missing:
            permissions = []
            for cidr in sorted(missing):
                network = ipaddress.ip_network(cidr, strict=False)
                permission: dict[str, Any] = {
                    "IpProtocol": "tcp",
                    "FromPort": self.settings.worker_port,
                    "ToPort": self.settings.worker_port,
                }
                if network.version == 4:
                    permission["IpRanges"] = [{"CidrIp": str(network)}]
                else:
                    permission["Ipv6Ranges"] = [{"CidrIpv6": str(network)}]
                permissions.append(permission)
            self.client.authorize_security_group_ingress(
                GroupId=group_id, IpPermissions=permissions
            )

    def _call_pages(
        self, method_name: str, *, result_key: str, **kwargs: Any
    ) -> list[dict[str, Any]]:
        method = getattr(self.client, method_name)
        results: list[dict[str, Any]] = []
        token = None
        while True:
            params = dict(kwargs)
            if token:
                params["NextToken"] = token
            response = method(**params)
            results.extend(response.get(result_key, []))
            token = response.get("NextToken")
            if not token:
                return results

    @staticmethod
    def _error_code(exc: Exception) -> str | None:
        response = getattr(exc, "response", None)
        if isinstance(response, dict):
            return response.get("Error", {}).get("Code")
        return None

    def _worker_from_instance(self, instance: dict[str, Any]) -> Ec2Worker:
        placement = instance.get("Placement", {})
        tags = {item.get("Key"): item.get("Value") for item in instance.get("Tags", [])}
        instance_type = str(instance.get("InstanceType") or self.settings.instance_type)
        ram_gib, concurrency = self._instance_runtime(instance_type)
        try:
            ram_gib = float(tags.get("RamGiB", ram_gib))
        except (TypeError, ValueError):
            pass
        try:
            concurrency = int(tags.get("WorkerConcurrency", concurrency))
        except (TypeError, ValueError):
            pass
        launched_at = instance.get("LaunchTime") or datetime.now(timezone.utc)
        if launched_at.tzinfo is None:
            launched_at = launched_at.replace(tzinfo=timezone.utc)
        return Ec2Worker(
            instance_id=instance["InstanceId"],
            state=instance.get("State", {}).get("Name", "unknown"),
            public_ip=instance.get("PublicIpAddress"),
            private_ip=instance.get("PrivateIpAddress"),
            launched_at=launched_at,
            az=placement.get("AvailabilityZone"),
            worker_port=self.settings.worker_port,
            instance_type=instance_type,
            ram_gib=ram_gib,
            concurrency=concurrency,
        )

    def _instance_runtime(self, instance_type: str) -> tuple[float, int]:
        """Resolve memory via EC2 when available, with an offline type table fallback."""
        ram_gib = self._ram_cache.get(instance_type)
        if ram_gib is None:
            ram_gib = INSTANCE_TYPE_RAM_GIB.get(instance_type, 8.0)
            describe = getattr(self.client, "describe_instance_types", None)
            if callable(describe):
                try:
                    response = describe(InstanceTypes=[instance_type])
                    descriptions = response.get("InstanceTypes", [])
                    size_mib = descriptions[0].get("MemoryInfo", {}).get("SizeInMiB")
                    if size_mib:
                        ram_gib = float(size_mib) / 1024.0
                except Exception:  # noqa: BLE001 - fallback keeps tests and launches offline
                    pass
            self._ram_cache[instance_type] = ram_gib
        return ram_gib, self.settings.concurrency_for_type(instance_type, ram_gib)

    def instance_runtime(self) -> dict[str, float | int | str]:
        """Effective RAM and concurrency for the configured EC2 instance type."""
        ram_gib, concurrency = self._instance_runtime(self.settings.instance_type)
        return {
            "instance_type": self.settings.instance_type,
            "ram_gib": ram_gib,
            "worker_concurrency": concurrency,
        }
