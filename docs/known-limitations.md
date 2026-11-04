# Known limitations

Read this before deploying.

## Waiting on the transform

1. **Opening hours have no UTC offset.** The transform stores local times with no time zone, so
   `/hsds` serves them without an offset. Decision 2 requires an offset on output, so this must be
   fixed before calling the output complete.
2. **ODRS fields not mapped yet:** categories and tags, ADA accessibility, service areas,
   languages, and required documents. They stay in the raw payloads. Until they're mapped, search
   and chat match ODRS records less well, and detection can't compare languages or service areas
   for ODRS records.

## Not exercised

3. **AWS.** Terraform passes `terraform validate`, but no `plan` or `apply` has run against an
   account. Treat the first deployment as a dry run.
4. **Bedrock, Cognito, S3, OpenSearch, and Textract** have not been called for real.
5. **Docker images** build only in CI so far.

## Before production

6. **Personal data.** Seeker profiles and help requests are personal data. Use only synthetic data
   until One Degree completes a privacy and legal review.
7. **Encryption in transit.** Without `certificate_arn`, the load balancer serves plain HTTP, which
   would expose login tokens. Set a certificate, or put CloudFront in front.
8. **Login tokens** are kept in browser `localStorage`, and the local accounts mode is for demos.
9. **IAM.** Bedrock and Textract permissions use `Resource = "*"` until the model ARNs are fixed.
10. **Request schemas.** The generated OpenAPI file lists every route, but most request and response
    bodies are untyped.

## Not built

11. The live website source, report-triggered checks (`RE_WORKER_MODE=reports`), the evaluation
    harness, the spreadsheet adapter, semantic search, and deleting records removed at the source.
    `ingest/adapters/` and `search/` still hold earlier stubs.
12. **Seed ids** in `seed/hsds_dataset.json` don't follow the transform's id rule. They're fixed test
    cases, so they don't collide with ingested records, but re-ingesting them through the transform
    would create new ids.
13. **Sample pages.** With the sample website source, a record with no sample page is reported as
    `check_failed`, not checked.
