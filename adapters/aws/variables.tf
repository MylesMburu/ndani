variable "region" {
  description = "AWS region. Check GPU instance availability before choosing (e.g. af-south-1 has a limited GPU selection)."
  type        = string
  default     = "eu-west-1"
}

variable "name" {
  type    = string
  default = "ndani"
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "availability_zone" {
  description = "Pick an AZ that actually has capacity for instance_type."
  type        = string
  default     = null
}

variable "instance_type" {
  description = "g6.xlarge (L4 24GB) or g5.xlarge (A10G 24GB) for gpu-small; g6e.xlarge (L40S 48GB) for gpu-large; any m7i/c7i for cpu profiles."
  type        = string
  default     = "g6.xlarge"
}

variable "profile" {
  description = "Ndani hardware profile (core/profiles/*.toml)."
  type        = string
  default     = "gpu-small"
}

variable "ami_id" {
  description = <<-EOT
    The instance has NO internet access, so the AMI must already contain Docker (with the
    compose plugin), the AWS CLI, python3 >= 3.11, nftables and, for GPU profiles, the NVIDIA
    driver + container toolkit. Leave null to use the AWS Deep Learning Base OSS NVIDIA GPU
    AMI (Ubuntu 22.04) via its public SSM parameter, or pass a golden AMI you built with Packer.
  EOT
  type        = string
  default     = null
}

variable "dlami_ssm_parameter" {
  type    = string
  default = "/aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id"
}

variable "root_volume_gb" {
  description = "One encrypted gp3 volume for OS, images and the model bundle. 7B fp16 ~15 GB, 32B AWQ ~19 GB, plus image tarballs."
  type        = number
  default     = 250
}

variable "client_cidrs" {
  description = "Who may reach the gateway on its private IP (e.g. your Client VPN CIDR). Empty = SSM port forwarding only."
  type        = list(string)
  default     = []
}

variable "gateway_port" {
  type    = number
  default = 8443
}

variable "flow_log_retention_days" {
  type    = number
  default = 90
}
