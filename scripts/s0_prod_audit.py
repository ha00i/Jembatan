#!/usr/bin/env python3
"""Jembatan S0: non-mutating, stdlib-only host survey; no QA execution.

Only /proc, statvfs, and constant read-only Docker commands are used. Output
may contain production container names: keep reports private and off GitHub.
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

GIB = 1024 ** 3
MIN_SURVEY_SECONDS = 24 * 3600


def read_text(path, default=""):
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return default


def kv(path):
    output = {}
    for row in read_text(path).splitlines():
        parts = row.replace(":", " ").split()
        if len(parts) >= 2 and parts[1].isdigit():
            output[parts[0]] = int(parts[1])
    return output


def cpu_ticks(content):
    first = next((s for s in content.splitlines() if s.startswith("cpu ")), "")
    try:
        numbers = [int(s) for s in first.split()[1:]]
        if len(numbers) < 5:
            return None
        return [sum(numbers), numbers[3] + numbers[4]]
    except ValueError:
        return None


def cpu_busy(previous, current):
    if not previous or not current:
        return None
    total = current[0] - previous[0]
    idle = current[1] - previous[1]
    if total <= 0 or idle < 0 or idle > total:
        return None
    return round(100.0 * (total - idle) / total, 2)


def disk_info(directory):
    try:
        stats = os.statvfs(directory)
        total = stats.f_blocks * stats.f_frsize
        available = stats.f_bavail * stats.f_frsize
        return {"total_bytes": total, "available_bytes": available,
                "free_percent": round(100.0 * available / total, 2) if total else None}
    except OSError:
        return None


def local_docker(*args):
    if not shutil.which("docker"):
        return None
    # Explicit host: don't follow DOCKER_HOST / DOCKER_CONTEXT to another server.
    command = ["docker", "--host", "unix:///var/run/docker.sock", *args]
    env = {k: v for k, v in os.environ.items()
           if k not in ("DOCKER_HOST", "DOCKER_CONTEXT")}
    try:
        p = subprocess.run(command, capture_output=True, text=True,
                           timeout=8, env=env, check=False)
        if p.returncode:
            return None
        return p.stdout
    except (OSError, subprocess.TimeoutExpired):
        return None


def docker_snapshot():
    info = local_docker("info", "--format", "{{.CgroupVersion}}|{{.CgroupDriver}}|{{.DockerRootDir}}")
    listing = local_docker("ps", "-a", "--format", "{{json .}}")
    if info is None or listing is None:
        return {"readable": False, "containers": [], "unavailable_reason": "docker_read_only_access_unavailable"}
    bits = info.strip().split("|", 2)
    containers = []
    for line in listing.splitlines():
        try:
            obj = json.loads(line)
            state = str(obj.get("State") or "unknown").lower()
            status = str(obj.get("Status") or "")
            health = "unhealthy" if "(unhealthy)" in status else "healthy" if "(healthy)" in status else "unknown"
            containers.append({"name": str(obj.get("Names") or "unknown"),
                               "state": state, "health": health})
        except (TypeError, ValueError):
            continue
    return {"readable": True, "cgroup_version": bits[0] if len(bits) > 0 else None,
            "cgroup_driver": bits[1] if len(bits) > 1 else None,
            "docker_storage": disk_info(bits[2]) if len(bits) == 3 else None,
            "containers": containers}


def snapshot(previous_cpu=None, previous_oom=None):
    mem = kv("/proc/meminfo")
    vm = kv("/proc/vmstat")
    ticks = cpu_ticks(read_text("/proc/stat"))
    oom = vm.get("oom_kill")
    return {
        "sampled_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "epoch": time.time(),
        "cpu_ticks": ticks,
        "cpu_busy_percent": cpu_busy(previous_cpu, ticks),
        "mem_total_bytes": mem.get("MemTotal", 0) * 1024,
        "mem_available_bytes": mem.get("MemAvailable", 0) * 1024,
        "oom_kill_count": oom,
        "oom_kill_delta": max(0, oom - previous_oom) if oom is not None and previous_oom is not None else None,
        "root_disk": disk_info("/"),
        "kernel": os.uname().release,
        "systemd_pid1": read_text("/proc/1/comm").strip() == "systemd",
        "cgroup_controllers": read_text("/sys/fs/cgroup/cgroup.controllers").strip().split(),
        "docker": docker_snapshot(),
    }


def _secure_append(path, line):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(fd, (json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def _read_samples(path):
    items = []
    with path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                if not isinstance(item, dict) or not isinstance(item.get("epoch"), (int, float)):
                    raise ValueError("invalid sample")
                items.append(item)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Sample JSONL baris {lineno} tidak valid") from exc
    return items


def analyze(samples, critical, min_seconds=MIN_SURVEY_SECONDS, max_gap=180):
    """Conservative advisory result. NO_GO is never permission to run QA."""
    reasons = []
    if len(samples) < 2:
        reasons.append("Sampel kurang dari dua")
        duration = 0
    else:
        duration = max(0, samples[-1]["epoch"] - samples[0]["epoch"])
    if duration < min_seconds:
        reasons.append("Baseline 24 jam belum lengkap")
    gaps = [b["epoch"] - a["epoch"] for a, b in zip(samples, samples[1:])]
    if any(g <= 0 or g > max_gap for g in gaps):
        reasons.append("Ada jeda sampel terlalu panjang atau timestamp tidak berurutan")

    mem_avail = [s.get("mem_available_bytes") for s in samples if isinstance(s.get("mem_available_bytes"), (int, float))]
    cpu = [s.get("cpu_busy_percent") for s in samples if isinstance(s.get("cpu_busy_percent"), (int, float))]
    disks = [s.get("root_disk") for s in samples]
    if not mem_avail or min(mem_avail) < 3 * GIB:
        reasons.append("MemAvailable minimum belum terbukti >= 3 GiB")
    if not cpu:
        reasons.append("Data pemakaian CPU belum lengkap")
    else:
        if max(cpu) >= 85:
            reasons.append("Puncak CPU tinggi: perlu evaluasi window beban")
        if any(sum(cpu[i:i + 5]) / 5 >= 50 for i in range(max(0, len(cpu) - 4))):
            reasons.append("Ada window CPU rata-rata tinggi; jadwalkan QA hanya pada jam idle")
    if any(not d or d.get("available_bytes", 0) < 15 * GIB or d.get("free_percent", 0) < 20 for d in disks):
        reasons.append("Kapasitas disk host tidak memenuhi >=15 GiB dan >=20% kosong")
    if any(s.get("oom_kill_delta", 0) not in (None, 0) for s in samples):
        reasons.append("Ada OOM kill di host selama survei")
    if not critical:
        reasons.append("Daftar container production kritis belum ditetapkan (--critical)")
    if any(not s.get("docker", {}).get("readable") for s in samples):
        reasons.append("Docker tidak dapat dimonitor secara konsisten dengan akses read-only")
    for name in critical:
        for s in samples:
            containers = [x for x in s.get("docker", {}).get("containers", []) if x.get("name") == name]
            if len(containers) != 1 or containers[0].get("state") != "running" or containers[0].get("health") != "healthy":
                reasons.append(f"Healthcheck production {name} belum selalu healthy/running")
                break
    latest = samples[-1] if samples else {}
    if latest.get("docker", {}).get("cgroup_version") != "2" or not latest.get("systemd_pid1"):
        reasons.append("Prasyarat awal cgroup v2/systemd belum terkonfirmasi")
    if not {"cpu", "memory", "pids"}.issubset(set(latest.get("cgroup_controllers") or [])):
        reasons.append("Controller cpu/memory/pids belum terlihat di host")
    return {
        "decision": "S0_NEEDS_REVIEW" if reasons else "S0_BASELINE_CANDIDATE",
        "qa_enabled": False,
        "note": "Bukan izin mengaktifkan QA. S2 harus menguji isolasi dan enforcement terlebih dahulu.",
        "sample_count": len(samples), "duration_hours": round(duration / 3600, 3),
        "mem_available_min_gib": round(min(mem_avail) / GIB, 2) if mem_avail else None,
        "mem_available_max_gib": round(max(mem_avail) / GIB, 2) if mem_avail else None,
        "cpu_peak_percent": max(cpu) if cpu else None,
        "critical_containers": critical,
        "reasons": sorted(set(reasons)),
    }


def _atomic_write(path, text):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(text)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Jembatan S0 read-only production audit; tidak menjalankan QA")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("snapshot", help="ambil satu sampel JSON pada stdout")
    collect = sub.add_parser("collect", help="survei 24 jam; proses terminal harus tetap hidup")
    collect.add_argument("--output", default=".s0-audit/samples.jsonl")
    collect.add_argument("--hours", type=float, default=24)
    collect.add_argument("--interval", type=int, default=60)
    report = sub.add_parser("report", help="analisis sampel yang sudah tersimpan")
    report.add_argument("--input", default=".s0-audit/samples.jsonl")
    report.add_argument("--output", default=".s0-audit/report.json")
    report.add_argument("--critical", action="append", default=[], metavar="CONTAINER")
    args = parser.parse_args(argv)
    if args.action == "snapshot":
        print(json.dumps(snapshot(), indent=2, ensure_ascii=False))
        return 0
    if args.action == "collect":
        if args.hours <= 0 or args.hours > 72 or args.interval < 10 or args.interval > 3600:
            parser.error("hours harus 0–72, interval 10–3600 detik")
        path = Path(args.output).expanduser()
        if path.exists():
            parser.error("Output sudah ada; gunakan path baru agar bukti audit tidak tercampur")
        end = time.monotonic() + args.hours * 3600
        previous_ticks = None
        previous_oom = None
        print(f"S0 read-only survey: {args.hours:g} jam, tiap {args.interval} detik. File: {path}", flush=True)
        while True:
            t0 = time.monotonic()
            sample = snapshot(previous_ticks, previous_oom)
            previous_ticks, previous_oom = sample["cpu_ticks"], sample["oom_kill_count"]
            _secure_append(path, sample)
            print(f"{sample['sampled_at']} | RAM available {sample['mem_available_bytes'] / GIB:.2f} GiB | CPU {sample['cpu_busy_percent']}%", flush=True)
            if t0 >= end:
                break
            time.sleep(min(args.interval, max(0, end - time.monotonic())))
        print("Survei selesai. Buat laporan dengan perintah report.")
        return 0
    source = Path(args.input).expanduser()
    if not source.is_file():
        parser.error(f"Tidak ditemukan: {source}")
    try:
        samples = _read_samples(source)
    except ValueError as err:
        parser.error(str(err))
    output = analyze(samples, args.critical, max_gap=3 * 60)
    target = Path(args.output).expanduser()
    _atomic_write(target, json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
