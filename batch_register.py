import os
import json
import base64
import requests
from deepface import DeepFace
from datetime import datetime

db          = {}
folder_path = "known_faces"
DB_FILE     = "rfid_faces.json"
API_URL     = "http://127.0.0.1:8000"

def is_api_online():
    try:
        requests.get(f"{API_URL}/get-students", timeout=3)
        return True
    except:
        return False

print("=" * 50)
print("   BATCH REGISTRATION — Starting")
print("=" * 50)

if not os.path.isdir(folder_path):
    print(f" Folder '{folder_path}' not found.")
    exit(1)

if os.path.exists(DB_FILE):
    with open(DB_FILE, "r") as f:
        try:
            db = json.load(f)
        except:
            db = {}

api_online = is_api_online()
print(f"Flask API: {' Online — will sync to MongoDB' if api_online else '❌ Offline — saving to JSON only'}\n")

newly_registered = 0
skipped          = 0
failed           = 0

for filename in sorted(os.listdir(folder_path)):
    if not (filename.endswith(".jpg") or filename.endswith(".png")):
        continue

    name_part = os.path.splitext(filename)[0]
    uid  = name_part.split("_")[0] if "_" in name_part else name_part
    name = name_part.split("_", 1)[1] if "_" in name_part else name_part

    img_path = os.path.join(folder_path, filename)
    print(f"→ Processing: {name}  (UID: {uid})")

    if uid in db:
        print(f"   [SKIP] Already in JSON. Delete entry to re-register.")
        skipped += 1
        continue

    try:
        result    = DeepFace.represent(
            img_path=img_path,
            model_name="Facenet",
            enforce_detection=False
        )
        embedding = result[0]["embedding"]
    except Exception as e:
        print(f"    DeepFace error: {e}")
        failed += 1
        continue

    # Save to rfid_faces.json
    db[uid] = {
        "name":          name,
        "vector":        embedding,
        "registered_on": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(DB_FILE, "w") as f:
        json.dump(db, f, indent=4)
    print(f"    Saved to {DB_FILE}")

    # Also save to MongoDB via Flask API
    if api_online:
        try:
            with open(img_path, "rb") as img_file:
                b64 = base64.b64encode(img_file.read()).decode("utf-8")

            res = requests.post(
                f"{API_URL}/register-student",
                json={"rfid": uid, "name": name, "image": b64},
                timeout=30
            )
            if res.ok:
                print(f"    Saved to MongoDB")
            else:
                print(f"    MongoDB error: {res.json().get('message')}")
        except Exception as e:
            print(f"    MongoDB error: {e}")

    newly_registered += 1

print("\n" + "=" * 50)
print(f"   DONE — {len(db)} total in {DB_FILE}")
print(f"   Newly registered : {newly_registered}")
print(f"   Skipped          : {skipped}")
print(f"   Failed           : {failed}")
print("=" * 50)