import csv
import json
import os
import requests

# Configuration 

OFFLINE_FILE = "micro_sd_log.csv"
API_URL      = "http://127.0.0.1:8000"
RFID_DB_FILE = "rfid_faces.json"


def is_online():
    try:
        requests.get(f"{API_URL}/get-students", timeout=3)
        return True
    except Exception:
        return False


def load_rfid_db():
    if os.path.exists(RFID_DB_FILE):
        with open(RFID_DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def sync_offline_to_api():
    if not is_online():
        print("[!] Flask API is not reachable. Cannot sync yet.")
        return

    if not os.path.exists(OFFLINE_FILE):
        print("[i] No offline file found. Nothing to sync.")
        return

    rows = []
    with open(OFFLINE_FILE, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if row:
                rows.append(row)

    if not rows:
        print("[i] Offline file is empty. Nothing to sync.")
        return

    rfid_db = load_rfid_db()
    pending = []

    for r in rows:
        if len(r) >= 4:
            if r[3].strip().lower() == "pending":
                pending.append({
                    "rfid":      r[0].strip(),
                    "name":      r[1].strip(),
                    "timestamp": r[2].strip(),
                    "_row":      r
                })
        elif len(r) == 2:
            rfid   = r[0].strip()
            stored = rfid_db.get(rfid, {})
            name   = stored.get("name", rfid) if isinstance(stored, dict) else rfid
            pending.append({
                "rfid":      rfid,
                "name":      name,
                "timestamp": r[1].strip(),
                "_row":      r
            })

    if not pending:
        print("[i] No pending records found. Everything is already synced.")
        return

    print(f"\n[SYNC] Found {len(pending)} pending record(s). Uploading...")

    synced_rows = set()

    for record in pending:
        try:
            res = requests.post(
                f"{API_URL}/sync-attendance",
                json={
                    "rfid":      record["rfid"],
                    "name":      record["name"],
                    "timestamp": record["timestamp"],
                },
                timeout=10
            )
            if res.ok:
                print(f"[SYNC]   {record['name']} at {record['timestamp']}")
                synced_rows.add(id(record["_row"]))
            else:
                msg = res.json().get("message", "unknown error")
                print(f"[SYNC]   {record['rfid']}: {msg}")
        except Exception as e:
            print(f"[SYNC]   Error uploading {record['rfid']}: {e}")

    with open(OFFLINE_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for r in rows:
            if len(r) >= 4:
                status = "synced" if id(r) in synced_rows else r[3]
                writer.writerow([r[0], r[1], r[2], status])
            elif len(r) == 2:
                rfid   = r[0].strip()
                stored = rfid_db.get(rfid, {})
                name   = stored.get("name", rfid) if isinstance(stored, dict) else rfid
                status = "synced" if id(r) in synced_rows else "pending"
                writer.writerow([rfid, name, r[1].strip(), status])
            else:
                writer.writerow(r)

    total   = len(pending)
    success = len(synced_rows)
    print(f"\n[SYNC] Done — {success}/{total} records synced.\n")


if __name__ == "__main__":
    sync_offline_to_api()