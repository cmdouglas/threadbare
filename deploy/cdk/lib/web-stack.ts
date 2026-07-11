import * as path from "path";
import * as cdk from "aws-cdk-lib";
import * as acm from "aws-cdk-lib/aws-certificatemanager";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as ecsPatterns from "aws-cdk-lib/aws-ecs-patterns";
import * as ecrAssets from "aws-cdk-lib/aws-ecr-assets";
import * as logs from "aws-cdk-lib/aws-logs";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import { Construct } from "constructs";

export interface WebStackProps extends cdk.StackProps {
  readonly vpc: ec2.Vpc;
  readonly postgresSecurityGroup: ec2.SecurityGroup;
  /** Same secret DatabaseStack's Postgres container reads "password" from
   * -- this stack reads its "database_url" field instead (see
   * database-stack.ts's DatabaseStackProps docstring for why one shared
   * secret with two JSON keys, rather than CDK composing one from the
   * other). */
  readonly databaseSecretArn: string;
  /** Operator-provided secret with DISCORD_BOT_TOKEN, DISCORD_CLIENT_ID,
   * DISCORD_CLIENT_SECRET, DISCORD_OAUTH_REDIRECT_URI, DISCORD_TEST_GUILD_ID,
   * FLASK_SECRET_KEY JSON keys -- the same values the setup wizard would
   * otherwise collect and write to .env. The wizard itself doesn't apply
   * here (see deploy/cdk/README.md's documented deviations): there's no
   * shared filesystem between separate Fargate tasks for it to write to. */
  readonly appConfigSecretArn: string;
  /** ARN of an already-issued, already-validated ACM certificate for the
   * domain this will be served on. Not created/DNS-validated by this stack
   * -- that would need a real Route53 hosted zone, which this template
   * doesn't assume the operator has delegated yet. */
  readonly certificateArn: string;
}

/**
 * DESIGN.md §8.4 Option C: ALB + ACM for the web app only (the sync worker
 * gets no load balancer at all -- see sync-worker-stack.ts). Public
 * subnets/public IP, no NAT gateway, matching network-stack.ts.
 */
export class WebStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: WebStackProps) {
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
    const certificate = acm.Certificate.fromCertificateArn(
      this,
      "Certificate",
      props.certificateArn
    );

    // Same root Dockerfile as docker-compose.yml's `web` service --
    // "different command, same image" (Dockerfile's own established
    // convention), not a second image to maintain.
    const image = new ecrAssets.DockerImageAsset(this, "Image", {
      directory: path.join(__dirname, "..", "..", ".."),
      file: "Dockerfile",
    });

    const cluster = new ecs.Cluster(this, "Cluster", { vpc: props.vpc });

    const service = new ecsPatterns.ApplicationLoadBalancedFargateService(this, "Service", {
      cluster,
      cpu: 512,
      memoryLimitMiB: 1024,
      desiredCount: 1,
      publicLoadBalancer: true,
      assignPublicIp: true,
      taskSubnets: { subnetType: ec2.SubnetType.PUBLIC },
      certificate,
      redirectHTTP: true,
      circuitBreaker: { rollback: true },
      minHealthyPercent: 100,
      maxHealthyPercent: 200,
      taskImageOptions: {
        image: ecs.ContainerImage.fromDockerImageAsset(image),
        command: ["threadbare-web"],
        containerPort: 5000,
        environment: {
          HOST: "0.0.0.0",
        },
        secrets: {
          DATABASE_URL: ecs.Secret.fromSecretsManager(databaseSecret, "database_url"),
          DISCORD_BOT_TOKEN: ecs.Secret.fromSecretsManager(appConfigSecret, "DISCORD_BOT_TOKEN"),
          DISCORD_CLIENT_ID: ecs.Secret.fromSecretsManager(appConfigSecret, "DISCORD_CLIENT_ID"),
          DISCORD_CLIENT_SECRET: ecs.Secret.fromSecretsManager(
            appConfigSecret,
            "DISCORD_CLIENT_SECRET"
          ),
          DISCORD_OAUTH_REDIRECT_URI: ecs.Secret.fromSecretsManager(
            appConfigSecret,
            "DISCORD_OAUTH_REDIRECT_URI"
          ),
          DISCORD_TEST_GUILD_ID: ecs.Secret.fromSecretsManager(
            appConfigSecret,
            "DISCORD_TEST_GUILD_ID"
          ),
          FLASK_SECRET_KEY: ecs.Secret.fromSecretsManager(appConfigSecret, "FLASK_SECRET_KEY"),
        },
        logDriver: ecs.LogDrivers.awsLogs({
          streamPrefix: "web",
          logRetention: logs.RetentionDays.ONE_WEEK,
        }),
      },
    });

    // Fargate's own health-check grace period needs the app to actually
    // answer -- "/" 302s to "/login" rather than 200ing, which is fine for
    // the ALB target group's default 200-299 matcher only if we widen it,
    // since a healthy-but-redirecting app would otherwise be marked
    // unhealthy and cycled forever.
    service.targetGroup.configureHealthCheck({ path: "/", healthyHttpCodes: "200-399" });

    props.postgresSecurityGroup.addIngressRule(
      service.service.connections.securityGroups[0],
      ec2.Port.tcp(5432),
      "Web app -> Postgres"
    );
  }
}
