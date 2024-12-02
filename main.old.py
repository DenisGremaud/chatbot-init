import chromadb
import os
from dotenv import load_dotenv
import chromadb.utils.embedding_functions as embedding_functions
import json
import uuid
from langchain.schema import Document
import logging
import hashlib
from datetime import datetime
import psycopg

load_dotenv()

logging_level = os.getenv("LOGGING_LEVEL", "INFO")

if logging_level == "DEBUG":
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
elif logging_level == "INFO":
    logging.basicConfig(level=logging.INFO)

logger = logging.getLogger(__name__)

config_path = os.getenv("CONFIG_PATH", "configs/config.json")

print(f"config_path: {config_path}")

with open(config_path, 'r') as f:
    config = json.load(f)

os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY")

chroma_host = os.getenv("CHROMADB_HOST", "localhost")
chroma_port = int(os.getenv("CHROMADB_PORT", 3003))

postgres_user = os.getenv('POSTGRES_USER')
postgres_password = os.getenv('POSTGRES_PASSWORD')
postgres_db = os.getenv('POSTGRES_DB')
postgres_host = os.getenv('POSTGRES_HOST', 'localhost')
postgres_port = os.getenv('POSTGRES_PORT', 5432)

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
            INSERT INTO collections (collection_name, desc_collection, host, port, search_k, hash_collection, last_update)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (collection_name, description, host, port, search_k, hash_, last_update))
        conn.commit()
        logger.info(f"Collection {collection_name} insérée")

def update_collection(collection_name, description, host, port, search_k, hash_, last_update):
    with conn.cursor() as cursor:
        cursor.execute("""
            UPDATE collections
            SET desc_collection = %s, host = %s, port = %s, search_k = %s, hash_collection = %s, last_update = %s
            WHERE collection_name = %s
        """, (description, host, port, search_k, hash_, last_update, collection_name))
        conn.commit()
        logger.info(f"Collection {collection_name} mise à jour")
    
def update_search_k(collection_name, search_k):
    with conn.cursor() as cursor:
        cursor.execute("""
            UPDATE collections
            SET search_k = %s
            WHERE collection_name = %s
        """, (search_k, collection_name))
        conn.commit()
        logger.info(f"search_k de la collection {collection_name} mis à jour")

def get_hash(collection_name):
    with conn.cursor() as cursor:
        cursor.execute("SELECT hash_collection FROM collections WHERE collection_name = %s", (collection_name,))
        hash_ = cursor.fetchone()
        return hash_

def generate_hash(data):
    hash_object = hashlib.sha256()
    hash_object.update(data.encode('utf-8'))
    return hash_object.hexdigest()

def pretty_print(json_data):
    return json.dumps(json_data, indent=4, ensure_ascii=False)

def convert_value(value, target_type):
    conversion_functions = {
        'integer': lambda x: int(x),
        'float': lambda x: float(x),
        'boolean': lambda x: bool(x),
        'string': lambda x: str(x)
    }
    try:
        return conversion_functions[target_type](value)
    except (ValueError, TypeError, KeyError):
        return None

def process_flat_file(data, file_config, context):
    documents = []
    root_key = file_config.get('root_key')
    
    for key, value in data[root_key].items():
        metadata = {'context': context}
        document_content = {key: value}
        documents.append(Document(page_content=json.dumps(document_content, ensure_ascii=False), metadata=metadata))
        logger.debug(f"Contenu du document: {pretty_print(document_content)}")
        logger.debug(f"Métadonnées: {pretty_print(metadata)}")

    return documents

def process_nested_file(data, file_config, context):
    documents = []
    root_key = file_config.get('root_key')
    nested_structure = file_config.get('nested_structure', [])
    metadata_keys = [item['key'] for item in nested_structure if item.get('add_to_metadata')]
    logger.info(f"Ajout de documents à partir de la clé racine {root_key } et de la structure imbriquée {nested_structure}")

    for root_item, nested_data in data[root_key].items():
        for nested_key, nested_values in nested_data.items():
            metadata = {}
            document_content = {}

            for level in nested_structure:
                key = level['key']
                if level.get('is_root'):
                    value = root_item
                else:
                    value = nested_key
                
                if key in metadata_keys:
                    metadata[key] = value
                document_content[key] = value

                if 'fields' in level:
                    for field in level['fields']:
                        field_name = field['key']

                        if isinstance(nested_values, dict):
                            field_value = nested_values.get(field_name, None)
                        elif isinstance(nested_values, list):
                            try:
                                field_value = nested_values[int(field_name)]
                            except (IndexError, ValueError):
                                field_value = None
                        else:
                            field_value = convert_value(nested_data.get(field_name), field['type'])
                        
                        document_content[field_name] = field_value
                        if field_name in metadata_keys:
                            metadata[field_name] = field_value

            metadata['context'] = context
            documents.append(Document(page_content=json.dumps(document_content, ensure_ascii=False), metadata=metadata))
            logger.debug(f"Contenu du document: {pretty_print(document_content)}")
            logger.debug(f"Métadonnées: {pretty_print(metadata)}")

    return documents

def process_file(file_config, collection):
    documents = []
    file_path = file_config['path']
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            context = data.get('context', '')

            if file_config.get('structure') == 'nested':
                documents = process_nested_file(data, file_config, context)

            elif file_config.get('structure') == 'flat':
                documents = process_flat_file(data, file_config, context)
            
            # elif file_config.get('structure') == 'nested_w_object':
            #     documents = process_nested_with_object(data, file_config, context)
            
        for doc in documents:
            collection.upsert(
                ids=[str(uuid.uuid1())],
                metadatas=[doc.metadata],
                documents=[doc.page_content]
            )
        
        logger.info(f"Ajout de {len(documents)} documents à la collection {collection.name}")
        return int(len(documents) * 0.5)
    except Exception as e:
        logger.error(f"Erreur lors du traitement du fichier {file_path}: {str(e)}")

def process_files(file_configs, collection):
    for file_config in file_configs:
        search_k = process_file(file_config, collection)
    return search_k
        
def hash_file_content(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        data = f.read()
        hash_object = hashlib.sha256()
        hash_object.update(data.encode('utf-8'))
        return hash_object.hexdigest()
   

if __name__ == "__main__":
    try:
        chroma_client = chromadb.HttpClient(host=chroma_host, port=chroma_port)
        openai_ef = embedding_functions.OpenAIEmbeddingFunction(api_key=os.getenv("OPENAI_API_KEY"), model_name=config["embedding_model"])
        json_to_save_db = {"all_collections": []}
        
        for collection_config in config["collections"]:
            collection = chroma_client.get_or_create_collection(name=collection_config["name"], embedding_function=openai_ef)
            if isinstance(collection_config["description"], list):
                description =  " ".join(collection_config["description"])
            else:
                description = collection_config["description"]
                
            new_data_hash = ""
            for file_config in collection_config["files"]:
                file_path = file_config["path"]
                new_data_hash += hash_file_content(file_path)
            logger.info(f"new_data_hash: {new_data_hash}")
            data_to_save_db = { 
                "collection": collection_config["name"], 
                "description": description,
                "host": chroma_host, 
                "port": chroma_port,
                "search_k": 10,
                "hash": new_data_hash, 
                "last_update": datetime.now().isoformat()
            }
            json_to_save_db["all_collections"].append(data_to_save_db)
            
            existing_hash = get_hash(data_to_save_db["collection"])

            if existing_hash is None:
                logger.info(f"Insertion nécessaire pour la collection {data_to_save_db['collection']}")
                insert_collection(data_to_save_db["collection"], data_to_save_db["description"], data_to_save_db["host"], data_to_save_db["port"], data_to_save_db["search_k"], data_to_save_db["hash"], data_to_save_db["last_update"])
                search_k = process_files(collection_config["files"], collection)
                print(f"search_k: {search_k}")
                update_search_k(data_to_save_db["collection"], search_k)
            elif existing_hash[0] != new_data_hash:
                logger.info(f"Mise à jour nécessaire pour la collection {data_to_save_db['collection']}")
                update_collection(data_to_save_db["collection"], data_to_save_db["description"], data_to_save_db["host"], data_to_save_db["port"], data_to_save_db["search_k"], data_to_save_db["hash"], data_to_save_db["last_update"])
                search_k = process_files(collection_config["files"], collection)
                update_search_k(data_to_save_db["collection"], search_k)
            else:
                logger.info(f"Aucune mise à jour nécessaire pour la collection {data_to_save_db['collection']}")

        logger.info(f"all_collections: {pretty_print(json_to_save_db)}")
    except Exception as e:
        logger.error(f"Erreur lors de l'exécution du script: {str(e)}")
    finally:
        conn.close()
        logger.info("Connexion à la base de données fermée")
