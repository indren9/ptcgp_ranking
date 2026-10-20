"""Synthetic observer lifecycle fixture; never imports historical_rebuild."""
import argparse
import subprocess
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument("--case", choices=["normal", "nonzero", "child", "inactivity"], required=True)
args = parser.parse_args()
if args.case == "normal":
    print("SYNTHETIC_NORMAL", flush=True)
    time.sleep(0.8)
elif args.case == "nonzero":
    print("SYNTHETIC_EXIT_7", flush=True)
    sys.exit(7)
elif args.case == "child":
    p = subprocess.Popen(
        [sys.executable, "-u", "-c",
         "import time; print('SYNTHETIC_CHILD_ALIVE',flush=True);time.sleep(2.3)"]
    )
    print(f"SYNTHETIC_CHILD_PID={p.pid}", flush=True)
    if p.wait() != 0:
        sys.exit(9)
    time.sleep(0.8)
else:
    print("SYNTHETIC_LONG_INACTIVITY", flush=True)
    time.sleep(9)
print("SYNTHETIC_DONE", flush=True)