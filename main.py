import secrets
import string
import json
import os
import io
import threading
from uuid import uuid4
from flask import Flask, request, jsonify, send_file,  Response
from flask_cors import CORS
import requests
from io import BytesIO
from telegram import Bot, InputFile
from telegram.ext import Updater
from telegram.error import TelegramError
import filetype


ID = os.getenv("ID")
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID")
DEBUG_USER_ID = os.getenv("DEBUG_USER_ID")
app = Flask(__name__)
bot = Bot(token=BOT_TOKEN)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20MB
CORS(app)
@app.errorhandler(413)
def request_entity_too_large(error):
    return jsonify({"error": "File too large. Max allowed size is 20MB"}), 413

@app.route("/")
def home():
    return "Use /get/<api> to fetch a table. Use /get/<api>/<file_id> to stream a Telegram file. Use POST /upload/<path>/<api> to upload."

# @app.route("/start")
# def start():
#     bot.send_message(
#         chat_id=GROUP_CHAT_ID,
#         text=396,
#     )
#     return "done"



@app.route("/get/<string:api>")
def get_item(api):

    type_ = request.args.get("type")   
    value = request.args.get("value")

    
    check = check_api(api=api)
    if not check:
        return jsonify({"error": "wrong"}), 400

    
    if not type_:
        return check

    if type_ in ["collection", "folder"]:
        collection = get_table_by_id(value, api=api)
        return collection
    elif type_ == "file":
        folders = check["folders"]
        for folder in folders:
            folder_name = folder["id"]
            if folder_name != value:
                return jsonify({"error": "wrong api"}), 400
            
        file = get_file(file_id=value, api=api)
        return file
    else:
        return jsonify({"error": "type is invalid"}), 400

def get_file(api, file_id):
    try:
        file = bot.get_file(file_id)

        resp = requests.get(file.file_path, stream=True)
        image_bytes = BytesIO(resp.content)

        kind = filetype.guess(image_bytes.getvalue())

        if kind:
            filename = f"file.{kind.extension}"
            mime_type = kind.mime
        else:
            filename = "file.bin"
            mime_type = "application/octet-stream"

        image_bytes.seek(0)

        response = send_file(
            image_bytes,
            mimetype=mime_type,
            as_attachment=True,
            download_name=filename,
        )

        # response.headers["Access-Control-Expose-Headers"] = (
        #     "Content-Disposition"
        # )

        return response

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
@app.route("/create", methods=["POST"])
def create():
    body = request.get_json()
    email = body.get("email")
    password = body.get("password")
    name = body.get("name")
    
    
    if email is None:
        return jsonify({"error":"No email was provided"})
    
    if password is None:
        return jsonify({"error":"No password was provided"})
    
    fwd_msg = get_tables()
    data = get_json(fwd_msg=fwd_msg)
    if isinstance(data, Response):
        return data
    
    for value in data["tables"]:
        if value["email"] == email:
            if name is not None and name != "":
                return jsonify({"error":"This email is already in use"})
            elif value["password"] == password:
                return value
            return jsonify({"error":"Wrong password"})
    if name is None or name == "":
        return jsonify({"error":"Wrong email"})
    chars = string.ascii_letters + string.digits
    api_suffix = "api_" + ''.join(secrets.choice(chars) for _ in range(40)) + f"{data['length']}"
    key = "key_" + ''.join(secrets.choice(chars) for _ in range(20))

    new_table_msg_id = create_new_table(api=api_suffix)

    new_entry = {
        "api": api_suffix,
        "key": key,
        "id": new_table_msg_id,
        "email": str(email),
        "password": str(password),
        "name": str(name)
    }
    data["tables"].append(new_entry)
    data["length"] = len(data["tables"])
    save_new_table(data)

    return new_entry

@app.route("/upload/<string:path>/<string:api>", methods=["POST"])
def upload(path, api):
    if path == "file":
        return upload_file(api=api)
    elif path == "collection":
        return collection(api=api, path=path)
    else:
        return "error: no path", 400

def collection(api, path):
    body = request.get_json()
    collection_header = body.get("collection")
    id_header = body.get("id")  
    action = body.get("action")
    data_header = body.get("data")

    if collection_header is None:
        return jsonify({"error": "collection header missing"}), 400
    if action is None:
        return jsonify({"error": "action header missing"}), 400
    if action not in {"add", "edit", "delete", "create", "erase"}:
        return jsonify({"error": "invalid action"}), 400

    parsed_data = None
    if action not in {"delete", "create", "erase"}:
        if data_header is None:
            return jsonify({"error": "data header missing"}), 400
        try:
            parsed_data = json.loads(data_header)
        except json.JSONDecodeError:
            return jsonify({"error": "Invalid JSON in 'data' header"}), 400

    table = check_api(api=api)
    if table is False:
        return jsonify({"error": "WRONG API"}), 404

    collections = table.get("collections", [])

    if action == "create":
        new_collection_id = create_new_table(api=api, new_table=[])
        new_collection = {"name": f"{collection_header}", "id": new_collection_id}
        collections.append(new_collection)
        table["collections"] = collections
        save_new_table(new_table=table, id=table["id"], name=api)
        return jsonify(new_collection)

    target_collection_id = None
    for c in collections:
        if str(c.get("id")) == str(collection_header):
            target_collection_id = c.get("id")
            break

    if target_collection_id is None:
        return jsonify({"error": "collection id not found"}), 404
    
    if action == "erase":
        for c in collections:
            if c.get("id") == collection_header:
                collections.remove(c)
                table["collections"] = collections
                save_new_table(new_table=table, id=table["id"], name=api)
                return jsonify({"success": "collection was erased"})
        return jsonify({"error": "wrong collection id"})
    
    collection_by_id = get_table_by_id(id=target_collection_id, api=api)
    if isinstance(collection_by_id, Response):  
        return collection_by_id

    
    if not isinstance(collection_by_id, list):
        return jsonify({"error": "collection storage is not a list"}), 500

    result = {"error": "no action performed"}
    if action == "add":
        if not isinstance(parsed_data, dict):
            return jsonify({"error": "data must be a JSON object"}), 400
        parsed_data["id"] = str(uuid4()) + str(len(collection_by_id))
        collection_by_id.append(parsed_data)
        result = parsed_data

    else:
        if id_header is None:
            return jsonify({"error": "id header missing"}), 400

        value_by_id = None
        index = None
        for i, item in enumerate(collection_by_id):
            if str(item.get("id")) == str(id_header):
                value_by_id = item
                index = i
                break

        if value_by_id is None:
            return jsonify({"error": "item id not found in collection"}), 404

        if action == "edit":
            if not isinstance(parsed_data, dict):
                return jsonify({"error": "data must be a JSON object"}), 400
            parsed_data.setdefault("id", value_by_id.get("id"))
            collection_by_id[index] = parsed_data
            result = parsed_data

        elif action == "delete":
            collection_by_id.pop(index)
            result = {"success": "item was removed"}

    save_new_table(new_table=collection_by_id, id=target_collection_id, name=f"collection_{target_collection_id}_{api}")
    return jsonify(result)

def to_json_file(data, name="data"):
    json_str = json.dumps(data, indent=2)
    byte_stream = io.BytesIO(json_str.encode('utf-8'))
    byte_stream.name = f"{name}.json"
    return byte_stream

def get_tables():
    id_msg = bot.forward_message(chat_id=DEBUG_USER_ID, from_chat_id=GROUP_CHAT_ID, message_id=ID)
    if not getattr(id_msg, "text", None):
        raise RuntimeError("Pointer message has no text to read the table id from.")
    pointer = int(id_msg.text)
    fwd_msg = bot.forward_message(
        chat_id=DEBUG_USER_ID,
        from_chat_id=GROUP_CHAT_ID,
        message_id=pointer
    )
    return fwd_msg

def save_file(file):
    data = bot.send_document(
        chat_id=GROUP_CHAT_ID,
        document=InputFile(file, file.filename),
        timeout=3600
    )

    if hasattr(data, "document") and data.document:
        return data.document.file_id

    if hasattr(data, "photo") and data.photo:
        return data.photo[-1].file_id

    if hasattr(data, "video") and data.video:
        return data.video.file_id

    if hasattr(data, "audio") and data.audio:
        return data.audio.file_id

    if hasattr(data, "voice") and data.voice:
        return data.voice.file_id

    if hasattr(data, "animation") and data.animation:
        return data.animation.file_id

    if hasattr(data, "video_note") and data.video_note:
        return data.video_note.file_id

    if hasattr(data, "sticker") and data.sticker:
        return data.sticker.file_id

    return None

        
def save_new_table(new_table, id=ID, name="data"):
    data = bot.send_document(
        chat_id=GROUP_CHAT_ID,
        document=InputFile(to_json_file(new_table, name=name))
    )
    try:
        bot.edit_message_text(chat_id=GROUP_CHAT_ID, message_id=id, text=str(data.message_id))
    except TelegramError:
        bot.send_message(chat_id=GROUP_CHAT_ID, text=str(data.message_id))
    return data.message_id

def create_new_table(api, new_table=None):
    if new_table is None:
        new_table = {
            "api": f"{api}",
            "folders": [],
            "collections": []
        }
    table_msg = bot.send_document(chat_id=GROUP_CHAT_ID, document=InputFile(to_json_file(new_table, name=api)))
    msg = bot.send_message(chat_id=GROUP_CHAT_ID, text=str(table_msg.message_id))
    return msg.message_id

def get_table_by_id(id, api ):
    fwd_id = bot.forward_message(
        chat_id=DEBUG_USER_ID,
        from_chat_id=GROUP_CHAT_ID,
        message_id=id
    )
    if not getattr(fwd_id, "text", None):
        return jsonify({"error": "Folder doesn't exist"}), 400
    fwd_table = bot.forward_message(
        chat_id=DEBUG_USER_ID,
        from_chat_id=GROUP_CHAT_ID,
        message_id=int(fwd_id.text)
    )
    return get_json(fwd_table, api= api)

def upload_file(api):
    body = request.form or request.json
    id_header = body.get("id")
    action = body.get("action")
    name = body.get("name")
    
    if action is None:
        return jsonify({"error": "action is missing"}), 400
    if action not in {"add", "delete", "create"}:
        return jsonify({"error": "invalid action"}), 400
    if name is None or name == "":
        return jsonify({"error": "name is missing"}), 400
        
    table = check_api(api=api)
    if table is False:
        return jsonify({"error": "WRONG API"}), 404

    folders= table.get("folders", [])

    if action == "create":
        new_folder_id = create_new_table(api=f"folder_{api}", new_table=[])
        folders.append({"name": f"{name}", "id": new_folder_id})
        table["folders"] = folders
        save_new_table(new_table=table, id=table["id"], name=api)
        return jsonify({"success": f"new folder was created: {name}", "id": new_folder_id})

    target_folder_id = None
    for c in folders:
        if str(c.get("id")) == str(name):
            target_folder_id = c.get("id")
            break

    if target_folder_id is None:
        return jsonify({"error": "folder id not found"}), 404

    folder_by_id = get_table_by_id(id=target_folder_id, api= api)
    if isinstance(folder_by_id, Response):  
        return folder_by_id

    if not isinstance(folder_by_id, list):
        return jsonify({"error": "collection storage is not a list"}), 500

    result = {"error": "no action performed"}

    if action == "add":
        if "file" not in request.files:
            return jsonify({"error": "No file part in request"}), 400

        file = request.files["file"]
        if file.filename == "":
            return jsonify({"error": "No selected file"}), 400


        saved_file_id = save_file(file=file)

        new_data = {"name": f"{file.filename}", "id": saved_file_id}
        folder_by_id.append(new_data)
        result = {"success": "file was added"}
        

    else:
        if id_header is None:
            return jsonify({"error": "id header missing"}), 400

        value_by_id = None
        index = None
        for i, item in enumerate(folder_by_id):
            if str(item.get("id")) == str(id_header):
                value_by_id = item
                index = i
                break

        if value_by_id is None:
            return jsonify({"error": "file id not found in folder"}), 404


        elif action == "delete":
            folder_by_id.pop(index)
            result = {"success": "file was deleted"}

    save_new_table(new_table=folder_by_id, id=target_folder_id, name=f"folder_{target_folder_id}_{api}")
    return jsonify(result)

    
    


def get_json(fwd_msg, api= None):
    try:
        if not fwd_msg.document:
            return jsonify({"error": "No document found in the message"}), 400
        if not fwd_msg.document.file_name.endswith(".json"):
            return jsonify({"error": "The document is not a .json file"}), 400
        if api is not None:
            if api not in fwd_msg.document.file_name:
                return jsonify({"error": "wrong api"}), 400
        file = bot.get_file(fwd_msg.document.file_id)
        file_content = file.download_as_bytearray()
        json_data = json.loads(file_content.decode("utf-8"))
        return json_data

    except TelegramError as e:
        return jsonify({"error": getattr(e, 'message', str(e))}), 400
    except (json.JSONDecodeError, UnicodeDecodeError):
        return jsonify({"error": "Failed to parse the .json file"}), 400

def check_api(api):
    fwd_msg = get_tables()
    data = get_json(fwd_msg=fwd_msg)
 

    tables = data.get("tables", [])
    for table in tables:
        if table.get("api") == api:
            tdata = get_table_by_id(id=table["id"], api= None)
          
            tdata["id"] = table["id"]
            return tdata
    return False

def run_telegram_bot():
    updater = Updater(token=BOT_TOKEN, use_context=True)
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    threading.Thread(target=run_telegram_bot, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, use_reloader=False)
