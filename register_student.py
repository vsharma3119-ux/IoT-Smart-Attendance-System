import os
import json
import requests
import base64
from deepface import DeepFace

# Configurstion
FOLDER_PATH  = "known_faces"    # put your UID_Name.jpg files here
DB_FILE      = "rfid_faces.json"
API_URL      = "http://127.0.0.1:8000"

#  File naming convention 
# Files inside known_faces/ must be named:   UID_Full Name.jpg

#   1234_Vaibhav Sharma.jpg

# Helpers

def is_api_reachable():
    """Return True if the Flask backend is running."""
    try:
        requests.get(f"{API_URL}/get-students", timeout=3)
        return True
    except Exception:
        return False


def load_existing_db():
    """Load rfid_faces.json or return empty dict if missing/corrupt."""
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                print(f"  {DB_FILE} was corrupt — starting fresh.")
    return {}


def register_to_mongodb(rfid, name, img_path):
    """
    POST the student to Flask /register-student.
    Flask will save to MongoDB AND update rfid_faces.json automatically.
    Returns True on success.
    """
    try:
        with open(img_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")

        # Send as plain base64 (no data-URI prefix) — Flask handles both formats
        res = requests.post(
            f"{API_URL}/register-student",
            json={"rfid": rfid, "name": name, "image": b64},
            timeout=30,
        )
        if res.ok:
            print(f"   [MongoDB]  Saved via API")
            return True
        else:
            print(f"   [MongoDB]  API error: {res.json().get('message')}")
            return False
    except Exception as e:
        print(f"   [MongoDB]  Could not reach API: {e}")
        return False


# Batch Registration

print("=" * 50)
print("   BATCH REGISTRATION — Starting")
print("=" * 50)

if not os.path.isdir(FOLDER_PATH):
    print(f" Folder '{FOLDER_PATH}' not found. Please create it and add images.")
    exit(1)

api_online = is_api_reachable()
if api_online:
    print(f" Flask API reachable at {API_URL} — will sync to MongoDB too.")
else:
    print(f"  Flask API not reachable — registering to {DB_FILE} only.")
    print(f"   Start deepface_api.py and re-run to sync to MongoDB.\n")

# Load existing JSON DB so we don't wipe old entries
db = load_existing_db()
newly_registered = 0
skipped          = 0
failed           = 0

for filename in sorted(os.listdir(FOLDER_PATH)):
    if not (filename.endswith(".jpg") or filename.endswith(".png")):
        continue

    # ── Parse UID and Name from filename ────────────────────────
    name_part = os.path.splitext(filename)[0]   # e.g. "A4784F06_John Doe"

    if "_" in name_part:
        uid  = name_part.split("_")[0]           # "A4784F06"
        name = name_part.split("_", 1)[1]        # "John Doe"
    else:
        uid  = name_part
        name = name_part

    img_path = os.path.join(FOLDER_PATH, filename)
    print(f"\n→ Processing: {name}  (UID: {uid})")

    # ── Skip if already in JSON DB (unless you want to re-register) ─
    if uid in db:
        print(f"   [SKIP] Already registered in {DB_FILE}. Delete entry to re-register.")
        skipped += 1
        continue

    # ── Extract face embedding ───────────────────────────────────
    try:
        result    = DeepFace.represent(
            img_path=img_path,
            model_name="Facenet",
            enforce_detection=True,
        )
        embedding = result[0]["embedding"]
    except Exception as e:
        print(f"   DeepFace error: {e}")
        failed += 1
        continue

    # ── FIX #10 + FIX #1: Write to rfid_faces.json first ────────
    # Always use "vector" as the key (standardized — fixes Bug #1).
    from datetime import datetime
    db[uid] = {
        "name":          name,
        "vector":        embedding,            # ← always "vector", never "embedding"
        "registered_on": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    # Save JSON immediately (so partial runs still persist)
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=4)

    print(f"   Saved to {DB_FILE}")

    # ── FIX #10: Also register in MongoDB via Flask API ─────────
    if api_online:
        register_to_mongodb(uid, name, img_path)

    newly_registered += 1

# Summary

print("\n" + "=" * 50)
print(f"   DONE — {len(db)} total students in {DB_FILE}")
print(f"   Newly registered : {newly_registered}")
print(f"   Skipped (exists) : {skipped}")
print(f"   Failed           : {failed}")
if not api_online and newly_registered > 0:
    print(f"\n   ⚠️  MongoDB NOT updated (API offline).")
    print(f"   To sync, start deepface_api.py then re-run this script")
    print(f"   (already-registered entries will be skipped in JSON but")
    print(f"    you can remove their entries from {DB_FILE} to force re-sync).")
print("=" * 50)