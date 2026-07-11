import * as path from "path";
import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as ecrAssets from "aws-cdk-lib/aws-ecr-assets";
import * as logs from "aws-cdk-lib/aws-logs";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import { Construct } from "constructs";

export interface MigrateStackProps extends cdk.StackProps {
  readonly vpc: ec2.Vpc;
  readonly postgresSecurityGroup: ec2.SecurityGroup;
  readonly databaseSecretArn: string;
}

/**
 * A one-shot `threadbare-migrate` task definition -- not explicitly listed
 * in DESIGN.md §8.4's Option C bullet list, but added here because without
 * it the deployment can't actually function (mirrors docker-compose.yml's
 * one-shot `migrate` service, which every other stack in this template
 * assumes has already run). Registered as a task definition only, no
 * ecs.FargateService -- there's nothing long-running to keep alive. Run it
 * with `aws ecs run-task` after each deploy that changes the schema; see
 * deploy/cdk/README.md for the exact command (this stack's CfnOutputs give
 * you the cluster/task-definition ARNs and subnet/security-group ids it
 * needs).
 */
export class MigrateStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: MigrateStackProps) {
    super(scope, id, props);

    const databaseSecret = secretsmanager.Secret.fromSecretCompleteArn(
      this,
      "DatabaseSecret",
      props.databaseSecretArn
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
    taskDefinition.addContainer("migrate", {
      image: ecs.ContainerImage.fromDockerImageAsset(image),
      command: ["threadbare-migrate"],
      secrets: {
        DATABASE_URL: ecs.Secret.fromSecretsManager(databaseSecret, "database_url"),
      },
      logging: ecs.LogDrivers.awsLogs({
        streamPrefix: "migrate",
        logRetention: logs.RetentionDays.ONE_WEEK,
      }),
    });

    const securityGroup = new ec2.SecurityGroup(this, "SecurityGroup", {
      vpc: props.vpc,
      description: "Threadbare migrate task -- outbound only, run via `aws ecs run-task`",
      allowAllOutbound: true,
    });
    props.postgresSecurityGroup.addIngressRule(
      securityGroup,
      ec2.Port.tcp(5432),
      "Migrate task -> Postgres"
    );

    const publicSubnetIds = props.vpc.publicSubnets.map((subnet) => subnet.subnetId);

    new cdk.CfnOutput(this, "RunTaskCommand", {
      description: "Run this once after every deploy that changes the DB schema",
      value:
        `aws ecs run-task --cluster ${cluster.clusterName} ` +
        `--task-definition ${taskDefinition.family} --launch-type FARGATE ` +
        `--network-configuration "awsvpcConfiguration={subnets=[${publicSubnetIds.join(",")}]` +
        `,securityGroups=[${securityGroup.securityGroupId}],assignPublicIp=ENABLED}"`,
    });
  }
}
