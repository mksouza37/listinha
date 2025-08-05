import os
import json
import firebase_admin
from firebase_admin import credentials, firestore

# Parse JSON string from env
firebase_creds = json.loads(os.getenv("FIREBASE_CREDENTIALS"))
cred = credentials.Certificate(firebase_creds)

firebase_admin.initialize_app(cred)
db = firestore.client()

def get_user_group(phone):
    ref = db.collection("users").document(phone)
    doc = ref.get()
    if doc.exists:
        return doc.to_dict().get("group")
    return {"owner": phone, "list": "default", "instance": "default"}

def set_default_group_if_missing(phone, instance_id="default"):
    ref = db.collection("users").document(phone)
    if not ref.get().exists:
        ref.set({"group": {"owner": phone, "list": "default", "instance": instance_id}})

def add_item(phone, item):
    group = get_user_group(phone)
    doc_id = f"{group.get('instance', 'default')}__{group['owner']}__{group['list']}"
    ref = db.collection("listas").document(doc_id)
    doc = ref.get()
    items = doc.to_dict()["itens"] if doc.exists else []

    if any(i.lower() == item.lower() for i in items):
        return False  # Duplicate

    items.append(item)
    ref.set({"itens": items})
    return True


def get_items(phone):
    group = get_user_group(phone)
    doc_id = f"{group.get('instance', 'default')}__{group['owner']}__{group['list']}"
    ref = db.collection("listas").document(doc_id)
    doc = ref.get()
    return doc.to_dict()["itens"] if doc.exists else []

def clear_items(phone):
    group = get_user_group(phone)
    doc_id = f"{group.get('instance', 'default')}__{group['owner']}__{group['list']}"
    db.collection("listas").document(doc_id).set({"itens": []})

def delete_item(phone, item_name):
    group = get_user_group(phone)
    doc_id = f"{group.get('instance', 'default')}__{group['owner']}__{group['list']}"
    ref = db.collection("listas").document(doc_id)
    doc = ref.get()
    if not doc.exists:
        return False
    items = doc.to_dict()["itens"]
    new_items = [i for i in items if i.lower() != item_name.lower()]
    if len(new_items) == len(items):
        return False
    ref.set({"itens": new_items})
    return True
