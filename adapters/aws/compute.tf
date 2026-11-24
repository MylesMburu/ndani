data "aws_ssm_parameter" "dlami" {
  count = var.ami_id == null ? 1 : 0
  name  = var.dlami_ssm_parameter
}

locals {
  ami_id       = var.ami_id != null ? var.ami_id : data.aws_ssm_parameter.dlami[0].value
  vpc_resolver = cidrhost(var.vpc_cidr, 2)
}

resource "aws_iam_role" "instance" {
  name = "${var.name}-instance"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "instance" {
  role = aws_iam_role.instance.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadBundle"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:ListBucket"]
        Resource = [aws_s3_bucket.bundle.arn, "${aws_s3_bucket.bundle.arn}/*"]
      },
      {
        Sid      = "ReadSecrets"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter", "ssm:GetParameters"]
        Resource = "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter/ndani/${var.name}/*"
      },
      {
        Sid      = "Decrypt"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource = aws_kms_key.this.arn
      }
    ]
  })
}

resource "aws_iam_instance_profile" "instance" {
  name = "${var.name}-instance"
  role = aws_iam_role.instance.name
}

resource "aws_security_group" "instance" {
  name        = "${var.name}-instance"
  description = "Ndani host: gateway in from clients only; out to VPC endpoints and S3 only"
  vpc_id      = aws_vpc.this.id

  dynamic "ingress" {
    for_each = length(var.client_cidrs) > 0 ? [1] : []
    content {
      description = "Gateway from approved clients"
      from_port   = var.gateway_port
      to_port     = var.gateway_port
      protocol    = "tcp"
      cidr_blocks = var.client_cidrs
    }
  }

  egress {
    description = "Interface endpoints"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    description     = "S3 via gateway endpoint"
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    prefix_list_ids = [data.aws_ec2_managed_prefix_list.s3.id]
  }
}

resource "aws_instance" "this" {
  ami                         = local.ami_id
  instance_type               = var.instance_type
  subnet_id                   = aws_subnet.private.id
  vpc_security_group_ids      = [aws_security_group.instance.id]
  iam_instance_profile        = aws_iam_instance_profile.instance.name
  associate_public_ip_address = false

  metadata_options {
    http_tokens                 = "required" # IMDSv2 only
    http_put_response_hop_limit = 1          # containers cannot reach IMDS through Docker NAT
    http_endpoint               = "enabled"
  }

  root_block_device {
    volume_size = var.root_volume_gb
    volume_type = "gp3"
    encrypted   = true
    kms_key_id  = aws_kms_key.this.arn
  }

  user_data_replace_on_change = true
  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    name         = var.name
    region       = var.region
    bucket       = aws_s3_bucket.bundle.bucket
    profile      = var.profile
    vpc_cidr     = var.vpc_cidr
    vpc_resolver = local.vpc_resolver
    bind_addr    = length(var.client_cidrs) > 0 ? "0.0.0.0" : "127.0.0.1"
    gateway_port = var.gateway_port
  })

  depends_on = [aws_vpc_endpoint.interface, aws_vpc_endpoint.s3,
                aws_route53_resolver_firewall_rule_group_association.this]
}
