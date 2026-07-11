# Threadbare on AWS (Option C)

DESIGN.md §8.4's "cloud via infrastructure-as-code" deployment path: an ECS Fargate service
each for the web app and sync worker, an ALB + ACM certificate in front of the web app only,
and Postgres as a Fargate service with an EBS-backed volume (RDS documented as an opt-in
alternative in `lib/database-stack.ts`). Same root `Dockerfile` as `docker-compose.yml` — one
image, different `command:` per stack, no second image to maintain.

Rough honest cost, done frugally: **$15–30/month** — the price of `cdk deploy` convenience
over Option B's plain VPS. The single biggest line item to watch is RDS, if you switch to it
(see `database-stack.ts`); the default Fargate+EBS Postgres avoids that entirely.

## What's here

| Stack | Contents |
|---|---|
| `ThreadbareNetwork` | VPC, public subnets only, `natGateways: 0` (no NAT gateway tax — every task gets a public IP for outbound API/ECR access instead) |
| `ThreadbareDatabase` | Postgres on Fargate + a 20GB EBS volume, `desiredCount: 1`, no public ingress |
| `ThreadbareMigrate` | A one-shot `threadbare-migrate` task definition — no service, nothing running by default. Run it by hand (see below) |
| `ThreadbareWeb` | ALB + ACM + Fargate running `threadbare-web` |
| `ThreadbareSyncWorker` | Fargate running `threadbare-sync-worker`, `desiredCount: 1`, **no load balancer at all** — it's a singleton by design (DESIGN.md §8.4: two gateway connections on one bot session causes event weirdness), and there's no HTTP traffic for an ALB to route |

## Setup

```
cd deploy/cdk
npm install
npx cdk synth   # validate the template renders before touching AWS at all
```

### Secrets

This template does **not** auto-generate or auto-compose any secret values. You create two
secrets in Secrets Manager yourself, before the first deploy:

1. **`threadbare/database`** — a JSON secret with two keys:
   - `password` — whatever you want Postgres's own password to be
   - `database_url` — the full connection string using that same password, e.g.
     `postgresql://threadbare:<password>@postgres.threadbare.local:5432/threadbare`
     (`postgres.threadbare.local` is this template's fixed Cloud Map service-discovery name —
     see `database-stack.ts`'s `cloudMapNamespace`/`serviceName`, don't change one without the
     other)

   Why one secret with two keys, rather than CDK generating the password and composing the URL
   itself: doing that would need `SecretValue.unsafeUnwrap()`'s string-interpolation escape
   hatch to build a single `DATABASE_URL` from a separately-generated password — the CDK docs
   themselves discourage reaching for that when it's avoidable. Keeping both values in one
   secret you set yourself keeps every stack's secret-handling code the same shape
   (`fromSecretsManager(secret, jsonField)`), and you're responsible for keeping the two values
   consistent (there's no validation that they match).

2. **`threadbare/app-config`** — a JSON secret with these keys, matching exactly what the setup
   wizard would otherwise collect and write to `.env` for Option B/A:
   `DISCORD_BOT_TOKEN`, `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`,
   `DISCORD_OAUTH_REDIRECT_URI`, `DISCORD_TEST_GUILD_ID`, `FLASK_SECRET_KEY`.

You'll also need an ACM certificate already issued and validated for whatever domain you're
serving on (`aws acm request-certificate` + DNS validation, done ahead of time — this template
doesn't create or validate one itself, since that needs a real Route53-delegated domain this
template doesn't assume you have wired up yet).

### The setup wizard doesn't apply here

**This is the most important deviation from every other deployment path, and it's deliberate,
not an oversight.** The setup wizard (ROADMAP.md §7) writes Discord config to a local `.env`
file, bind-mounted read-write into both the `web` and `sync-worker` containers *on the same
host* under Docker Compose (Options A/B). There is no shared filesystem between separate
Fargate tasks for it to write to — a config change written inside the `web` task's container
would never reach the `sync-worker` task at all.

So for Option C, skip the wizard entirely: populate `threadbare/app-config` yourself (above)
with the same values the wizard would have collected, and both the `web` and `sync-worker`
tasks start already configured — `config.is_configured()` is true from the first request,
`threadbare-web` never serves the wizard, and it launches straight into gunicorn.

### Deploy

```
npx cdk deploy --all \
  -c databaseSecretArn=arn:aws:secretsmanager:<region>:<account>:secret:threadbare/database-XXXXXX \
  -c appConfigSecretArn=arn:aws:secretsmanager:<region>:<account>:secret:threadbare/app-config-XXXXXX \
  -c certificateArn=arn:aws:acm:<region>:<account>:certificate/XXXXXXXX
```

After the **first** deploy (and again after any deploy that changes the DB schema), run the
migrate task once — it's registered but never started automatically:

```
aws ecs run-task --cluster <from ThreadbareMigrate's RunTaskCommand output> ...
```

`cdk deploy`'s own output prints the exact command (`ThreadbareMigrate.RunTaskCommand`) with
the right cluster/task-definition/subnet/security-group values already filled in — copy it
verbatim rather than reconstructing it by hand.

Point your domain's DNS at the ALB's address (also in `cdk deploy`'s output) once it's up.

## What's verified, and what isn't

Per this project's own convention of flagging live-testing gaps explicitly (DESIGN.md §10)
rather than leaving them implicit:

- **`cdk synth` is verified**: `npm install && npx cdk synth` succeeds cleanly (zero errors,
  zero warnings) and produces the expected CloudFormation shape for all five stacks — spot
  checked directly (ALB + listeners + target group + one Fargate service in `ThreadbareWeb`;
  correct `command`/`secrets`/`environment` wiring on every task definition; `DesiredCount: 1`
  and no load balancer resources at all in `ThreadbareSyncWorker`).
- **`cdk deploy` against a real AWS account is not verified** — no AWS account is available in
  this environment. Everything downstream of a real deploy (ALB reachability, ACM validation
  actually working end-to-end, the EBS volume actually attaching and persisting data,
  `aws ecs run-task` actually succeeding) is unexercised. This is a real, open gap, not an
  assumption that it works.
- The migrate one-shot task isn't explicitly listed in DESIGN.md §8.4's Option C bullet list —
  it was added here because the deployment can't function without it (mirrors
  `docker-compose.yml`'s one-shot `migrate` service, which the rest of that stack assumes has
  already run).
