#!/usr/bin/env bash
# Upgrade a running Option C (CDK) deployment: deploy every stack, then
# automatically run the one-shot migrate task -- closing the gap the rest
# of this directory's README documents as a manual `aws ecs run-task` step.
# Forward the same `-c key=value` context flags you'd pass to `cdk deploy`
# directly, e.g.:
#
#   ./upgrade.sh \
#     -c databaseSecretArn=arn:aws:secretsmanager:... \
#     -c appConfigSecretArn=arn:aws:secretsmanager:... \
#     -c certificateArn=arn:aws:acm:...
#
# See DESIGN.md §7's "Upgrade contract" for the guarantees this relies on.
# Not deployable/verifiable in the environment this was written in (no AWS
# account available) -- see README.md's "What's verified, and what isn't".
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

echo "==> Deploying all stacks..."
npx cdk deploy --all --require-approval never "$@"

echo "==> Fetching the migrate task's run command from ThreadbareMigrate's outputs..."
run_task_command="$(aws cloudformation describe-stacks \
  --stack-name ThreadbareMigrate \
  --query "Stacks[0].Outputs[?OutputKey=='RunTaskCommand'].OutputValue" \
  --output text)"

if [ -z "$run_task_command" ]; then
  echo "Could not find ThreadbareMigrate's RunTaskCommand output -- deploy may have failed." >&2
  exit 1
fi

echo "==> Running migrations:"
echo "$run_task_command"
eval "$run_task_command"

echo "==> Done. Check the admin page's Version section once the web task is healthy,"
echo "    to confirm the running version and latest applied migration match what you expect."
