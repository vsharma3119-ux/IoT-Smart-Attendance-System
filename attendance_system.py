import cv2
import mediapipe as mp
import numpy as np
from scipy.spatial import distance as dist
from deepface import DeepFace
import json
import csv
import os
import requests
import base64
import pyttsx3
import time
from datetime import datetime

# Configurations

EYE_AR_THRESH        = 0.22
EYE_AR_CONSEC_FRAMES = 2
OFFLINE_FILE         = "micro_sd_log.csv"
THRESHOLD            = 0.75          # Raise to 0.65 if friend's face still passes
API_URL              = "http://127.0.0.1:8000"
NODE_SERVER          = "http://127.0.0.1:3000"

LEFT_EYE_IDX  = [33, 160, 158, 133, 153, 144]
RIGHT_EYE_IDX = [362, 385, 387, 263, 373, 380]

# Globals

last_scan_time = {}

# Initialization

engine = pyttsx3.init()

mp_face_mesh = mp.solutions.face_mesh
face_mesh    = mp_face_mesh.FaceMesh(refine_landmarks=True)

# Utility Functions

def speak(text):
    print(f"[VOICE]: {text}")
    engine.say(text)
    engine.runAndWait()


def calculate_ear(eye_points):
    v1 = dist.euclidean(eye_points[1], eye_points[5])
    v2 = dist.euclidean(eye_points[2], eye_points[4])
    h  = dist.euclidean(eye_points[0], eye_points[3])
    return (v1 + v2) / (2.0 * h)


def cosine_similarity(vec1, vec2):
    vec1 = np.array(vec1)
    vec2 = np.array(vec2)
    return np.dot(vec1, vec2) / (np.linalg.norm(vec1) * np.linalg.norm(vec2))


def frame_to_base64(frame):
    _, buffer = cv2.imencode('.jpg', frame)
    return base64.b64encode(buffer).decode('utf-8')


def is_offline():
    test_urls = [
        "https://www.google.com",
        "https://8.8.8.8",
        "https://www.cloudflare.com",
    ]
    for url in test_urls:
        try:
            requests.get(url, timeout=5)
            return False
        except:
            continue
    return True

# Attendance Storage

def save_attendance_offline(rfid_tag, name, timestamp):
    """Save attendance to CSV when offline."""
    with open(OFFLINE_FILE, "a", newline='', encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([rfid_tag, name, timestamp, "pending"])
    print(f"[OFFLINE] Saved to {OFFLINE_FILE}: {name} at {timestamp}")


def save_attendance_online(rfid_tag, frame):
    """Send attendance with face image to Flask API → MongoDB."""
    try:
        image_b64 = frame_to_base64(frame)
        response  = requests.post(
            f"{API_URL}/mark-attendance",
            json={"rfid": rfid_tag, "image": image_b64},
            timeout=10
        )
        result = response.json()
        if response.ok:
            print(f"[MONGODB]  Saved: {result.get('name')} at {result.get('time')}")
            return True, result.get('name')
        else:
            print(f"[MONGODB]  Failed: {result.get('message')}")
            return False, None
    except Exception as e:
        print(f"[MONGODB]  Error: {e}")
        return False, None


# Sync offline records to mongodb

def sync_offline_records():
    """Upload all pending offline CSV records to MongoDB via Flask API."""
    if not os.path.exists(OFFLINE_FILE):
        print("[SYNC] No offline file found. Skipping.")
        return

    rows = []
    with open(OFFLINE_FILE, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if row:
                rows.append(row)

    if not rows:
        print("[SYNC] Offline file is empty. Skipping.")
        return

    pending_new = [r for r in rows if len(r) >= 4 and r[3] == "pending"]
    old_format  = [r for r in rows if len(r) == 3]

    total = len(pending_new) + len(old_format)
    if total == 0:
        print("[SYNC] No pending records to sync.")
        return

    print(f"\n[SYNC] Uploading {total} offline records to MongoDB...")

    rfid_db = {}
    if os.path.exists("rfid_faces.json"):
        with open("rfid_faces.json", "r") as f:
            rfid_db = json.load(f)

    to_sync = []
    for r in pending_new:
        to_sync.append({"rfid": r[0], "name": r[1], "timestamp": r[2]})
    for r in old_format:
        rfid   = r[0]
        stored = rfid_db.get(rfid, {})
        name   = stored.get("name", rfid) if isinstance(stored, dict) else rfid
        to_sync.append({"rfid": rfid, "name": name, "timestamp": str(r[1])})

    synced = 0
    for record in to_sync:
        try:
            res = requests.post(
                f"{API_URL}/sync-attendance",
                json=record,
                timeout=10
            )
            if res.ok:
                synced += 1
                print(f"[SYNC]  {record['name']} at {record['timestamp']}")
            else:
                print(f"[SYNC]  {record['rfid']}: {res.json().get('message')}")
        except Exception as e:
            print(f"[SYNC]  Error uploading {record.get('rfid')}: {e}")

    with open(OFFLINE_FILE, "w", newline='', encoding="utf-8") as f:
        writer = csv.writer(f)
        for r in rows:
            if len(r) >= 4:
                writer.writerow([r[0], r[1], r[2], "synced"])
            elif len(r) == 3:
                rfid   = r[0]
                stored = rfid_db.get(rfid, {})
                name   = stored.get("name", rfid) if isinstance(stored, dict) else rfid
                writer.writerow([rfid, name, r[1], "synced"])

    print(f"[SYNC] Done! {synced}/{total} records synced.\n")
    if synced > 0:
        speak(f"Sync complete. {synced} offline records uploaded.")


# RFID Polling

def wait_for_rfid():
    """Poll Node.js server until ESP32 scans a card."""
    print("\n[RFID] Waiting for card scan...")
    speak("Please scan your RFID card.")

    while True:
        try:
            res  = requests.get(f"{NODE_SERVER}/get-uid", timeout=5)
            data = res.json()
            if data.get("uid"):
                uid = data["uid"]
                print(f"[RFID]  Card scanned: {uid}")
                return uid
        except Exception as e:
            print(f"[RFID] Polling error: {e}")
        time.sleep(1)


# Main attendance function

def run_attendance():
    """Full attendance flow: RFID → Liveness → Face verify → Save."""

    if not os.path.exists("rfid_faces.json"):
        print("[ERROR] rfid_faces.json not found!")
        speak("Database not found. Please contact administrator.")
        return

    with open("rfid_faces.json", "r") as f:
        rfid_db = json.load(f)

    rfid_tag = wait_for_rfid()

    if rfid_tag not in rfid_db:
        speak("RFID card not registered. Access denied.")
        print(f"[AUTH]  Unknown RFID: {rfid_tag}")
        return

    stored        = rfid_db[rfid_tag]
    name          = stored.get("name", "Unknown")
    stored_vector = stored.get("vector")

    if not stored_vector:
        speak("No face data found for this card. Please re-register.")
        print(f"[AUTH]  No face vector for RFID: {rfid_tag}")
        return

    # ── Step 2: Cooldown check ──────────────────────────────────
    now = time.time()
    if rfid_tag in last_scan_time:
        elapsed = now - last_scan_time[rfid_tag]
        if elapsed < 60:
            remaining = int(60 - elapsed)
            speak(f"{name}, attendance already marked. Wait {remaining} seconds.")
            print(f"[AUTH]  Duplicate scan for {name}. {remaining}s cooldown.")
            return

    speak(f"Hello {name}. Please look at the camera and blink to verify.")

    #  Webcam — Liveness Detection
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[ERROR] Could not open webcam.")
        speak("Camera error. Please try again.")
        return

    blink_counter  = 0
    blink_detected = False
    verified       = False
    start_time     = time.time()

    print("[CAM] Starting liveness check. Please blink...")

    while time.time() - start_time < 15:
        ret, frame = cap.read()
        if not ret:
            print("[CAM] Failed to read frame.")
            break

        rgb     = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = face_mesh.process(rgb)

        if results.multi_face_landmarks:
            landmarks = results.multi_face_landmarks[0].landmark
            h, w      = frame.shape[:2]

            left_pts  = [(int(landmarks[i].x * w), int(landmarks[i].y * h)) for i in LEFT_EYE_IDX]
            right_pts = [(int(landmarks[i].x * w), int(landmarks[i].y * h)) for i in RIGHT_EYE_IDX]

            left_ear  = calculate_ear(left_pts)
            right_ear = calculate_ear(right_pts)
            ear       = (left_ear + right_ear) / 2.0

            # Blink detection
            if ear < EYE_AR_THRESH:
                blink_counter += 1
            else:
                if blink_counter >= EYE_AR_CONSEC_FRAMES:
                    blink_detected = True
                    print("[LIVENESS]  Blink detected!")
                blink_counter = 0

            # HUD overlay
            color  = (0, 255, 0) if blink_detected else (0, 0, 255)
            status = "Blink Detected!" if blink_detected else f"EAR: {ear:.2f} — Please blink"
            cv2.putText(frame, status,                (10, 30),  cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
            cv2.putText(frame, f"ID: {name}",         (10, 60),  cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
            time_left = max(0, int(15 - (time.time() - start_time)))
            cv2.putText(frame, f"Time: {time_left}s", (10, 90),  cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

            # ── Step 4: Face verification after blink ───────────
            if blink_detected and not verified:

                print("[FACE] Waiting 0.8s for eyes to fully open...")
                time.sleep(0.8)

                print("[FACE] Running face verification (3 attempts)...")
                best_similarity = 0.0
                best_frame      = frame

                for attempt in range(3):
                    ret2, capture_frame = cap.read()
                    if not ret2:
                        break
                    try:
                        result      = DeepFace.represent(
                            img_path=capture_frame,
                            model_name="Facenet",
                            enforce_detection=False
                        )
                        live_vector = result[0]["embedding"]
                        similarity  = cosine_similarity(live_vector, stored_vector)
                        print(f"[FACE] Attempt {attempt+1}: similarity = {similarity:.4f}")
                        if similarity > best_similarity:
                            best_similarity = similarity
                            best_frame      = capture_frame
                    except Exception as e:
                        print(f"[FACE] Attempt {attempt+1} error: {e}")
                    time.sleep(0.3)

                similarity = best_similarity
                frame      = best_frame
                print(f"[FACE] Best similarity: {similarity:.4f} (threshold: {THRESHOLD})")

                if similarity >= THRESHOLD:
                    verified = True
                    last_scan_time[rfid_tag] = time.time()
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                    cv2.putText(frame, "VERIFIED", (10, 130),
                                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 3)
                    cv2.imshow("Attendance System", frame)
                    cv2.waitKey(1500)

                    speak(f"Attendance marked for {name}.")
                    print(f"[AUTH]  Verified: {name} at {timestamp}")

                    if not is_offline():
                        success, _ = save_attendance_online(rfid_tag, frame)
                        if not success:
                            print("[FALLBACK] Online save failed, saving offline.")
                            save_attendance_offline(rfid_tag, name, timestamp)
                    else:
                        save_attendance_offline(rfid_tag, name, timestamp)

                else:
                    cv2.putText(frame, "FACE MISMATCH", (10, 130),
                                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 3)
                    cv2.imshow("Attendance System", frame)
                    cv2.waitKey(1500)
                    speak("Face does not match. Access denied.")
                    print(f"[AUTH]  Face mismatch. Best similarity: {similarity:.4f}")

                break

        cv2.imshow("Attendance System", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            print("[INFO] Quit by user.")
            break

    cap.release()
    cv2.destroyAllWindows()

    if not blink_detected:
        speak("No blink detected. Liveness check failed. Please try again.")
        print("[AUTH]  Liveness check failed — no blink in 15 seconds.")
    elif not verified:
        print("[AUTH]  Verification failed.")


# Program entry point

if __name__ == "__main__":

    print("=" * 50)
    print("   IoT Attendance System — Starting Up")
    print("=" * 50)

    print("\n[STARTUP] Checking for pending offline records...")
    if not is_offline():
        sync_offline_records()
    else:
        print("[STARTUP] Device is offline — sync will happen when back online.\n")

    while True:
        run_attendance()
        cont = input("\nMark another attendance? (y/n): ").strip().lower()
        if cont != 'y':
            print("\n[EXIT] Attendance system closed.")
            break