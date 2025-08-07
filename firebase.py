import os
import json
import firebase_admin
from firebase_admin import credentials, firestore
from icu import Collator, Locale
collator = Collator.createInstance(Locale("pt_BR"))

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

    item = item.strip().capitalize()
    print(f"📥 Trying to insert item: {item}")

    if any(i.lower() == item.lower() for i in items):
        return False  # Duplicate

    items.append(item)
    ref.set({"itens": items}, merge=True)
    return True

def get_items(phone):
    group = get_user_group(phone)
    doc_id = f"{group.get('instance', 'default')}__{group['owner']}__{group['list']}"
    ref = db.collection("listas").document(doc_id)
    doc = ref.get()
    items = doc.to_dict()["itens"] if doc.exists else []
    return sorted(items, key=collator.getSortKey)


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

def add_user_to_list(admin_phone, target_phone, name=""):
    from firebase_admin import firestore

    users_ref = firestore.client().collection("users")
    admin_doc = users_ref.document(admin_phone).get()
    if not admin_doc.exists:
        return False, "admin_not_found"

    group_info = admin_doc.to_dict().get("group")
    if not group_info:
        return False, "group_not_found"

    # Avoid copying admin role
    new_group_info = {
        "instance": group_info["instance"],
        "list": group_info["list"],
        "owner": group_info["owner"],
        "role": "user"  # 👈 force "user" role
    }

    user_ref = users_ref.document(target_phone)
    if user_ref.get().exists:
        existing_group = user_ref.get().to_dict().get("group", {})
        if (
            existing_group.get("instance") == new_group_info["instance"]
            and existing_group.get("list") == new_group_info["list"]
            and existing_group.get("owner") == new_group_info["owner"]
        ):
            return False, "already_in_list"

    user_ref.set({
        "group": new_group_info,
        "name": name[:20]
    })
    return True, "added"

def remove_user_from_list(admin_phone, target_phone):
    admin_ref = db.collection("users").document(admin_phone)
    if not admin_ref.get().exists:
        return False
    admin_group = admin_ref.get().to_dict()["group"]

    target_ref = db.collection("users").document(target_phone)
    if not target_ref.get().exists:
        return False
    target_group = target_ref.get().to_dict()["group"]

    # Check same list via users data
    if (target_group["owner"] != admin_group["owner"] or
        target_group["list"] != admin_group["list"] or
        target_group["instance"] != admin_group["instance"]):
        return False

    # Remove from members array (optional for display)
    doc_id = f"{admin_group['instance']}__{admin_group['owner']}__{admin_group['list']}"
    list_ref = db.collection("listas").document(doc_id)
    list_data = list_ref.get().to_dict()
    members = list_data.get("members", [])
    if target_phone in members:
        members = [m for m in members if m != target_phone]
        list_ref.update({"members": members})

    # Delete target user document
    db.collection("users").document(target_phone).delete()
    return True

def remove_self_from_list(user_phone):
    user_ref = db.collection("users").document(user_phone)
    if not user_ref.get().exists:
        return False

    user_group = user_ref.get().to_dict()["group"]

    # Admins cannot self-remove
    if user_group["role"] == "admin":
        return False

    # Remove from members array (optional for display)
    doc_id = f"{user_group['instance']}__{user_group['owner']}__{user_group['list']}"
    list_ref = db.collection("listas").document(doc_id)
    list_data = list_ref.get().to_dict()
    members = list_data.get("members", [])
    if user_phone in members:
        members = [m for m in members if m != user_phone]
        list_ref.update({"members": members})

    # Delete the user document
    db.collection("users").document(user_phone).delete()
    return True

def propose_admin_transfer(admin_phone, target_phone):
    admin_ref = db.collection("users").document(admin_phone)
    if not admin_ref.get().exists:
        return False
    admin_group = admin_ref.get().to_dict()["group"]

    target_ref = db.collection("users").document(target_phone)
    if not target_ref.get().exists:
        return False
    target_group = target_ref.get().to_dict()["group"]

    # Check same list via users data
    if (target_group["owner"] != admin_group["owner"] or
        target_group["list"] != admin_group["list"] or
        target_group["instance"] != admin_group["instance"]):
        return False

    # Store pending transfer
    target_ref.update({
        "pending_admin_transfer": {
            "from": admin_phone,
            "doc_id": f"{admin_group['instance']}__{admin_group['owner']}__{admin_group['list']}"
        }
    })
    return True

def accept_admin_transfer(user_phone):
    user_ref = db.collection("users").document(user_phone)
    user_data = user_ref.get().to_dict()

    pending = user_data.get("pending_admin_transfer")
    if not pending:
        return False

    from_phone = pending["from"]
    doc_id = pending["doc_id"]

    # Update target to admin
    user_group = user_data["group"]
    user_group["role"] = "admin"
    db.collection("users").document(user_phone).set({"group": user_group}, merge=True)

    # Update old admin to user
    from_ref = db.collection("users").document(from_phone)
    from_group = from_ref.get().to_dict()["group"]
    from_group["role"] = "user"
    db.collection("users").document(from_phone).set({"group": from_group}, merge=True)

    # Remove pending transfer
    db.collection("users").document(user_phone).update({"pending_admin_transfer": firestore.DELETE_FIELD})

    return {"from": from_phone}
