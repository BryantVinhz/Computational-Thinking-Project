import os
import requests

def _auth_request(endpoint, email, password):
    api_key = os.getenv("FIREBASE_API_KEY", "").strip()
    if not api_key:
        raise ValueError("Missing FIREBASE_API_KEY in .env file.")

    url = f"https://identitytoolkit.googleapis.com/v1/accounts:{endpoint}?key={api_key}"
    payload = {"email": email, "password": password, "returnSecureToken": True}
    
    try:
        res = requests.post(url, json=payload, timeout=15)
        data = res.json()
        if not res.ok:
            err_msg = data.get("error", {}).get("message", "Authentication failed.")
            raise ValueError(f"Firebase Auth Error: {err_msg}")
        return {
            "email": data["email"],
            "uid": data["localId"],
            "idToken": data["idToken"],
        }
    except requests.RequestException as e:
        raise ConnectionError(f"Cannot connect to Firebase Auth: {e}")

def sign_up(email, password):
    return _auth_request("signUp", email, password)

def sign_in(email, password):
    return _auth_request("signInWithPassword", email, password)