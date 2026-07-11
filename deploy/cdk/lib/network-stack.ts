import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { Construct } from "constructs";

/**
 * DESIGN.md §8.4 Option C's NAT-avoidance note: tasks run in public subnets
 * with public IPs for outbound API access (Discord, ECR pulls) instead of
 * paying the ~$32/month NAT gateway tax, security-grouped down to
 * inbound-nothing (sync worker) / ALB-only (web) by the stacks that use
 * this VPC. natGateways: 0 means there is no private-subnet path at all --
 * deliberate, not an oversight.
 */
export class NetworkStack extends cdk.Stack {
  readonly vpc: ec2.Vpc;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    this.vpc = new ec2.Vpc(this, "Vpc", {
      maxAzs: 2,
      natGateways: 0,
      subnetConfiguration: [
        {
          name: "public",
          subnetType: ec2.SubnetType.PUBLIC,
          cidrMask: 24,
        },
      ],
    });
  }
}
