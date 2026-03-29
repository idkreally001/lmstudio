"""Integration test: CSV → Script → Delete workflow."""
import json
import subprocess
import time
import sys
import os

CONTAINER = "ai_sandbox"

def docker_exec(cmd, timeout=30):
    proc = subprocess.run(
        ["docker", "exec", CONTAINER, "bash", "-c", cmd],
        capture_output=True, text=True, timeout=timeout, encoding="utf-8"
    )
    return proc

def main():
    print("=== Integration Test: CSV → Script → Delete ===\n")
    errors = []

    # Step 1: Create a CSV file
    print("[1/4] Creating CSV file...")
    csv_content = "Name,Age\\nAlice,30\\nBob,25\\nCharlie,35\\nDiana,28\\nEve,32"
    proc = docker_exec(f'echo -e "{csv_content}" > /workspace/test_data.csv')
    if proc.returncode != 0:
        errors.append(f"CSV creation failed: {proc.stderr}")
    else:
        print("  ✓ CSV created")

    # Step 2: Create a Python script to compute average age
    print("[2/4] Creating analysis script...")
    script = """import csv
with open('/workspace/test_data.csv') as f:
    reader = csv.DictReader(f)
    ages = [int(row['Age']) for row in reader]
    avg = sum(ages) / len(ages)
    print(f'Average age: {avg:.1f}')
    print(f'Count: {len(ages)}')
"""
    import base64
    encoded = base64.b64encode(script.encode()).decode()
    proc = docker_exec(f"echo '{encoded}' | base64 -d > /workspace/analyze.py")
    if proc.returncode != 0:
        errors.append(f"Script creation failed: {proc.stderr}")
    else:
        print("  ✓ Script created")

    # Step 3: Run the script
    print("[3/4] Running analysis script...")
    proc = docker_exec("python3 /workspace/analyze.py")
    if proc.returncode != 0:
        errors.append(f"Script execution failed: {proc.stderr}")
    else:
        output = proc.stdout.strip()
        print(f"  ✓ Output: {output}")
        if "Average age: 30.0" not in output:
            errors.append(f"Unexpected output: {output}")
        if "Count: 5" not in output:
            errors.append(f"Missing count in output: {output}")

    # Step 4: Delete both files
    print("[4/4] Cleaning up...")
    proc = docker_exec("rm -f /workspace/test_data.csv /workspace/analyze.py")
    if proc.returncode != 0:
        errors.append(f"Cleanup failed: {proc.stderr}")
    else:
        # Verify deletion
        proc = docker_exec("ls /workspace/test_data.csv /workspace/analyze.py 2>&1")
        if "No such file" in proc.stdout or proc.returncode != 0:
            print("  ✓ Files deleted")
        else:
            errors.append("Files still exist after deletion")

    # Report
    print("\n" + "=" * 50)
    if errors:
        print(f"FAILED — {len(errors)} error(s):")
        for e in errors:
            print(f"  ✗ {e}")
        sys.exit(1)
    else:
        print("PASSED — All steps completed successfully.")
        sys.exit(0)

if __name__ == "__main__":
    main()
