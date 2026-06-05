from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select, text, col
from typing import List, Dict, Any

from database import get_session
from documents import Media
from auth import get_current_user
from user import User

router = APIRouter(prefix="/search", tags=["Moteur de Recherche"])

@router.get("/", response_model=Dict[str, Any])
def advanced_search_videos(
    q: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Moteur de recherche avancé :
    1. Recherche stricte par mot-clé (ILIKE).
    2. Recherche tolérante aux fautes d'orthographe (pg_trgm).
    3. Suggestions automatiques si rien n'est trouvé.
    """
    keyword = q.strip()
    if not keyword:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le terme de recherche ne peut pas être vide."
        )

    # --- 1. RECHERCHE STRICTE ---
    statement_strict = select(Media).where(col(Media.title).ilike(f"%{keyword}%"))
    results = session.exec(statement_strict).all()

    # --- 2. RECHERCHE FLUE / TOLÉRANTE AUX FAUTES ---
    if not results:
        statement_fuzzy = (
            select(Media)
            .where(text("similarity(title, :kw) > 0.2"))
            .order_by(text("similarity(title, :kw) DESC"))
            .params({"kw": keyword})
        )
        results = session.exec(statement_fuzzy).all()

    # --- 3. LES SUGGESTIONS DE SECOURS ---
    if not results:
        statement_suggestions = select(Media).order_by(col(Media.created_at).desc()).limit(3)
        suggestions = session.exec(statement_suggestions).all()
        
        return {
            "search_type": "no_results_found",
            "message": f"Aucun résultat pour '{keyword}'. Voici des vidéos récentes :",
            "results": suggestions
        }

    return {
        "search_type": "success",
        "total_results": len(results),
        "results": results
    }