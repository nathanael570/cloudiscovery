from ipaddress import ip_network

from provider.vpc.diagram import VpcDiagram
from shared.command import CommandRunner, BaseCommand
from shared.common import (
    BaseOptions,
    ResourceDigest,
    VPCE_REGEX,
    SOURCE_IP_ADDRESS_REGEX,
)
from shared.diagram import NoDiagram, BaseDiagram


class VpcOptions(BaseOptions):
    vpc_id: str

    def __new__(cls, session, region_name, vpc_id):
        self = super(BaseOptions, cls).__new__(cls, (session, region_name))
        self.vpc_id = vpc_id
        return self

    def vpc_digest(self):
        return ResourceDigest(id=self.vpc_id, type="aws_vpc")


class Vpc(BaseCommand):
    def __init__(self, vpc_id, region_names, session, diagram):
        super().__init__(region_names, session, diagram)
        self.vpc_id = vpc_id

    @staticmethod
    def check_vpc(vpc_options: VpcOptions):
        client = vpc_options.client("ec2")
        response = client.describe_vpcs(VpcIds=[vpc_options.vpc_id])

        dataresponse = response["Vpcs"][0]
        message = "------------------------------------------------------\n"
        message = (
            message
            + "VPC: {} - {}\nCIDR Block: {}\nTenancy: {}\nIs default: {}".format(
                vpc_options.vpc_id,
                vpc_options.region_name,
                dataresponse["CidrBlock"],
                dataresponse["InstanceTenancy"],
                dataresponse["IsDefault"],
            )
        )
        print(message)

    def run(self):
        command_runner = CommandRunner()

        for region in self.region_names:

            """if vpc is none, get all vpcs and check"""
            if self.vpc_id is None:
                client = self.session.client("ec2", region_name=region)
                vpcs = client.describe_vpcs()
                for data in vpcs["Vpcs"]:
                    vpc_id = data["VpcId"]
                    vpc_options = VpcOptions(
                        session=self.session, region_name=region, vpc_id=vpc_id,
                    )
                    self.check_vpc(vpc_options)
                    diagram_builder: BaseDiagram
                    if self.diagram:
                        diagram_builder = VpcDiagram(
                            name="AWS VPC {} Resources - Region {}".format(
                                vpc_id, region
                            ),
                            filename=vpc_id,
                            vpc_id=vpc_id,
                        )
                    else:
                        diagram_builder = NoDiagram()
                    command_runner.run("vpc", vpc_options, diagram_builder)
            else:
                vpc_options = VpcOptions(
                    session=self.session, region_name=region, vpc_id=self.vpc_id,
                )

                self.check_vpc(vpc_options)
                if self.diagram:
                    diagram_builder = VpcDiagram(
                        name="AWS VPC {} Resources - Region {}".format(
                            self.vpc_id, region
                        ),
                        filename=self.vpc_id,
                        vpc_id=self.vpc_id,
                    )
                else:
                    diagram_builder = NoDiagram()
                command_runner.run("vpc", vpc_options, diagram_builder)


def check_ipvpc_inpolicy(document, vpc_options: VpcOptions):
    document = document.replace("\\", "").lower()

    """Checking if VPC is inside document, it's a 100% true information"""
    if vpc_options.vpc_id in document:
        return "direct VPC reference"
    else:
        """
        Vpc_id not found, trying to discover if it's a potencial subnet IP or VPCE is allowed
        """
        if "aws:sourcevpce" in document:

            """Get VPCE found"""
            aws_sourcevpces = []
            for vpce_tuple in VPCE_REGEX.findall(document):
                aws_sourcevpces.append(vpce_tuple[1])

            """Get all VPCE of this VPC"""
            ec2 = vpc_options.client("ec2")

            filters = [{"Name": "vpc-id", "Values": [vpc_options.vpc_id]}]

            vpc_endpoints = ec2.describe_vpc_endpoints(Filters=filters)

            """iterate VPCEs found found"""
            if len(vpc_endpoints["VpcEndpoints"]) > 0:
                matching_vpces = []
                """Iterate VPCE to match vpce in Policy Document"""
                for data in vpc_endpoints["VpcEndpoints"]:
                    if data["VpcEndpointId"] in aws_sourcevpces:
                        matching_vpces.append(data["VpcEndpointId"])
                return "VPC Endpoint(s): " + (", ".join(matching_vpces))

        if "aws:sourceip" in document:

            """Get ip found"""
            aws_sourceips = []
            for vpce_tuple in SOURCE_IP_ADDRESS_REGEX.findall(document):
                aws_sourceips.append(vpce_tuple[1])
            """Get subnets cidr block"""
            ec2 = vpc_options.client("ec2")

            filters = [{"Name": "vpc-id", "Values": [vpc_options.vpc_id]}]

            subnets = ec2.describe_subnets(Filters=filters)
            overlapping_subnets = []
            """iterate ips found"""
            for ipfound in aws_sourceips:

                """Iterate subnets to match ipaddress"""
                for subnet in list(subnets["Subnets"]):
                    ipfound = ip_network(ipfound)
                    network_addres = ip_network(subnet["CidrBlock"])

                    if ipfound.overlaps(network_addres):
                        overlapping_subnets.append(
                            "{} ({})".format(str(network_addres), subnet["SubnetId"])
                        )
            if len(overlapping_subnets) != 0:
                return "source IP(s): {} -> subnet CIDR(s): {}".format(
                    ", ".join(aws_sourceips), ", ".join(overlapping_subnets)
                )

        return False
