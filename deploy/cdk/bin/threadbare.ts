#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { NetworkStack } from "../lib/network-stack";
import { DatabaseStack } from "../lib/database-stack";
import { WebStack } from "../lib/web-stack";
import { SyncWorkerStack } from "../lib/sync-worker-stack";
import { MigrateStack } from "../lib/migrate-stack";

const app = new cdk.App();

// Every value below is operator-supplied via `-c key=value` (or cdk.json's
// own "context" block) rather than auto-generated -- see README.md's
// "Secrets" section for exactly what to create and why CDK doesn't
// generate/compose these itself.
function requireContext(key: string): string {
  const value = app.node.tryGetContext(key);
  if (!value) {
    throw new Error(
      `Missing required context value "${key}" -- pass it with -c ${key}=... ` +
        "(see deploy/cdk/README.md for what's needed and why)."
    );
  }
  return value;
}

const databaseSecretArn = requireContext("databaseSecretArn");
const appConfigSecretArn = requireContext("appConfigSecretArn");
const certificateArn = requireContext("certificateArn");

const env: cdk.Environment = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION,
};

const network = new NetworkStack(app, "ThreadbareNetwork", { env });

const database = new DatabaseStack(app, "ThreadbareDatabase", {
  env,
  vpc: network.vpc,
  databaseSecretArn,
});

new MigrateStack(app, "ThreadbareMigrate", {
  env,
  vpc: network.vpc,
  postgresSecurityGroup: database.securityGroup,
  databaseSecretArn,
});

new WebStack(app, "ThreadbareWeb", {
  env,
  vpc: network.vpc,
  postgresSecurityGroup: database.securityGroup,
  databaseSecretArn,
  appConfigSecretArn,
  certificateArn,
});

new SyncWorkerStack(app, "ThreadbareSyncWorker", {
  env,
  vpc: network.vpc,
  postgresSecurityGroup: database.securityGroup,
  databaseSecretArn,
  appConfigSecretArn,
});
