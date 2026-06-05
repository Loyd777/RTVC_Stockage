from typing import Optional, Dict, List
from pydantic import BaseModel, EmailStr
from argon2 import PasswordHasher
from sqlmodel import SQLModel, Field, Session, select
from fastapi import APIRouter, HTTPException, status, Depends
from uuid import uuid4 # <-- On importe le générateur d'UUID de Python
from fastapi.security import OAuth2PasswordRequestForm



# Import de la connexion à la base de données
from database import get_session
from auth import create_access_token

ph = PasswordHasher()

# --- MODÈLE DE DONNÉES DEVENU ULTRA SÉCURISÉ ---
class User(SQLModel, table=True):
    # L'id devient un texte indexé, unique, généré automatiquement par uuid4()
    id: str = Field(
        default_factory=lambda: str(uuid4()), 
        primary_key=True, 
        index=True, 
        nullable=False
    )
    username: str = Field(index=True, unique=True)
    email: str = Field(unique=True)
    hashed_password: str

# --- SCHÉMAS DE RÉCEPTION (PYDANTIC) ---
class UserCreate(BaseModel):
    username: str
    email: EmailStr
    password: str


# --- DÉFINITION DU ROUTEUR ---
router = APIRouter(prefix="/users", tags=["Users"])


# --- ROUTES ---

@router.post("/", response_model=User, status_code=status.HTTP_201_CREATED)
def create_user(user_data: UserCreate, session: Session = Depends(get_session)):
    """
    Route pour créer un utilisateur, générer son UUID et l'enregistrer en BDD
    """
    # Vérifier si l'utilisateur existe déjà
    existing_user = session.exec(
        select(User).where((User.username == user_data.username) | (User.email == user_data.email))
    ).first()
    
    if existing_user:
        raise HTTPException(status_code=400, detail="Username ou Email déjà utilisé.")
    
    # Hachage du mot de passe
    password_securise = ph.hash(user_data.password)
    
    # Création de l'utilisateur (l'UUID se génère tout seul ici !)
    new_user = User(
        username=user_data.username,
        email=user_data.email,
        hashed_password=password_securise
    )
    
    session.add(new_user)
    session.commit()
    session.refresh(new_user)
    
    # On renvoie l'objet utilisateur complet
    return new_user

@router.get("/")
def list_users(session: Session = Depends(get_session)):
    """
    Route pour lister les utilisateurs enregistrés en Base de Données
    """
    users = session.exec(select(User)).all()
    return [{"id": u.id, "username": u.username, "email": u.email} for u in users]

@router.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends(), session: Session = Depends(get_session)):
    """
    Route de connexion : Vérifie le mot de passe et renvoie le Token JWT
    """
    # 1. Chercher l'utilisateur en BDD
    user = session.exec(select(User).where(User.username == form_data.username)).first()
    if not user:
        raise HTTPException(status_code=400, detail="Identifiants incorrects")
        
    # 2. Vérifier le mot de passe haché avec Argon2
    try:
        ph.verify(user.hashed_password, form_data.password)
    except Exception:
        raise HTTPException(status_code=400, detail="Identifiants incorrects")
        
    # 3. Générer le Token si tout est correct
    access_token = create_access_token(data={"sub": user.username})
    
    return {"access_token": access_token, "token_type": "bearer"}