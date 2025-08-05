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

def is_admin(phone):
    ref = db.collection("users").document(phone)
    doc = ref.get()
    if not doc.exists:
        return False
    group = doc.to_dict().get("group", {})
    return group.get("role") == "admin"

def add_user_to_list(admin_phone, target_phone):
    # Check if target is already in a list
    if user_in_list(target_phone):
        return False, "already_in_list"

    # Get admin group
    admin_ref = db.collection("users").document(admin_phone)
    admin_group = admin_ref.get().to_dict()["group"]

    # Create target user document
    target_group = {
        "owner": admin_group["owner"],  # admin's phone
        "list": admin_group["list"],
        "instance": admin_group["instance"],
        "role": "user"
    }
    db.collection("users").document(target_phone).set({"group": target_group})

    # Add target to members in list
    doc_id = f"{admin_group['instance']}__{admin_group['owner']}__{admin_group['list']}"
    list_ref = db.collection("listas").document(doc_id)
    list_ref.update({"members": firestore.ArrayUnion([target_phone])})

    return True, "added"

def remove_user_from_list(admin_phone, target_phone):
    admin_ref = db.collection("users").document(admin_phone)
    admin_group = admin_ref.get().to_dict()["group"]

    doc_id = f"{admin_group['instance']}__{admin_group['owner']}__{admin_group['list']}"
    list_ref = db.collection("listas").document(doc_id)
    list_data = list_ref.get().to_dict()

    members = list_data.get("members", [])
    if target_phone not in members:
        return False  # Not a member

    new_members = [m for m in members if m != target_phone]
    list_ref.update({"members": new_members})

    db.collection("users").document(target_phone).delete()
    return True


def remove_self_from_list(user_phone):
    user_ref = db.collection("users").document(user_phone)
    user_group = user_ref.get().to_dict()["group"]

    doc_id = f"{user_group['instance']}__{user_group['owner']}__{user_group['list']}"
    list_ref = db.collection("listas").document(doc_id)
    list_data = list_ref.get().to_dict()

    if user_group["role"] == "admin":
        return False  # Admins cannot self-remove

    members = list_data.get("members", [])
    new_members = [m for m in members if m != user_phone]
    list_ref.update({"members": new_members})

    db.collection("users").document(user_phone).delete()
    return True
