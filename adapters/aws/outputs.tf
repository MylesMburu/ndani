output "instance_id" {
  value = aws_instance.this.id
}

output "private_ip" {
  value = aws_instance.this.private_ip
}

output "bundle_bucket" {
  value = aws_s3_bucket.bundle.bucket
}

output "api_key" {
  description = "Gateway API key. `terraform output -raw api_key`"
  value       = local.api_key
  sensitive   = true
}

output "port_forward_command" {
  description = "Reach the gateway from your laptop with no public IP and no SSH."
  value       = "aws ssm start-session --region ${var.region} --target ${aws_instance.this.id} --document-name AWS-StartPortForwardingSession --parameters portNumber=${var.gateway_port},localPortNumber=${var.gateway_port}"
}

output "flow_log_query" {
  description = "CloudWatch Logs Insights query: every outbound flow from the instance that was not to the VPC."
  value       = <<-EOT
    fields @timestamp, srcAddr, dstAddr, dstPort, action
    | filter interfaceId = "${aws_instance.this.primary_network_interface_id}"
    | filter srcAddr = "${aws_instance.this.private_ip}"
    | filter not isIpv4InSubnet(dstAddr, "${var.vpc_cidr}")
    | stats count(*) by dstAddr, dstPort, action
  EOT
}
