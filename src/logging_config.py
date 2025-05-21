
import logging
import os 

LOG_DIR = "output"
os.makedirs(LOG_DIR, exist_ok=True)

APP_LOG_FILE = os.path.join(LOG_DIR, "app.log")
SERVER_LOG_FILE = os.path.join(LOG_DIR, "server.log") 

logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", 
    handlers=[
        logging.FileHandler(APP_LOG_FILE, encoding='utf-8'), 
        logging.StreamHandler() 
    ]
)
logger = logging.getLogger(__name__)
__all__ = ['logger'] 
logger.info("Configuração de logging inicializada.")