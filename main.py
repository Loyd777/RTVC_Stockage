import os
from fastapi import FastAPI, HTTPException, status, Depends
from sqlmodel import SQLModel
from fastapi.middleware.cors import CORSMiddleware

# Import de la base de données locale
from database import engine

# Import des routeurs de tes modules
import documents
import search
import user

# Initialisation de l'application FastAPI
app = FastAPI(title="Mini Vimeo API - Système de Stockage et Streaming")

# Configuration du middleware CORS pour ton interface HTML5
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],             # Autorise ton fichier HTML à lire l'API
    allow_credentials=True,
    allow_methods=["*"],             
    allow_headers=["*"],             
)

# --- CONFIGURATION DU STOCKAGE DISTANT ---
USE_LOCAL_SIMULATION = False 


@app.on_event("startup")
def on_startup():
    """
    Synchronisation de la base de données au démarrage.
    """
    SQLModel.metadata.create_all(engine)
    print("=== [BDD] Tables PostgreSQL synchronisées ===")
    print("=== [STOCKAGE] Mode NAS Distant (SFTP) Activé ===")


@user.router.get("/files")
def list_remote_nas_files():
    """
    Liste les fichiers du NAS Synology distant en utilisant l'API File Station
    et l'ID QuickConnect (Contourne le blocage de la box internet).
    """
    from documents import get_synology_filestation, NAS_STORAGE_DIR

    try:
        # 1. Connexion à File Station via QuickConnect
        fl = get_synology_filestation()
        
        # 2. Lecture dynamique du dossier distant pour contourner Pylance
        list_func = getattr(fl, "get_file_list")
        result = list_func(folder_path=NAS_STORAGE_DIR)
        
        # 3. Extraction sécurisée des noms de fichiers (évite l'erreur d'indexation sur type object)
        files_list = []
        if isinstance(result, dict) and "data" in result:
            data_content = result["data"]
            if isinstance(data_content, dict) and "files" in data_content:
                for f in data_content["files"]:
                    if isinstance(f, dict) and "name" in f:
                        files_list.append(f["name"])
        
        return {
            "status": "success",
            "mode": "nas_quickconnect_production",
            "total_files": len(files_list),
            "files": files_list
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Impossible de lister les fichiers via QuickConnect : {e}"
        )


# --- INCLUSION DES ROUTEURS DE L'APPLICATION ---
app.include_router(user.router)       # Module de gestion des utilisateurs
app.include_router(documents.router)  # Module des vidéos (Enregistrement / Streaming)
app.include_router(search.router)     # Module du moteur de recherche interne