# DNS is the exfiltration path people forget: the VPC resolver will happily recurse
# `<base64-secret>.attacker.com` to the internet even with no IGW. Route 53 Resolver
# DNS Firewall allows only AWS service names and blocks everything else.

resource "aws_route53_resolver_firewall_domain_list" "allowed" {
  name = "${var.name}-allowed"
  domains = [
    "*.amazonaws.com.",
    "*.${var.region}.compute.internal.",
    "*.ec2.internal.",
  ]
}

resource "aws_route53_resolver_firewall_domain_list" "everything" {
  name    = "${var.name}-everything"
  domains = ["*."]
}

resource "aws_route53_resolver_firewall_rule_group" "this" {
  name = "${var.name}-dns-allowlist"
}

resource "aws_route53_resolver_firewall_rule" "allow_aws" {
  name                    = "allow-aws"
  action                  = "ALLOW"
  firewall_domain_list_id = aws_route53_resolver_firewall_domain_list.allowed.id
  firewall_rule_group_id  = aws_route53_resolver_firewall_rule_group.this.id
  priority                = 100
}

resource "aws_route53_resolver_firewall_rule" "block_rest" {
  name                    = "block-everything-else"
  action                  = "BLOCK"
  block_response          = "NXDOMAIN"
  firewall_domain_list_id = aws_route53_resolver_firewall_domain_list.everything.id
  firewall_rule_group_id  = aws_route53_resolver_firewall_rule_group.this.id
  priority                = 200
}

resource "aws_route53_resolver_firewall_rule_group_association" "this" {
  name                   = "${var.name}-dns-allowlist"
  firewall_rule_group_id = aws_route53_resolver_firewall_rule_group.this.id
  vpc_id                 = aws_vpc.this.id
  priority               = 101
}

resource "aws_route53_resolver_firewall_config" "this" {
  resource_id        = aws_vpc.this.id
  firewall_fail_open = "DISABLED" # if the firewall is unavailable, resolve nothing
}

# Log every query so blocked lookups show up as evidence too.
resource "aws_cloudwatch_log_group" "dns" {
  name              = "/ndani/${var.name}/dns-queries"
  retention_in_days = var.flow_log_retention_days
  kms_key_id        = aws_kms_key.this.arn
}

resource "aws_route53_resolver_query_log_config" "this" {
  name            = "${var.name}-dns"
  destination_arn = aws_cloudwatch_log_group.dns.arn
}

resource "aws_route53_resolver_query_log_config_association" "this" {
  resolver_query_log_config_id = aws_route53_resolver_query_log_config.this.id
  resource_id                  = aws_vpc.this.id
}
