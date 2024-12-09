import chromadb
from chromadb.config import Settings
import os
from dotenv import load_dotenv
import chromadb.utils.embedding_functions as embedding_functions
import json
from langchain.schema import Document
import logging
import uuid
import hashlib
from datetime import datetime
import psycopg
import pandas as pd

load_dotenv()

logging_level = os.getenv("LOGGING_LEVEL", "DEBUG")

if logging_level == "DEBUG":
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
elif logging_level == "INFO":
    logging.basicConfig(level=logging.INFO)

logger = logging.getLogger(__name__)

os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY")

chroma_host = os.getenv("CHROMADB_HOST", "localhost")
chroma_port = int(os.getenv("CHROMADB_PORT", 3003))

postgres_user = os.getenv("POSTGRES_USER")
postgres_password = os.getenv("POSTGRES_PASSWORD")
postgres_db = os.getenv("POSTGRES_DB")
postgres_host = os.getenv("POSTGRES_HOST", "localhost")
postgres_port = os.getenv("POSTGRES_PORT", 5432)

path = os.getenv("DATA_PATH", "data/json")

global conn

conn = psycopg.connect(
    dbname=postgres_db,
    user=postgres_user,
    password=postgres_password,
    host=postgres_host,
    port=postgres_port
)

def insert_collection(collection_name, description, host, port, search_k, hash_, last_update):
    with conn.cursor() as cursor:
        cursor.execute("""
            INSERT INTO collections ("collectionName", "description", "host", "port", "searchK", "hashCollection", "lastUpdate")
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (collection_name, description, host, port, search_k, hash_, last_update))
        conn.commit()
        logger.info(f"Collection {collection_name} inserted")

def update_collection(collection_name, description, host, port, search_k, hash_, last_update):
    with conn.cursor() as cursor:
        cursor.execute("""
            UPDATE collections
            SET "description" = %s, "host" = %s, "port" = %s, "searchK" = %s, "hashCollection" = %s, "lastUpdate" = %s
            WHERE "collectionName" = %s
        """, (description, host, port, search_k, hash_, last_update, collection_name))
        conn.commit()
        logger.info(f"Collection {collection_name} updated")

def update_search_k(collection_name, search_k):
    with conn.cursor() as cursor:
        cursor.execute("""
            UPDATE collections
            SET "searchK" = %s
            WHERE "collectionName" = %s
        """, (search_k, collection_name))
        conn.commit()
        logger.info(f"search_k for collection {collection_name} updated")

def get_hash(collection_name):
    with conn.cursor() as cursor:
        cursor.execute('SELECT "hashCollection" FROM "collections" WHERE "collectionName" = %s', (collection_name,))
        hash_ = cursor.fetchone()
        return hash_

def generate_hash(data):
    hash_object = hashlib.sha256()
    hash_object.update(data.encode("utf-8"))
    return hash_object.hexdigest()

def pretty_print(json_data):
    return json.dumps(json_data, indent=4, ensure_ascii=False)

def save_to_json_file(data, file_path):
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def open_json_file(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)

def hash_file_content(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        data = f.read()
        hash_object = hashlib.sha256()
        hash_object.update(data.encode("utf-8"))
        return hash_object.hexdigest()
    
def create_documents(data, metadata):
    documents = []
    for d in data:
        documents.append(Document(page_content=json.dumps(d, ensure_ascii=False), metadata=metadata))
        logger.debug(f"Document created: {pretty_print(documents[-1].page_content)}")
        logger.debug(f"Metadata: {pretty_print(documents[-1].metadata)}")
    return documents

def test_connection():
    with conn.cursor() as cursor:
        cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
        tables = cursor.fetchall()
        logger.info(f"Tables: {tables}")
        for table in tables:
            cursor.execute(f"SELECT column_name FROM information_schema.columns WHERE table_name = '{table[0]}'")
            columns = cursor.fetchall()
            logger.info(f"Columns of {table[0]}: {columns}")

def process_json_all():
    datas = open_json_file("data/ALL.json")
    datas = json.loads(datas[0])
    data_list = []
    for data in datas:
        if isinstance(data, list):
            for d in data:
                data_list.append(d)

    for i, data in enumerate(data_list):
        desc = data.get("desc", None)[0]
        str_id = generate_hash(desc)[:63]
        save_to_json_file(data, f"{path}/{str_id}.json")

if __name__ == "__main__":
    search = input("Quel pourcentage de données voulez-vous pour le search_k ?(ex: 70) ")
    if not search.isdigit():
        logger.error("Veuillez entrer un nombre")
        exit()
    elif int(search) < 0 or int(search) > 100:
        logger.error("Veuillez entrer un nombre entre 0 et 100")
        exit()
    if not os.path.exists(path):
        os.makedirs(path)
        logger.info(f"Directory {path} created")
        process_json_all()
    elif len(os.listdir(path)) == 0:
        process_json_all()
        logger.info(f"Directory {path} is empty, adding JSON files from ALL.json")
    try:
        list_files = os.listdir(path)
        json_to_save_db = {
            "all_collections": []
        }
        
        for f in list_files:
            data = open_json_file(f"{path}/{f}")
            collection_name = f.split(".")[0]
            description = data.get("desc", None)[0]
            data = data.get("data", None)          
            new_data_hash = hash_file_content(f"{path}/{f}")

            data_to_save_db = {
                "collection": collection_name,
                "description": description,
                "host": chroma_host,
                "port": chroma_port,
                "search_k": int(len(data) * (int(search) / 100)),
                "hash": new_data_hash,
                "last_update": datetime.now().isoformat()
            }

            metadata = {
                "collection": collection_name,
                "description": description,
                "last_update": datetime.now().isoformat()
            }

            json_to_save_db["all_collections"].append(data_to_save_db)
            
            logger.info(f"Update needed for collection {collection_name}")
            update_collection(data_to_save_db["collection"], data_to_save_db["description"], data_to_save_db["host"], data_to_save_db["port"], data_to_save_db["search_k"], data_to_save_db["hash"], data_to_save_db["last_update"])
            logger.info(f"search_k for collection {collection_name} updated")
    except Exception as e:
        logger.error(f"Error during script execution: {str(e)}")
    finally:
        conn.close()
        logger.info("Database connection closed")
