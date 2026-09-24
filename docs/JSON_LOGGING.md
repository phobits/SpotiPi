# 📊 Structured JSON Logging in SpotiPi

## Overview

Current release context: **v1.10.1** on **Python 3.10+**.

Since **v1.3.8**, SpotiPi supports structured JSON logging for production observability. This enables:
- **Better debugging**: Correlate alarm failures across multiple structured fields
- **Log aggregation**: Parse logs easily with journalctl, Loki, ELK, or similar tools
- **Automated monitoring**: Set up alerts based on specific error codes or contexts
- **Performance analysis**: Track request durations, cache hit rates, and API latencies

---

## Configuration

### Enable JSON Logging

JSON logging is **automatically enabled** on Raspberry Pi in production mode. To control it manually:

```bash
# Enable JSON logs (explicit)
SPOTIPI_JSON_LOGS=1

# Disable JSON logs (force traditional format)
SPOTIPI_JSON_LOGS=0
```

Add this to your `.env` file or set it in your systemd service:

```ini
# /etc/systemd/system/spotipi.service
[Service]
Environment="SPOTIPI_JSON_LOGS=1"
```

---

## Log Format

### JSON Log Structure

```json
{
  "timestamp": "2025-11-04T06:30:00.123Z",
  "level": "ERROR",
  "logger": "alarm_scheduler",
  "message": "Alarm execution failed",
  "source": "alarm.py:184",
  "function": "execute_alarm",
  "alarm_id": "20251104T063000Z",
  "error_code": "device_not_found",
  "device_name": "Living Room",
  "scheduled_utc": "2025-11-04T06:30:00Z"
}
```

### Traditional Log Format (Development)

```
2025-11-04 06:30:00,123 | alarm_scheduler | ERROR | alarm.py:184 | Alarm execution failed
```

---

## Querying JSON Logs

### Using `journalctl` (systemd)

**View recent alarm-related logs:**
```bash
journalctl -u spotipi.service --since "2025-11-04 06:00" | grep "alarm_probe"
```

On the Pi only the `alarm_probe` logger reaches journald. Besides the scheduler states it carries
(since v1.12.11) the playback start (`execute_play_verify_read`, `execute_play_volume_enforced`),
every fade-in read (`execute_fade_read`: reported volume, verdict, next step) and the snooze
monitor as `"kind": "snooze_probe"` events (`snooze_state`: `session_started`, `armed_poll`,
`snoozing_poll`, `snoozed`, `manual_resume_rearm`, `resumed`, `session_stopped`).

**Extract JSON logs with `jq`:**
```bash
journalctl -u spotipi.service -o json | jq 'select(.MESSAGE | contains("alarm_id"))'
```

**Filter by log level:**
```bash
journalctl -u spotipi.service --since today -o json | jq 'select(.LEVEL == "ERROR")'
```

**Count errors by error_code:**
```bash
journalctl -u spotipi.service --since today -o json \
  | jq -r 'select(.LEVEL == "ERROR") | .MESSAGE' \
  | jq -r '.error_code // "unknown"' \
  | sort | uniq -c
```

**Track alarm execution timeline:**
```bash
journalctl -u spotipi.service --since "2025-11-04 06:25" -o json \
  | jq -r 'select(.MESSAGE | contains("scheduler_state")) | 
           [.timestamp, (.MESSAGE | fromjson | .scheduler_state)] | @tsv'
```

### Using Python for Advanced Queries

```python
import json
import subprocess

# Get logs from journalctl
proc = subprocess.run(
    ["journalctl", "-u", "spotipi.service", "--since", "today", "-o", "json"],
    capture_output=True,
    text=True
)

alarm_failures = []
for line in proc.stdout.strip().split('\n'):
    try:
        log_entry = json.loads(line)
        message = log_entry.get("MESSAGE", "")
        if "alarm_id" in message:
            alarm_data = json.loads(message)
            if alarm_data.get("error_code"):
                alarm_failures.append(alarm_data)
    except json.JSONDecodeError:
        continue

print(f"Found {len(alarm_failures)} alarm failures today:")
for failure in alarm_failures:
    print(f"  - {failure['alarm_id']}: {failure['error_code']}")
```

---

## Structured Logging in Code

### Using `log_structured()` Helper

```python
from src.utils.logger import setup_logger, log_structured
import logging

logger = setup_logger(__name__)

# Log with structured context
log_structured(
    logger,
    logging.ERROR,
    "Alarm execution failed",
    alarm_id="20251104T063000Z",
    error_code="device_not_found",
    device_name="Living Room",
    retry_count=3
)
```

**Output (JSON mode):**
```json
{
  "timestamp": "2025-11-04T06:30:00.123Z",
  "level": "ERROR",
  "logger": "alarm",
  "message": "Alarm execution failed",
  "alarm_id": "20251104T063000Z",
  "error_code": "device_not_found",
  "device_name": "Living Room",
  "retry_count": 3
}
```

**Output (Traditional mode):**
```
2025-11-04 06:30:00,123 | alarm | ERROR | Alarm execution failed | alarm_id=20251104T063000Z error_code=device_not_found device_name=Living Room retry_count=3
```

### Using `extra=` in Standard Logging

```python
logger.error(
    "Failed to start playback",
    extra={
        "spotify_track_uri": "spotify:track:abc123",
        "device_id": "xyz789",
        "http_status": 502
    }
)
```

---

## Key Structured Fields

### Alarm Events
- `alarm_id`: Unique ID (format: `YYYYMMDDTHHMMSSsZ`)
- `scheduled_utc`: ISO 8601 timestamp of scheduled trigger
- `scheduler_state`: Current state (`execute_enter`, `execute_config_error`, `alarm_triggered`, etc.)
- `error_code`: Machine-readable error identifier
- `device_name`: Configured Spotify device
- `ntp_offset_ms`: System clock offset from NTP (for time-drift debugging)
- `network_ready`: Network connectivity status

### HTTP/API Events
- `endpoint`: API route (e.g., `/api/alarm/execute`)
- `http_status`: Response status code
- `duration_ms`: Request processing time
- `user_agent`: Client identifier

### Spotify Integration
- `spotify_track_uri`: Track/playlist URI
- `device_id`: Spotify device ID
- `api_call`: Spotify API endpoint called
- `retry_count`: Number of retries attempted

---

## Monitoring & Alerting Examples

### Prometheus Exporter Pattern

```python
from prometheus_client import Counter, Histogram

alarm_failures = Counter(
    'spotipi_alarm_failures_total',
    'Total number of alarm failures',
    ['error_code']
)

# In alarm execution code:
if not success:
    alarm_failures.labels(error_code=result.error_code).inc()
```

### Simple Email Alert on Alarm Failure

```bash
#!/bin/bash
# /usr/local/bin/spotipi-alarm-monitor.sh

LOG_OUTPUT=$(journalctl -u spotipi.service --since "5 minutes ago" -o json \
  | jq -r 'select(.MESSAGE | contains("error_code")) | .MESSAGE' \
  | jq -r 'select(.scheduler_state == "execute_failed")')

if [ -n "$LOG_OUTPUT" ]; then
  echo "$LOG_OUTPUT" | mail -s "SpotiPi Alarm Failure" your@email.com
fi
```

Add to crontab:
```cron
*/5 * * * * /usr/local/bin/spotipi-alarm-monitor.sh
```

---

## Migration from Traditional Logging

No code changes required! Existing log statements automatically gain structured output when `SPOTIPI_JSON_LOGS=1`:

**Before (Traditional):**
```python
logger.error("Failed to load config: %s", error_message)
```

**After (JSON, automatic):**
```json
{"timestamp": "...", "level": "ERROR", "message": "Failed to load config: Connection timeout", ...}
```

**Enhanced (Structured Context):**
```python
from src.utils.logger import log_structured
log_structured(logger, logging.ERROR, "Failed to load config",
              error=error_message, config_path=config_file, retry_count=3)
```

---

## Troubleshooting

### JSON Logs Not Appearing

1. **Check environment variable:**
   ```bash
   ssh pi@spotipi.local 'systemctl show spotipi.service -p Environment'
   ```

2. **Verify systemd journal:**
   ```bash
   journalctl -u spotipi.service --since "1 minute ago" -o json-pretty
   ```

3. **Force JSON logging:**
   ```bash
   ssh pi@spotipi.local 'sudo systemctl edit spotipi.service'
   # Add:
   [Service]
   Environment="SPOTIPI_JSON_LOGS=1"
   
   sudo systemctl daemon-reload
   sudo systemctl restart spotipi.service
   ```

### Logs Contain Non-JSON Mixed Content

Some third-party libraries (e.g., `urllib3`) log to root logger. Filter in queries:
```bash
journalctl -u spotipi.service -o json | jq 'select(.MESSAGE | startswith("{"))'
```

---

## Best Practices

1. **Use structured fields** for data you want to query/aggregate (e.g., `alarm_id`, `error_code`)
2. **Keep messages human-readable** – they appear in `message` field
3. **Avoid sensitive data** in logs (tokens, passwords) – use sanitized IDs instead
4. **Set appropriate log levels**:
   - `DEBUG`: Verbose development info
   - `INFO`: Normal operation milestones
   - `WARNING`: Recoverable issues (retries, fallbacks)
   - `ERROR`: Failures requiring attention
   - `CRITICAL`: System-level failures

---

## Related Documentation

- [ENVIRONMENT_VARIABLES.md](./ENVIRONMENT_VARIABLES.md) – Full list of logging flags
- [THREAD_SAFETY.md](./THREAD_SAFETY.md) – Thread-safe logging considerations
- [alarm_logging.py](../src/core/alarm_logging.py) – Alarm-specific structured logging helpers

---

**Version:** v1.10.0  
**Last Updated:** May 31, 2026
