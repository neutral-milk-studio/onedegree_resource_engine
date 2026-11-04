# Optional (cost: roughly a small always-on instance). Default search is the in-process BM25 index, which needs no cluster.
resource "aws_opensearch_domain" "this" {
  count          = var.enable_opensearch ? 1 : 0
  domain_name    = local.name
  engine_version = "OpenSearch_2.13"
  cluster_config {
    instance_type  = "t3.small.search"
    instance_count = 1
  }
  ebs_options {
    ebs_enabled = true
    volume_size = 20
  }
  encrypt_at_rest { enabled = true }
  node_to_node_encryption { enabled = true }
  domain_endpoint_options {
    enforce_https       = true
    tls_security_policy = "Policy-Min-TLS-1-2-2019-07"
  }
  access_policies = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { AWS = aws_iam_role.task.arn }, Action = "es:ESHttp*",
  Resource = "arn:aws:es:${var.region}:${data.aws_caller_identity.me.account_id}:domain/${local.name}/*" }] })
}
