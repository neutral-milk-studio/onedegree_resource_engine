# Deploying to AWS

Not exercised against a real AWS account while this was built: treat the first deployment as a dry run in a sandbox account. `terraform validate` runs in CI.

## 1. One-time setup
1. **State bucket** (versioned, encrypted S3 bucket) for Terraform state.
2. **GitHub OIDC role.** Create the GitHub OIDC identity provider (`token.actions.githubusercontent.com`) if absent, then a role trusting your repo:
```json
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Federated":"arn:aws:iam::<ACCOUNT>:oidc-provider/token.actions.githubusercontent.com"},
 "Action":"sts:AssumeRoleWithWebIdentity",
 "Condition":{"StringEquals":{"token.actions.githubusercontent.com:aud":"sts.amazonaws.com"},
 "StringLike":{"token.actions.githubusercontent.com:sub":"repo:<ORG>/<REPO>:environment:production"}}}]}
```
Grant it the permissions Terraform needs (ECR, ECS, EC2/VPC, ELB, RDS, S3, Cognito, IAM roles it creates, Secrets Manager, Scheduler, CloudWatch Logs). Start broad in a sandbox, then scope down from CloudTrail.
3. **Bedrock access.** In the Bedrock console, enable the Anthropic model set in `bedrock_model_id` for your region.
4. **GitHub settings.** Secret `AWS_DEPLOY_ROLE_ARN`; variables `AWS_REGION`, `TF_STATE_BUCKET`, `BEDROCK_MODEL_ID`; an Environment named `production` (add required reviewers for a manual gate).
5. Optional: an ACM certificate ARN for HTTPS (`certificate_arn`) and a Route 53 alias to the ALB.

## 2. Deploy
Run the *deploy* workflow by hand. It: runs tests → creates ECR → builds and pushes the image tagged with the commit SHA → applies Terraform with that tag → waits for the ECS service to be stable → calls `/api/health`.

Manual equivalent:
```bash
cd infra/terraform
terraform init -backend-config="bucket=<state-bucket>" -backend-config="key=resource-engine.tfstate" -backend-config="region=us-west-2"
terraform apply -target=aws_ecr_repository.app -var image_tag=bootstrap -var bedrock_model_id=<model>
docker build -f ../../api/Dockerfile -t $(terraform output -raw ecr_repository_url):v1 ../.. && docker push $(terraform output -raw ecr_repository_url):v1
terraform apply -var image_tag=v1 -var bedrock_model_id=<model>
```

## 3. First-run tasks
- The default `auth_mode` is `local`. Create the first admin with a one-off task that calls `UserService.create`. Set `auth_mode=cognito` only after testing it.
- `config/sources.json` points at the sample pages in the image.
- Run the first import: admin UI → Imports, or `aws ecs run-task ... --overrides` with `python -m api.app.jobs.run import`. Then run maintenance.

## 4. Operate
- Logs: CloudWatch `/ecs/resource-engine`. Runs: Admin → Imports and Overview.
- Schedules: EventBridge Scheduler `resource-engine-import` (04:00 Pacific) and `-maintenance`, adjustable through Terraform variables.
- Rollback: the ECS circuit breaker rolls back failed deployments; to roll back manually, apply Terraform with an earlier `image_tag`.
- Backups: Aurora automated backups (7 days) and S3 versioning.

## 5. Cost notes
No NAT gateway, one Fargate task, Aurora at 0.5 ACU minimum, scheduled jobs as short Fargate tasks, in-process search. The largest variable costs are Bedrock tokens (bounded by page-hash skipping and the `maintenance_batch` limit), the ALB and Aurora. Enable OpenSearch only when in-process search stops being enough.
