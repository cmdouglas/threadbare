import * as path from "path";
import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as ecrAssets from "aws-cdk-lib/aws-ecr-assets";
import * as logs from "aws-cdk-lib/aws-logs";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import { Construct } from "constructs";

export interface SyncWorkerStackProps extends cdk.StackProps {
  readonly vpc: ec2.Vpc;
  readonly postgresSecurityGroup: ec2.SecurityGroup;
  readonly databaseSecretArn: string;
  readonly appConfigSecretArn: string;
}

/**
 * DESIGN.md §8.4: the sync worker gets `desiredCount: 1` and no load
 * balancer at all -- it's a singleton by design (two gateway connections on
 * the same bot session would cause event weirdness), and there's nothing
 * for an ALB to route to since it serves no HTTP traffic. Security group
 * has zero inbound rules -- the gateway connection and REST calls are both
 * outbound-only, matching this project's minimal-permissions posture for
 * every other deployment path (DESIGN.md §8.4's Option A/B docs make the
 * same "sync worker needs no inbound ports at all" point).
 */
export class SyncWorkerStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: SyncWorkerStackProps) {
    super(scope, id, props);

    const databaseSecret = secretsmanager.Secret.fromSecretCompleteArn(
      this,
      "DatabaseSecret",
      props.databaseSecretArn
    );
    const appConfigSecret = secretsmanager.Secret.fromSecretCompleteArn(
      this,
      "AppConfigSecret",
      props.appConfigSecretArn
    );

    const image = new ecrAssets.DockerImageAsset(this, "Image", {
      directory: path.join(__dirname, "..", "..", ".."),
      file: "Dockerfile",
    });

    const cluster = new ecs.Cluster(this, "Cluster", { vpc: props.vpc });

    const taskDefinition = new ecs.FargateTaskDefinition(this, "TaskDefinition", {
      cpu: 256,
      memoryLimitMiB: 512,
    });
    taskDefinition.addContainer("sync-worker", {
      image: ecs.ContainerImage.fromDockerImageAsset(image),
      command: ["threadbare-sync-worker"],
      secrets: {
        DATABASE_URL: ecs.Secret.fromSecretsManager(databaseSecret, "database_url"),
        DISCORD_BOT_TOKEN: ecs.Secret.fromSecretsManager(appConfigSecret, "DISCORD_BOT_TOKEN"),
      },
      logging: ecs.LogDrivers.awsLogs({
        streamPrefix: "sync-worker",
        logRetention: logs.RetentionDays.ONE_WEEK,
      }),
    });

    const securityGroup = new ec2.SecurityGroup(this, "SecurityGroup", {
      vpc: props.vpc,
      description: "Threadbare sync worker -- outbound only, no inbound rules at all",
      allowAllOutbound: true,
    });

    new ecs.FargateService(this, "Service", {
      cluster,
      taskDefinition,
      desiredCount: 1,
      assignPublicIp: true,
      vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
      securityGroups: [securityGroup],
      circuitBreaker: { rollback: true },
      // A singleton gateway connection: bring the new task up before
      // tearing down the old one during deploys, never run zero or two at
      // once.
      minHealthyPercent: 100,
      maxHealthyPercent: 200,
    });

    props.postgresSecurityGroup.addIngressRule(
      securityGroup,
      ec2.Port.tcp(5432),
      "Sync worker -> Postgres"
    );
  }
}
