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
    if not ref.get().exists:  # <-- FIX: removed ()
        # Create user as admin of a new list
        group_data = {
            "owner": phone,
            "list": "default",
            "instance": instance_id,
            "role": "admin"
        }
        ref.set({"group": group_data})

        # Create the list document
        doc_id = f"{instance_id}__{phone}__default"
        list_data = {
            "owner": phone,
            "members": [phone],
            "itens": []
        }
        db.collection("listas").document(doc_id).set(list_data)

        # Debug prints
        print(f"✅ Created new admin list: {doc_id}")
        print(f"📄 User doc: {group_data}")
        print(f"📄 List doc: {list_data}")

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

def user_in_list(phone):
    ref = db.collection("users").document(phone)
    doc = ref.get()
    return doc.exists  # True if user is already in a list

def create_new_list(phone, instance_id="default"):
    group_data = {
        "owner": phone,
        "list": "default",
        "instance": instance_id,
        "role": "admin"
    }
    db.collection("users").document(phone).set({"group": group_data})

    doc_id = f"{instance_id}__{phone}__default"
    list_data = {
        "owner": phone,
        "members": [phone],
        "itens": []
    }
    db.collection("listas").document(doc_id).set(list_data)

    print(f"✅ New list created for {phone} in {instance_id}")
    return doc_id

def eliminate_user(phone):
    # Remove from users collection
    db.collection("users").document(phone).delete()

    # Remove from any listas members and delete if they are the owner
    listas_ref = db.collection("listas").stream()
    for lista_doc in listas_ref:
        lista_data = lista_doc.to_dict()
        if lista_data.get("owner") == phone:
            db.collection("listas").document(lista_doc.id).delete()
        elif "members" in lista_data and phone in lista_data["members"]:
            new_members = [m for m in lista_data["members"] if m != phone]
            db.collection("listas").document(lista_doc.id).update({"members": new_members})

    print(f"🗑️ Eliminated user {phone} from database.")




