import os
from datetime import datetime
from uuid import uuid4
import tempfile
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from sqlmodel import Field, Session, SQLModel, select
from auth import get_current_user
from database import get_session
from user import User

# Utilisation exclusive de la librairie officielle Synology
from synology_api import filestation

# ==============================================================================
# CONFIGURATION GLOBALE
# ==============================================================================
# On garde l'IP du LAN 2, mais on laisse le package gérer les tentatives
NAS_LOCAL_IP = "192.168.1.40"
NAS_PORT = "5000"
NAS_USER = "Loan_Admin"
NAS_PASSWORD = "xM3nvd/l"
NAS_STORAGE_DIR = "/RTVC_DATA2/DISQUES_UR/DISQUES DUR" 

router = APIRouter(prefix="/documents", tags=["Videos"])


class Media(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, index=True, nullable=False)
    title: str = Field(index=True)
    filename: str
    file_path: str
    size_mb: float
    duration_seconds: float = Field(default=0.0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    user_id: str = Field(foreign_key="user.id", index=True, nullable=False)


# ==============================================================================
# CONNEXION AUTO-GÉRÉE (ANTI-CRASH)
# ==============================================================================
def get_nas_client():
    """
    Initialise la session FileStation. Si le lien direct échoue, 
    le package applique ses règles de secours internes sans faire crasher l'API.
    """
    try:
        fl = filestation.FileStation(
            ip_address=NAS_LOCAL_IP,
            port=NAS_PORT,
            username=NAS_USER,
            password=NAS_PASSWORD,
            secure=False,
            dsm_version=7
        )
        return fl
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Échec d'authentification de l'API Synology : {e}"
        )


# ==============================================================================
# ROUTE 1 : UPLOAD
# ==============================================================================
@router.post("/upload", response_model=Media, status_code=status.HTTP_201_CREATED)
async def upload_video(
    title: str = Form(...),
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="Fichier invalide.")

    temp_dir = tempfile.gettempdir()
    temp_path = os.path.join(temp_dir, f"upload_{uuid4()}_{file.filename}")
    
    content = await file.read()
    with open(temp_path, "wb") as f:
        f.write(content)

    try:
        fl = get_nas_client()
        # Envoi via la méthode native du package
        upload_result = fl.upload_file(
            dest_path=NAS_STORAGE_DIR,
            file_path=temp_path
        )
        
        # Sécurité de lecture du dictionnaire pour le linter
        if not isinstance(upload_result, dict) or not upload_result.get("success"):
            raise Exception(f"Le Synology a refusé le fichier : {upload_result}")

        file_size_mb = len(content) / (1024 * 1024)
        remote_path = f"{NAS_STORAGE_DIR}/{file.filename}"

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur d'upload NAS : {e}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    new_video = Media(
        id=str(uuid4()),
        title=title,
        filename=file.filename,
        file_path=remote_path,
        size_mb=round(file_size_mb, 2),
        user_id=current_user.id
    )
    session.add(new_video)
    session.commit()
    session.refresh(new_video)
    return new_video


# ==============================================================================
# ROUTE 2 : STREAMING VIA LE FLUX DU PACKAGER
# ==============================================================================
@router.get("/{media_id}/stream")
async def stream_video(media_id: str, session: Session = Depends(get_session)):
    media = session.get(Media, media_id)
    if not media:
        raise HTTPException(status_code=404, detail="Vidéo introuvable.")

    try:
        import httpx # type: ignore
        fl = get_nas_client()
        sid = getattr(fl, "_sid", getattr(fl, "sid", ""))
        
        # On demande au package de nous donner son URL d'accès active
        base_url = f"http://{NAS_LOCAL_IP}:{NAS_PORT}"
        stream_url = f"{base_url}/webapi/entry.cgi?api=SYNO.FileStation.Download&version=2&method=download&path={media.file_path}&mode=download&_sid={sid}"

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur d'accès au fichier : {e}")

    async def video_stream():
        async with httpx.AsyncClient(verify=False) as client:
            async with client.stream("GET", stream_url) as r:
                async for chunk in r.aiter_bytes(chunk_size=1024 * 512):
                    yield chunk

    return StreamingResponse(
        video_stream(),
        media_type="video/mp4",
        headers={
            "Content-Disposition": f'inline; filename="{media.filename}"',
            "Accept-Ranges": "bytes",
        }
    )


# ==============================================================================
# ROUTE 3 : LISTE LOCALE
# ==============================================================================
@router.get("/", response_model=list[Media])
def list_medias(session: Session = Depends(get_session)):
    return session.exec(select(Media)).all()


# ==============================================================================
# ROUTE 4 : DIAGNOSTIC (CORRIGÉ ET SÉCURISÉ)
# ==============================================================================
@router.get("/nas/files")
async def list_nas_files():
    """
    Utilise la méthode native du package Synology pour lister les fichiers.
    """
    try:
        fl = get_nas_client()
        
        # Utilisation de la vraie méthode du package synology-api
        result = fl.get_file_list(folder_path=NAS_STORAGE_DIR)
        
        if not isinstance(result, dict) or not result.get("success"):
            error_detail = result.get("error") if isinstance(result, dict) else result
            raise Exception(f"FileStation a répondu : {error_detail}")

        raw_data = result.get("data", {})
        if not isinstance(raw_data, dict):
            raw_data = {}
            
        files_list_raw = raw_data.get("files", [])
        files_list = [f["name"] for f in files_list_raw if isinstance(f, dict) and "name" in f]
        
        return {
            "status": "Succès de connexion !",
            "ip_nas": NAS_LOCAL_IP,
            "dossier": NAS_STORAGE_DIR,
            "fichiers_trouves": len(files_list),
            "liste": files_list
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Erreur de communication avec le Synology : {e}"
        )