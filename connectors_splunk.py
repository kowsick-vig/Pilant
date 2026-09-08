"""A fifth source, same role as connectors_helpdesk.py: entirely static/
fictional sample data, no network call, no API key, nothing that can fail.
Explicitly requested ("add splunk give me fake data's crowded data's") as a
busy notable-events queue to try App Studio against — not a real Splunk
connector (Splunk's actual REST API needs a token + search head URL, which
nobody asked to wire up here). All 24 events, hostnames, and analyst names
below are fictional; timestamps are relative to `today` so the feed always
reads as a live, active queue instead of visibly aging demo data.
"""
from datetime import datetime, timedelta, timezone

STATUSES = ['new', 'investigating', 'escalated', 'resolved']
SEVERITIES = ['critical', 'high', 'medium', 'low', 'informational']


def get_events(now=None):
    """Fictional Splunk Enterprise Security-style notable events: a mix of
    security correlation-search hits (failed logins, C2 traffic, ransomware
    indicators) and ops/infra alerts (disk space, pod crash loops) — the
    kind of crowded, mixed-severity queue a real Splunk search head throws
    at a SOC/NOC team. Returns a copy of each row — callers can't mutate
    the fixture."""
    now = now or datetime.now(timezone.utc)
    hosts = ['web-prod-03', 'db-prod-01', 'vpn-gateway-02', 'ad-dc-01', 'fw-edge-01',
        'app-prod-07', 'mail-relay-01', 'build-ci-04', 'db-prod-02', 'web-prod-01',
        'k8s-node-11', 'proxy-edge-03', 'ad-dc-02', 'app-prod-02', 'storage-01',
        'vpn-gateway-01', 'web-prod-05', 'app-prod-04', 'dns-internal-01', 'web-prod-02',
        'fw-edge-02', 'app-prod-09', 'ci-runner-02', 'db-prod-03']
    analysts = ['Nina Alvarez', 'Tomás Reyes', 'Grace Lin', 'Devon Cole', None]
    # (title, severity, status, mitre_technique, hours_ago, event_count, index)
    examples = [
        ('Excessive Failed Logins From Single Source', 'high', 'investigating', 'T1110 Brute Force', 1.5, 342, 'security'),
        ('Successful Login From Unusual Location Following Failures', 'critical', 'escalated', 'T1078 Valid Accounts', 3, 12, 'security'),
        ('New Local Admin Account Created', 'high', 'new', 'T1136 Create Account', 4, 1, 'security'),
        ('Possible Ransomware Note File Created', 'critical', 'escalated', 'T1486 Data Encrypted for Impact', 0.5, 7, 'security'),
        ('Unusual Volume of Data Egress to External Host', 'critical', 'investigating', 'T1041 Exfiltration Over C2 Channel', 2, 1, 'security'),
        ('Disabled Windows Firewall via Netsh', 'medium', 'new', 'T1562 Impair Defenses', 6, 1, 'security'),
        ('PowerShell Encoded Command Execution', 'high', 'investigating', 'T1059 Command and Scripting Interpreter', 5, 3, 'security'),
        ('Certificate Expiring Within 7 Days', 'low', 'new', '', 18, 1, 'ops'),
        ('High CPU Utilization Sustained Over Threshold', 'medium', 'resolved', '', 30, 240, 'ops'),
        ('Disk Space Below 10% on Production Volume', 'high', 'resolved', '', 26, 18, 'ops'),
        ('Scheduled Task Created By Non-Admin User', 'medium', 'new', 'T1053 Scheduled Task/Job', 8, 2, 'security'),
        ('Brute Force Access Behavior Detected Over One Hour', 'critical', 'new', 'T1110 Brute Force', 0.75, 918, 'security'),
        ('First Time Seen Command Line Argument', 'informational', 'new', 'T1027 Obfuscated Files or Information', 9, 1, 'security'),
        ('Suspicious Process With Discovery Keywords', 'medium', 'investigating', 'T1082 System Information Discovery', 12, 4, 'security'),
        ('Web Server Error Rate Above Baseline', 'medium', 'resolved', '', 40, 1560, 'ops'),
        ('USB Storage Device Attached', 'low', 'resolved', 'T1052 Exfiltration Over Physical Medium', 55, 1, 'security'),
        ('DNS Query To Newly Registered Domain', 'high', 'investigating', 'T1071 Application Layer Protocol', 7, 6, 'security'),
        ('SSH Login From Blocklisted Country', 'critical', 'escalated', 'T1078 Valid Accounts', 1, 2, 'security'),
        ('Service Account Login Outside Business Hours', 'medium', 'new', 'T1078 Valid Accounts', 14, 1, 'security'),
        ('Multiple Users Failing To Authenticate From Host', 'high', 'new', 'T1110 Brute Force', 3.5, 87, 'security'),
        ('Kubernetes Pod CrashLoopBackOff Threshold Exceeded', 'medium', 'investigating', '', 10, 34, 'ops'),
        ('Cloud Storage Bucket Made Public', 'critical', 'new', 'T1530 Data from Cloud Storage', 2.5, 1, 'security'),
        ('Antivirus Signature Update Failure', 'low', 'resolved', '', 48, 22, 'ops'),
        ('Outbound Traffic To Known C2 Domain', 'critical', 'escalated', 'T1071 Application Layer Protocol', 0.25, 5, 'security'),
    ]
    rows = []
    for i, (title, severity, status, mitre, hours_ago, count, index) in enumerate(examples):
        rows.append({
            'id': f'NE-{30231 + i}', 'title': title, 'severity': severity, 'status': status,
            'owner': analysts[i % len(analysts)] or 'Unassigned', 'host': hosts[i % len(hosts)],
            'index': index, 'mitre_technique': mitre, 'event_count': count,
            'trigger_time': (now - timedelta(hours=hours_ago)).isoformat(),
            'description': f'Correlation search "{title}" matched {count} event{"s" if count != 1 else ""} on {hosts[i % len(hosts)]}.',
            'data_origin': 'Fictional sample data',
        })
    return rows


if __name__ == "__main__":
    # Quick manual check: python3 connectors_splunk.py
    import json
    print(json.dumps(get_events(), indent=2))
