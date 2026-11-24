data "aws_caller_identity" "current" {}

resource "aws_kms_key" "this" {
  description             = "ndani ${var.name}: EBS, bundle bucket, logs, SSM parameters"
  enable_key_rotation     = true
  deletion_window_in_days = 7
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AccountAdmin"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "CloudWatchLogs"
        Effect    = "Allow"
        Principal = { Service = "logs.${var.region}.amazonaws.com" }
        Action    = ["kms:Encrypt*", "kms:Decrypt*", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"]
        Resource  = "*"
        Condition = {
          ArnLike = { "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/ndani/*" }
        }
      }
    ]
  })
}

resource "aws_kms_alias" "this" {
  name          = "alias/${var.name}"
  target_key_id = aws_kms_key.this.key_id
}

# Upload from your connected machine:
#   aws s3 sync ./bundle  s3://<bucket>/bundle/
#   aws s3 sync ./ndani   s3://<bucket>/repo/ --exclude ".git/*" --exclude ".ndani/*"
resource "aws_s3_bucket" "bundle" {
  bucket_prefix = "${var.name}-bundle-"
  force_destroy = false
}

resource "aws_s3_bucket_public_access_block" "bundle" {
  bucket                  = aws_s3_bucket.bundle.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "bundle" {
  bucket = aws_s3_bucket.bundle.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "bundle" {
  bucket = aws_s3_bucket.bundle.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.this.arn
    }
    bucket_key_enabled = true
  }
}

# The instance may only read this bucket, and only through the VPC endpoint.
resource "aws_s3_bucket_policy" "bundle" {
  bucket = aws_s3_bucket.bundle.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "InstanceReadsOnlyViaEndpoint"
      Effect    = "Deny"
      Principal = { AWS = aws_iam_role.instance.arn }
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.bundle.arn, "${aws_s3_bucket.bundle.arn}/*"]
      Condition = { StringNotEquals = { "aws:SourceVpce" = aws_vpc_endpoint.s3.id } }
    }]
  })
}

# Secrets for the gateway live in SSM Parameter Store, never in user data.
resource "random_password" "db" {
  length  = 32
  special = false
}

resource "random_password" "api_key" {
  length  = 43
  special = false
}

locals {
  api_key = "ndk_${random_password.api_key.result}"
}

resource "aws_ssm_parameter" "db_password" {
  name   = "/ndani/${var.name}/db_password"
  type   = "SecureString"
  key_id = aws_kms_key.this.arn
  value  = random_password.db.result
}

resource "aws_ssm_parameter" "api_key_hashes" {
  name   = "/ndani/${var.name}/api_key_hashes"
  type   = "SecureString"
  key_id = aws_kms_key.this.arn
  value  = sha256(local.api_key)
}
