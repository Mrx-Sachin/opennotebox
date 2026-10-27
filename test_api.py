import httpx
import time
import sys

BASE_URL = "http://localhost:8001/api"

def test_flow():
    client = httpx.Client(timeout=120.0)
    
    # 1. Register
    username = f"user_{int(time.time())}"
    email = f"{username}@example.com"
    password = "password123"
    
    print(f"Registering user: {username} ...")
    r = client.post(f"{BASE_URL}/auth/register", json={
        "username": username,
        "email": email,
        "password": password
    })
    if r.status_code != 200:
        print(f"Registration failed: {r.status_code} {r.text}")
        sys.exit(1)
    print("Registration successful!")
    
    # 2. Login
    print("Logging in ...")
    r = client.post(f"{BASE_URL}/auth/login", json={
        "username": username,
        "password": password
    })
    if r.status_code != 200:
        print(f"Login failed: {r.status_code} {r.text}")
        sys.exit(1)
    token_data = r.json()
    access_token = token_data["access_token"]
    print("Login successful! Token acquired.")
    
    # Set auth header
    client.headers["Authorization"] = f"Bearer {access_token}"
    
    # 3. Get profile
    r = client.get(f"{BASE_URL}/auth/me")
    print(f"Me endpoint: {r.status_code} {r.json()}")
    
    # 4. Upload file
    print("Uploading sample_testing.txt ...")
    files = {"file": ("sample_testing.txt", open("sample_testing.txt", "rb"), "text/plain")}
    r = client.post(f"{BASE_URL}/documents/upload", files=files)
    if r.status_code != 200:
        print(f"Upload failed: {r.status_code} {r.text}")
        sys.exit(1)
    doc = r.json()
    doc_id = doc["id"]
    print(f"Upload successful! Doc ID: {doc_id}")
    
    # 5. Poll processing status
    print("Polling document status ...")
    for _ in range(30):
        r = client.get(f"{BASE_URL}/documents/{doc_id}")
        if r.status_code != 200:
            print(f"Failed to get doc: {r.status_code} {r.text}")
            sys.exit(1)
        doc_info = r.json()
        status = doc_info.get("processing_status")
        print(f"Status: {status}")
        if status == "completed":
            print("Document processing completed successfully!")
            break
        elif status == "failed":
            print("Document processing failed!")
            # Get logs or errors if possible
            sys.exit(1)
        time.sleep(2)
    else:
        print("Polling timed out!")
        sys.exit(1)
        
    # 6. Retrieve summaries
    print("Retrieving summaries ...")
    r = client.get(f"{BASE_URL}/documents/{doc_id}/summaries")
    if r.status_code != 200:
        print(f"Failed to get summaries: {r.status_code} {r.text}")
        sys.exit(1)
    summaries = r.json()
    print(f"Summaries received ({len(summaries)}):")
    for s in summaries:
        print(f"- Type: {s['mode']}, Content: {s['content'][:100]}...")
        
    # 7. Ask chat question
    print("Starting chat conversation ...")
    # First create a conversation session
    r = client.post(f"{BASE_URL}/chat/sessions")
    if r.status_code != 200:
        print(f"Failed to create conversation: {r.status_code} {r.text}")
        sys.exit(1)
    conv = r.json()
    conv_id = conv["id"]
    print(f"Conversation ID: {conv_id}")
    
    print("Sending message ...")
    r = client.post(f"{BASE_URL}/chat/sessions/{conv_id}/messages", json={
        "document_ids": [doc_id],
        "content": "What is keep it simple?"
    })
    if r.status_code != 200:
        print(f"Message failed: {r.status_code} {r.text}")
        sys.exit(1)
    msg_resp = r.json()
    print("AI Response:", msg_resp["content"])
    
    # 8. Generate TTS
    # Let's find a summary ID
    summary_id = summaries[0]["id"] if summaries else None
    if summary_id:
        # TTS endpoint is /api/tts/play/{summary_id}
        r = client.post(f"{BASE_URL}/tts/play/{summary_id}")
        print(f"TTS response status: {r.status_code}")
        if r.status_code == 200:
            print("TTS response:", r.json())
        else:
            print(f"TTS failed: {r.text}")
            
if __name__ == "__main__":
    test_flow()
