import os
from pydantic_settings import BaseSettings # type: ignore

class Settings(BaseSettings):
    NAS_LOCAL_IP: str = "192.168.1.40"
    NAS_PORT: str = "5000"
    NAS_USER: str = "Loan_Admin"
    NAS_PASSWORD: str = "xM3nvd/l"
    NAS_STORAGE_DIR: str = "/RTVC_DATA2/DISQUES_UR/DISQUES DUR"
    ENV_MODE: str = "local"

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()