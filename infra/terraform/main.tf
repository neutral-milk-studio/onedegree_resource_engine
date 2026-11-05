terraform {
  required_version = ">= 1.6"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.60" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
  backend "s3" {} # configured by `terraform init -backend-config=...` (see docs/deployment.md)
}

provider "aws" {
  region = var.region
  default_tags { tags = { Project = var.project, ManagedBy = "terraform" } }
}

variable "project" { default = "resource-engine" }
variable "region" { default = "us-west-2" }
variable "image_tag" { default = "bootstrap" }
variable "app_count" { default = 1 }
variable "app_cpu" { default = 512 }
variable "app_memory" { default = 1024 }
variable "db_min_acu" { default = 0.5 }
variable "db_max_acu" { default = 4 }
variable "db_engine_version" { default = "16.4" }
variable "certificate_arn" {
  description = "ACM certificate for HTTPS. Empty = HTTP only (demo)."
  default     = ""
}
variable "bedrock_model_id" {
  description = "Anthropic model or inference profile enabled in the account. No default on purpose."
  type        = string
}
variable "llm_backend" { default = "bedrock" }
variable "website_backend" { default = "sample" } # live is not implemented yet
variable "auth_mode" { default = "local" }        # cognito is provisioned but not yet exercised
variable "enable_opensearch" { default = false }
variable "re_search_backend" {
  description = "search.query.search.get_backend's RE_SEARCH_BACKEND: \"\" (dialect default), \"semantic\", \"postgres\", or \"simple\"."
  default     = "semantic"
}
variable "bedrock_embedding_model_id" {
  description = "Bedrock Titan embeddings model for semantic search. Empty = the free offline embedder (no Bedrock access needed); set to e.g. \"amazon.titan-embed-text-v2:0\" once that model is enabled in the account for real embeddings."
  default     = ""
}
variable "import_schedule" { default = "cron(0 11 * * ? *)" }      # 04:00 Pacific, daily
variable "maintenance_schedule" { default = "cron(0 12 * * ? *)" } # after the import
variable "maintenance_batch" { default = 200 }

locals {
  name = var.project
  azs  = slice(data.aws_availability_zones.this.names, 0, 2)
}
data "aws_availability_zones" "this" { state = "available" }
data "aws_caller_identity" "me" {}
