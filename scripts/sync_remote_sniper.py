"""
sync_remote_sniper.py

Pulls oracle-lag-sniper's var/logs/ from the AWS Lightsail box down
to a local mirror at ~/.ols-sniper-cloud/var/logs/, so the local
dashboard can read it alongside the locally-running sniper.

Uses scp (bundled with Windows 10+ OpenSSH client) so no extra deps.

After scp, optionally rebuilds the cloud's SQLite mirror via
sync_sniper_to_sqlite.py so compare_strategies.py has fresh data.

Usage:
    python scripts/sync_remote_sniper.py
    python scripts/sync_remote_sniper.py --host ubuntu@54.87.91.133 \\
                                          --key C:/Users/dylan/.ssh/LightsailDefaultKey-us-east-1.pem

Defaults assume:
    - SSH key at ~/.ssh/LightsailDefaultKey-us-east-1.pem
    - Lightsail public IP 54.87.91.133
    - Cloud OLS_HOME at /home/ubuntu/oracle-lag-sniper/
    - Local mirror at ~/.ols-sniper-cloud/var/logs/
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT       = Path(__file__).resolve().parent.parent
DEFAULT_KEY     = Path.home() / ".ssh" / "LightsailDefaultKey-us-east-1.pem"
DEFAULT_HOST    = "ubuntu@54.87.91.133"
DEFAULT_REMOTE  = "/home/ubuntu/oracle-lag-sniper/var/logs"
DEFAULT_LOCAL   = Path.home() / ".ols-sniper-cloud" / "var" / "logs"
DEFAULT_DB      = Path.home() / ".ols-sniper-cloud" / "sniper.db"


def have_scp() -> bool:
    return shutil.which("scp") is not None


def pull(host: str, key: Path, remote: str, local_dir: Path) -> bool:
    local_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "scp",
        "-i", str(key),
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "BatchMode=yes",
        "-q",
        f"{host}:{remote}/state.json",
        f"{host}:{remote}/events.jsonl",
        f"{host}:{remote}/signals.jsonl",
        f"{host}:{remote}/attempts.jsonl",
        f"{host}:{remote}/trades.jsonl",
        f"{host}:{remote}/resolutions.jsonl",
        str(local_dir) + "/",
    ]
    # Some files may not exist yet on the cloud (e.g. resolutions before first
    # market resolves). scp returns non-zero when any single source is missing,
    # but the others still copy. So we run it once and tolerate exit codes.
    result = subprocess.run(cmd, capture_output=True, text=True)
    pulled = sum(1 for f in local_dir.glob("*.json*") if f.is_file())
    if pulled == 0:
        print(f"  scp pulled 0 files. stderr:\n{result.stderr.strip()}")
        return False
    return True


def maybe_rebuild_sqlite(local_dir: Path, db: Path) -> None:
    """If sync_sniper_to_sqlite.py is alongside this script, run it to
    rebuild the cloud sniper's SQLite mirror so compare_strategies.py
    can join cloud + paper-lab data.
    """
    sync_script = REPO_ROOT / "scripts" / "sync_sniper_to_sqlite.py"
    if not sync_script.exists():
        return
    # OLS_HOME for the adapter is the parent of var/logs/
    ols_home = local_dir.parent.parent
    cmd = [sys.executable, str(sync_script),
           "--ols-home", str(ols_home), "--db", str(db)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        # last line is "sync OK | trades=N resolutions=M"
        last = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        print(f"  sqlite mirror: {last}")
    else:
        print(f"  sqlite rebuild failed: {result.stderr.strip()}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host",   default=DEFAULT_HOST)
    parser.add_argument("--key",    type=Path, default=DEFAULT_KEY)
    parser.add_argument("--remote", default=DEFAULT_REMOTE)
    parser.add_argument("--local",  type=Path, default=DEFAULT_LOCAL)
    parser.add_argument("--db",     type=Path, default=DEFAULT_DB)
    parser.add_argument("--no-sqlite", action="store_true",
                        help="skip rebuilding the sqlite mirror")
    args = parser.parse_args()

    if not have_scp():
        print("ERROR: scp not found on PATH. On Windows 10+, install OpenSSH "
              "Client via Settings -> Apps -> Optional features. On other OSes, "
              "install openssh-client.")
        return 2

    if not args.key.exists():
        print(f"ERROR: SSH key not found at {args.key}")
        print(f"       Download the Lightsail default key (Account -> SSH keys),")
        print(f"       save it to {args.key}, and re-run.")
        print(f"       Or pass --key <path> to point at a different location.")
        return 2

    print(f"sync_remote_sniper")
    print(f"  host   : {args.host}")
    print(f"  key    : {args.key}")
    print(f"  remote : {args.remote}")
    print(f"  local  : {args.local}")
    print()

    print("scp-ing JSONL + state.json from cloud...")
    if not pull(args.host, args.key, args.remote, args.local):
        return 1

    files = sorted(args.local.glob("*"))
    for f in files:
        sz = f.stat().st_size
        print(f"  {f.name:24s} {sz:>10,d} bytes")

    if not args.no_sqlite:
        print()
        print("rebuilding sqlite mirror...")
        maybe_rebuild_sqlite(args.local, args.db)

    print()
    print("sync complete. dashboard.py will pick up the cloud section "
          "on its next refresh.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
