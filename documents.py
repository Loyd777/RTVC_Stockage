import os
import ffmpeg  # type: ignore
from datetime import datetime
from uuid import uuid4
import tempfile
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from sqlmodel import Session, Field, SQLModel, select
from auth import get_current_user
from database import get_session
from user import User
from config import settings

# Utilisation exclusive de la librairie officielle Synology
from synology_api import filestation

router = APIRouter(prefix="/documents", tags=["Videos"])

# Variables de configuration globales
NAS_LOCAL_IP = settings.NAS_LOCAL_IP
NAS_PORT = settings.NAS_PORT
NAS_USER = settings.NAS_USER
NAS_PASSWORD = settings.NAS_PASSWORD
NAS_STORAGE_DIR = settings.NAS_STORAGE_DIR


# ==============================================================================
# MODÈLE DE DONNÉES LOCAL (SQLModel)
# ==============================================================================
class Media(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, index=True, nullable=False)
    title: str = Field(index=True)
    filename: str
    file_path: str
    thumbnail_path: str | None = Field(default=None)  # Stocke le chemin de la miniature sur le NAS
    size_mb: float
    duration_seconds: float = Field(default=0.0)      # Stocke la durée réelle calculée par FFmpeg
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
# FONCTIONS UTILITAIRES FFMPEG (TRAITEMENT MÉDIA)
# ==============================================================================
def get_video_duration(video_path: str) -> float:
    """
    Utilise ffprobe pour inspecter le fichier temporaire et extraire sa durée exacte.
    """
    try:
        probe = ffmpeg.probe(video_path)
        video_stream = next((stream for stream in probe['streams'] if stream['codec_type'] == 'video'), None)
        if video_stream and 'duration' in video_stream:
            return float(video_stream['duration'])
        elif 'format' in probe and 'duration' in probe['format']:
            return float(probe['format']['duration'])
        return 0.0
    except Exception:
        # Évite de bloquer l'upload si le fichier est légèrement corrompu au niveau des métadonnées
        return 0.0


def extract_thumbnail(video_path: str, output_jpg_path: str, time_seconds: float = 2.0):
    """
    Prend une capture d'écran de la vidéo à un instant donné (2s par défaut)
    et la génère sous forme d'image JPEG locale.
    """
    try:
        (
            ffmpeg
            .input(video_path, ss=time_seconds)
            .output(output_jpg_path, vframes=1)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
    except ffmpeg.Error as e:
        # Log de l'erreur stderr de FFmpeg en console pour le débug
        print("Erreur FFmpeg lors de l'extraction de la miniature :", e.stderr.decode('utf8'))
        raise Exception("Impossible de générer la miniature vidéo.")


# ==============================================================================
# ROUTE 1 : UPLOAD INTELLIGENT (VIDÉO + MINIATURE + CONFIG ENRICHIE)
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

    # Préparation des identifiants et des chemins uniques locaux
    temp_dir = tempfile.gettempdir()
    unique_id = uuid4()
    
    temp_video_path = os.path.join(temp_dir, f"upload_{unique_id}_{file.filename}")
    temp_thumb_filename = f"thumb_{unique_id}_{os.path.splitext(file.filename)[0]}.jpg"
    temp_thumb_path = os.path.join(temp_dir, temp_thumb_filename)
    
    # 1. Écriture physique du fichier vidéo temporaire pour traitement FFmpeg
    content = await file.read()
    with open(temp_video_path, "wb") as f:
        f.write(content)

    try:
        # 2. Exécution des tâches FFmpeg en local
        duration = get_video_duration(temp_video_path)
        
        # Sécurité : Si la vidéo fait moins de 2 secondes, on capture à 0
        thumb_time = 2.0 if duration > 2.0 else 0.0
        extract_thumbnail(temp_video_path, temp_thumb_path, time_seconds=thumb_time)

        # 3. Connexion au client Synology
        fl = get_nas_client()
        
        # 4. Transfert de la vidéo sur le stockage du NAS
        upload_video_result = fl.upload_file(dest_path=NAS_STORAGE_DIR, file_path=temp_video_path)
        if not isinstance(upload_video_result, dict) or not upload_video_result.get("success"):
            raise Exception(f"Le Synology a refusé la vidéo : {upload_video_result}")

        # 5. Transfert de la miniature générée sur le stockage du NAS
        upload_thumb_result = fl.upload_file(dest_path=NAS_STORAGE_DIR, file_path=temp_thumb_path)
        
        # Définition des chemins de destination finaux
        remote_video_path = f"{NAS_STORAGE_DIR}/{file.filename}"
        remote_thumb_path = (
            f"{NAS_STORAGE_DIR}/{temp_thumb_filename}" 
            if isinstance(upload_thumb_result, dict) and upload_thumb_result.get("success") 
            else None
        )

        file_size_mb = len(content) / (1024 * 1024)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors du traitement multimédia ou du transfert NAS : {e}")
    finally:
        # Nettoyage strict des fichiers résiduels locaux du serveur pour éviter de saturer l'espace disque
        if os.path.exists(temp_video_path):
            os.remove(temp_video_path)
        if os.path.exists(temp_thumb_path):
            os.remove(temp_thumb_path)

    # 6. Sauvegarde du modèle enrichi dans ta base SQL locale
    new_video = Media(
        id=str(unique_id),
        title=title,
        filename=file.filename,
        file_path=remote_video_path,
        thumbnail_path=remote_thumb_path,
        size_mb=round(file_size_mb, 2),
        duration_seconds=round(duration, 2),
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
        import httpx  # type: ignore
        fl = get_nas_client()
        sid = getattr(fl, "_sid", getattr(fl, "sid", ""))
        
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