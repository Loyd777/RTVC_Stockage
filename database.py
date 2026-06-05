from sqlmodel import create_engine, Session

# ⚠️ MODIFIE CES 4 LIGNES AVEC TES PARAMÈTRES POSTGRES LOCAUX
DB_USER = "postgres"          # Ton nom d'utilisateur Postgres (souvent postgres)
DB_PASSWORD = "4Y1L04N" # Le mot de passe de ta base de données
DB_HOST = "localhost"         # Puisque Postgres tourne sur ta machine
DB_PORT = "5432"              # Le port par défaut de Postgres
DB_NAME = "Media_RTVC" # Le nom exact de la base où tu as créé les tables

# Construction de l'adresse de connexion
DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# L'Engine est le moteur qui gère le pool de connexions à Postgres
# echo=True permet de voir dans ton terminal le SQL généré automatiquement (génial pour ton rapport)
engine = create_engine(DATABASE_URL, echo=True)

# Cette fonction (un générateur) sera utilisée par nos routes FastAPI.
# Elle ouvre une session pour chaque requête (ex: une inscription) et la referme proprement après.
def get_session():
    with Session(engine) as session:
        yield session
        