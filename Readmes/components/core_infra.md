# Core Infrastructure (timers, cron, RTC, system setup)

This layer keeps XI healthy outside of flows:
- **Schedules** background jobs (Sentinel audits, rollups, sweeps, indexing)
- **Maintains** system time monotonicity via USB RTC (planned)
- **Runs** on systemd timers or cron jobs depending on your preference

---

## Systemd timers (preferred)

Configured under `/etc/systemd/system/`:contentReference[oaicite:0]{index=0}:

- **03:00** Night Sentinel Audit (`--sentinel --sentinel-ai-edgecases`)
- **03:10** Consume AI task (`--sentinel-ai-consume`)
- **03:20** Rollups + amendments sweep + indexer (`--run-all`)
- **04:00** Quick Sentinel (last 2h sanity)
- **04:30** Sandbox retention (keep 21d)  
- **Sun 04:45** DB vacuum/analyze  
- **1st @ 05:30** Monthly cold index sweep + DB backup  

Logs are appended into `/home/node-alpha/XI/logs/…`.

Enable with:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now xi-*.timer
```

---

## Cron jobs (alternative to Systemd timers)

0 3 * * *  python3 -m runtime.routines_orchestrator --sentinel --sentinel-ai-edgecases
10 3 * * * python3 -m runtime.routines_orchestrator --sentinel-ai-consume
20 3 * * * python3 -m runtime.routines_orchestrator --run-all
0 4 * * *  python3 -m runtime.routines_orchestrator --sentinel-quick --sentinel-window-hours 2
30 4 * * * python3 tools/sandbox_retention.py --root ./sandbox --days 21
45 4 * * 0 psql -d xi_memory -c "VACUUM (ANALYZE);"
30 5 1 * * python3 -m abilities.common_abilities.memory_indexer
35 5 1 * * pg_dump xi_memory > backups/xi_memory_$(date +\%Y\%m\%d).sql

---

## USB RTC (planned / placeholder)

Goal: hardware-based monotonic time at boot.
Planned implementation
:

Disable NTP (timedatectl set-ntp false)

Boot script (xi_time_boot.sh):

Read RTC → epoch

If RTC > system time, set system forward (cap +30 days)

If RTC ≤ system, do nothing (never backwards)

Log warnings + flags on failure

xi-time-boot.service ensures this runs before timers

Optional: xi-rtc-health.timer logs boot-time RTC failures

📌 Note: USB RTC integration is in progress. Until installed, system relies on current time at boot and NTP disabled.


## Why this matters

Systemd timers / cron → guarantee Sentinel, rollups, indexer, and DB hygiene run on schedule.

RTC monotonicity → prevents time rollback attacks, ensures timers don’t drift or misfire.

Logs & retention → clean, auditable history of all infra tasks.


---

## 📌 Next Steps

- Add `core_infra.md` to `README_MASTER.md` (Core Infra section & Fast Links).  
- In `CONNECTION_MAP.md`, just add a one-liner under “System Layer” → “Core Infra: timers, cron, RTC (see docs/components/core_infra.md)”.  


