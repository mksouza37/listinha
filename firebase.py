import os
import json
import firebase_admin
from firebase_admin import credentials, firestore

# Parse JSON string from env
firebase_creds = json.loads(os.getenv("FIREBASE_CREDENTIALS"))
cred = credentials.Certificate(firebase_creds)

firebase_admin.initialize_app(cred)
db = firestore.client()

def add_item(phone: str, item: str):
    doc_ref = db.collection("families").document(phone)
    doc_ref.set({"items": firestore.ArrayUnion([item])}, merge=True)

def get_items(phone: str):
    doc = db.collection("families").document(phone).get()
    if doc.exists:
        data = doc.to_dict()
        return data.get("items", [])
    return []
