# A VPC with no internet gateway and no NAT gateway. There is no route to the internet
# at the network layer, no matter what software runs on the instance.

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true # required for VPC endpoint private DNS
  enable_dns_hostnames = true
}

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  az = coalesce(var.availability_zone, data.aws_availability_zones.available.names[0])
}

resource "aws_subnet" "private" {
  vpc_id                  = aws_vpc.this.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, 1)
  availability_zone       = local.az
  map_public_ip_on_launch = false
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.this.id
  # Deliberately no 0.0.0.0/0 route. Only the local VPC route and the S3 gateway endpoint.
}

resource "aws_route_table_association" "private" {
  subnet_id      = aws_subnet.private.id
  route_table_id = aws_route_table.private.id
}

# Lock the default security group so nothing accidentally uses an open one.
resource "aws_default_security_group" "default" {
  vpc_id = aws_vpc.this.id
}

# Network ACL: stateless second layer. Only VPC-internal traffic and S3 (via gateway endpoint).
resource "aws_network_acl" "private" {
  vpc_id     = aws_vpc.this.id
  subnet_ids = [aws_subnet.private.id]

  ingress {
    rule_no    = 100
    protocol   = "-1"
    action     = "allow"
    cidr_block = var.vpc_cidr
    from_port  = 0
    to_port    = 0
  }
  # Return traffic from S3 (public IPs reached through the gateway endpoint).
  ingress {
    rule_no    = 110
    protocol   = "tcp"
    action     = "allow"
    cidr_block = "0.0.0.0/0"
    from_port  = 1024
    to_port    = 65535
  }
  egress {
    rule_no    = 100
    protocol   = "-1"
    action     = "allow"
    cidr_block = var.vpc_cidr
    from_port  = 0
    to_port    = 0
  }
  egress {
    rule_no    = 110
    protocol   = "tcp"
    action     = "allow"
    cidr_block = "0.0.0.0/0" # only routable via the S3 gateway endpoint; no IGW exists
    from_port  = 443
    to_port    = 443
  }
}

# Evidence layer: every accepted and rejected flow, kept for the audit trail.
resource "aws_cloudwatch_log_group" "flow" {
  name              = "/ndani/${var.name}/vpc-flow-logs"
  retention_in_days = var.flow_log_retention_days
  kms_key_id        = aws_kms_key.this.arn
}

resource "aws_iam_role" "flow" {
  name = "${var.name}-flow-logs"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "vpc-flow-logs.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "flow" {
  role = aws_iam_role.flow.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
      Resource = "${aws_cloudwatch_log_group.flow.arn}:*"
    }]
  })
}

resource "aws_flow_log" "vpc" {
  vpc_id                   = aws_vpc.this.id
  traffic_type             = "ALL"
  log_destination_type     = "cloud-watch-logs"
  log_destination          = aws_cloudwatch_log_group.flow.arn
  iam_role_arn             = aws_iam_role.flow.arn
  max_aggregation_interval = 60
  # default format, so the Logs Insights query in outputs.tf works without parsing
}
