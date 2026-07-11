import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as logs from "aws-cdk-lib/aws-logs";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import { Construct } from "constructs";

export interface DatabaseStackProps extends cdk.StackProps {
  readonly vpc: ec2.Vpc;
  /**
   * ARN of an operator-created secret with "password" and "database_url"
   * JSON keys (see deploy/cdk/README.md's "Secrets" section). CDK
   * deliberately doesn't auto-generate the Postgres password itself here --
   * doing that and then composing a single DATABASE_URL connection string
   * from it would need SecretValue.unsafeUnwrap()'s string-interpolation
   * escape hatch, which the CDK docs themselves discourage when it can be
   * avoided. Keeping both values in one operator-managed secret keeps every
   * stack's secret-handling code the same shape (fromSecretsManager(secret,
   * jsonField)) with no special-casing for the one value CDK would
   * otherwise have generated itself.
   */
  readonly databaseSecretArn: string;
}

/**
 * Postgres as a Fargate service with an EBS-backed volume (DESIGN.md
 * §8.4's hobby-scale default) rather than RDS. RDS is the documented
 * alternative for anyone who wants managed backups/Multi-AZ -- see the
 * commented-out sketch at the bottom of this file; swapping requires
 * changing what WebStack/SyncWorkerStack point their DATABASE_URL secret
 * at, not touching this stack's public surface (`securityGroup`).
 */
export class DatabaseStack extends cdk.Stack {
  readonly securityGroup: ec2.SecurityGroup;
  readonly cloudMapNamespace: string = "threadbare.local";
  readonly serviceName: string = "postgres";

  constructor(scope: Construct, id: string, props: DatabaseStackProps) {
    super(scope, id, props);

    const databaseSecret = secretsmanager.Secret.fromSecretCompleteArn(
      this,
      "DatabaseSecret",
      props.databaseSecretArn
    );

    this.securityGroup = new ec2.SecurityGroup(this, "PostgresSecurityGroup", {
      vpc: props.vpc,
      description:
        "Threadbare Postgres -- no public ingress; WebStack/SyncWorkerStack " +
        "add their own security groups to this one's allowed-ingress list " +
        "on port 5432 once they're created.",
      allowAllOutbound: true,
    });

    const cluster = new ecs.Cluster(this, "Cluster", { vpc: props.vpc });
    cluster.addDefaultCloudMapNamespace({ name: this.cloudMapNamespace });

    const taskDefinition = new ecs.FargateTaskDefinition(this, "PostgresTask", {
      cpu: 512,
      memoryLimitMiB: 1024,
    });

    const dataVolume = new ecs.ServiceManagedVolume(this, "DataVolume", {
      name: "postgres-data",
      managedEBSVolume: {
        size: cdk.Size.gibibytes(20),
        volumeType: ec2.EbsDeviceVolumeType.GP3,
        fileSystemType: ecs.FileSystemType.EXT4,
      },
    });
    taskDefinition.addVolume(dataVolume);

    const container = taskDefinition.addContainer("postgres", {
      image: ecs.ContainerImage.fromRegistry("postgres:16-alpine"),
      logging: ecs.LogDrivers.awsLogs({
        streamPrefix: "postgres",
        logRetention: logs.RetentionDays.ONE_WEEK,
      }),
      environment: {
        POSTGRES_USER: "threadbare",
        POSTGRES_DB: "threadbare",
      },
      secrets: {
        POSTGRES_PASSWORD: ecs.Secret.fromSecretsManager(databaseSecret, "password"),
      },
      portMappings: [{ containerPort: 5432 }],
    });
    dataVolume.mountIn(container, {
      containerPath: "/var/lib/postgresql/data",
      readOnly: false,
    });

    new ecs.FargateService(this, "Service", {
      cluster,
      taskDefinition,
      // A singleton, like the sync worker -- two Postgres tasks writing to
      // the same EBS volume isn't a thing ECS supports anyway.
      desiredCount: 1,
      assignPublicIp: true,
      vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
      securityGroups: [this.securityGroup],
      volumeConfigurations: [dataVolume],
      cloudMapOptions: { name: this.serviceName },
      circuitBreaker: { rollback: true },
      // A single EBS-backed task: never run two Postgres tasks against the
      // same volume at once, even transiently during a deploy.
      minHealthyPercent: 0,
      maxHealthyPercent: 100,
    });
  }
}

/*
 * RDS alternative (DESIGN.md §8.4's "database cost trap" note: RDS's
 * smallest sensible instance costs more per month than the entire Option B
 * VPS -- this template defaults to the Fargate+EBS sidecar above for hobby
 * scale, with this as the documented opt-in for anyone who wants managed
 * backups and Multi-AZ instead):
 *
 *   import * as rds from "aws-cdk-lib/aws-rds";
 *
 *   const instance = new rds.DatabaseInstance(this, "Postgres", {
 *     engine: rds.DatabaseInstanceEngine.postgres({
 *       version: rds.PostgresEngineVersion.VER_16,
 *     }),
 *     vpc: props.vpc,
 *     vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
 *     instanceType: ec2.InstanceType.of(ec2.InstanceClass.T4G, ec2.InstanceSize.MICRO),
 *     credentials: rds.Credentials.fromSecret(databaseSecret),
 *     databaseName: "threadbare",
 *     securityGroups: [this.securityGroup],
 *     multiAz: false, // set true for managed failover, at ~2x the cost
 *   });
 *
 * WebStack/SyncWorkerStack's DATABASE_URL secret would then need to embed
 * `instance.dbInstanceEndpointAddress` instead of the Cloud Map DNS name
 * this stack uses (`postgres.threadbare.local`) -- everything downstream of
 * "there is a DATABASE_URL secret" is unaffected either way.
 */
