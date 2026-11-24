# GCP adapter (not yet implemented)

Same contract as `adapters/aws`. A PR implementing it needs to provide:

| Contract item | AWS implementation | GCP equivalent |
|---|---|---|
| No route to the internet | VPC with no IGW/NAT | VPC with the default internet route deleted, no Cloud NAT |
| Private access to the platform | VPC endpoints (S3, SSM, KMS, logs) | Private Google Access + Private Service Connect |
| Shell without SSH/public IP | SSM Session Manager | IAP TCP forwarding |
| DNS exfiltration control | Route 53 Resolver DNS Firewall | Cloud DNS response policy (allow `*.googleapis.com`, NXDOMAIN the rest) |
| Network evidence | VPC Flow Logs | VPC Flow Logs + Firewall Rules Logging |
| Encrypted disk with your key | EBS + KMS | Persistent Disk + Cloud KMS (CMEK) |
| Instance metadata kept from containers | IMDSv2, hop limit 1 | the ndani host guard drops `169.254.169.254` for containers |

Then write `ndani.toml` with `allow_cidrs` = the subnet range and
`allow_dns_servers = ["169.254.169.254"]` (GCP's metadata server is also its DNS resolver,
so allow it for root only, like `allow_aws_metadata`).
