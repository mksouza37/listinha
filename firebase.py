import os
import json
import firebase_admin
from firebase_admin import credentials, firestore

# Parse JSON string from env
firebase_creds = json.loads(os.getenv("FIREBASE_CREDENTIALS"))
cred = credentials.Certificate(firebase_creds)

firebase_admin.initialize_app(cred)
db = firestore.client()

def add_item(phone, item):
    ref = db.collection("listas").document(phone)
    doc = ref.get()
    items = doc.to_dict()["itens"] if doc.exists else []
    items.append(item)
    ref.set({"itens": items})

def get_items(phone):
    ref = db.collection("listas").document(phone)
    doc = ref.get()
    return doc.to_dict()["itens"] if doc.exists else []

def clear_items(phone):
    db.collection("listas").document(phone).set({"itens": []})

def delete_item(phone, item_name):  # 👈 Renamed from remove_item
    ref = db.collection("listas").document(phone)
    doc = ref.get()
    if not doc.exists:
        return False
    items = doc.to_dict()["itens"]
    new_items = [i for i in items if i.lower() != item_name.lower()]  # case-insensitive match
    if len(new_items) == len(items):
        return False  # item not found
    ref.set({"itens": new_items})
    return True
