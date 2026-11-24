from ndani.guard import GuardPolicy, iptables_commands, nft_script


def test_default_policy_is_deny_with_logging():
    script = nft_script(GuardPolicy([], [443], []))
    assert "policy drop;" in script
    assert 'log prefix "NDANI-EGRESS host "' in script
    assert "ct state established,related accept" in script
    assert "169.254.169.254" not in script  # metadata closed unless asked
    assert "dport 53" not in script          # no DNS unless asked


def test_allowlist_rendering_handles_v4_and_v6():
    policy = GuardPolicy(["10.40.0.0/16", "fd00::/8"], [443, 8443], ["10.40.0.2"],
                         allow_aws_metadata=True, allow_aws_time_sync=True)
    script = nft_script(policy)
    assert "ip daddr 10.40.0.0/16 tcp dport { 443, 8443 } accept" in script
    assert "ip6 daddr fd00::/8 tcp dport { 443, 8443 } accept" in script
    assert "ip daddr 10.40.0.2 udp dport 53 accept" in script
    assert "169.254.169.254 tcp dport 80 meta skuid 0 accept" in script  # root only
    assert "169.254.169.123 udp dport 123 accept" in script


def test_script_is_idempotent_reload():
    first_lines = nft_script(GuardPolicy([], [443], [])).splitlines()[:2]
    assert first_lines == ["table inet ndani_guard", "delete table inet ndani_guard"]


def test_container_chain_order():
    cmds = [" ".join(c) for c in iptables_commands()]
    est = next(i for i, c in enumerate(cmds) if "ESTABLISHED,RELATED" in c)
    log = next(i for i, c in enumerate(cmds) if "-j LOG" in c)
    drop = next(i for i, c in enumerate(cmds) if c.endswith("-j DROP"))
    assert est < log < drop, "replies must be allowed before logging and dropping"
    assert "iptables -I DOCKER-USER -i ndani-edge -j NDANI-EGRESS" in cmds
    assert "iptables -I DOCKER-USER -i ndani-sealed -j NDANI-EGRESS" in cmds
