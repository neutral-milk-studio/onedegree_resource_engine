# ---- Aurora PostgreSQL Serverless v2 (HSDS store + workflow tables) ----------------------------------------
resource "aws_db_subnet_group" "this" {
  name       = local.name
  subnet_ids = aws_subnet.private[*].id
}
resource "aws_rds_cluster" "this" {
  cluster_identifier          = local.name
  engine                      = "aurora-postgresql"
  engine_mode                 = "provisioned"
  engine_version              = var.db_engine_version
  database_name               = "resource_engine"
  master_username             = "resource_engine"
  manage_master_user_password = true # password lives in Secrets Manager, rotated by RDS
  storage_encrypted           = true
  backup_retention_period     = 7
  deletion_protection         = true
  skip_final_snapshot         = false
  final_snapshot_identifier   = "${local.name}-final"
  db_subnet_group_name        = aws_db_subnet_group.this.name
  vpc_security_group_ids      = [aws_security_group.db.id]
  serverlessv2_scaling_configuration {
    min_capacity = var.db_min_acu
    max_capacity = var.db_max_acu
  }
}
resource "aws_rds_cluster_instance" "this" {
  identifier         = "${local.name}-1"
  cluster_identifier = aws_rds_cluster.this.id
  instance_class     = "db.serverless"
  engine             = aws_rds_cluster.this.engine
  engine_version     = aws_rds_cluster.this.engine_version
}

# ---- S3: raw payloads (as received) and policy documents -------------------------------------------------------
resource "aws_s3_bucket" "data" { bucket = "${local.name}-${data.aws_caller_identity.me.account_id}-${var.region}" }
resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    id     = "expire-old-raw"
    status = "Enabled"
    filter { prefix = "raw/" }
    transition {
      days          = 90
      storage_class = "STANDARD_IA"
    }
    noncurrent_version_expiration { noncurrent_days = 90 }
  }
}

# ---- Cognito: seekers, providers, admins -------------------------------------------------------------------------
resource "aws_cognito_user_pool" "this" {
  name                     = local.name
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  password_policy {
    minimum_length    = 10
    require_lowercase = true
    require_numbers   = true
    require_uppercase = true
    require_symbols   = false
  }
  mfa_configuration = "OPTIONAL"
  software_token_mfa_configuration { enabled = true }
}
resource "aws_cognito_user_pool_client" "web" {
  name                          = "web"
  user_pool_id                  = aws_cognito_user_pool.this.id
  generate_secret               = false
  explicit_auth_flows           = ["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
  prevent_user_existence_errors = "ENABLED"
  id_token_validity             = 8
  token_validity_units { id_token = "hours" }
}
resource "aws_cognito_user_group" "g" {
  for_each     = toset(["admin", "provider", "seeker"])
  name         = each.key
  user_pool_id = aws_cognito_user_pool.this.id
}

# ---- application secret --------------------------------------------------------------------------------------------
resource "random_password" "jwt" {
  length  = 48
  special = false
}
resource "aws_secretsmanager_secret" "jwt" { name = "${local.name}/jwt-secret" }
resource "aws_secretsmanager_secret_version" "jwt" {
  secret_id     = aws_secretsmanager_secret.jwt.id
  secret_string = random_password.jwt.result
}
