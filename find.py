from dotenv import load_dotenv
import os

load_dotenv()
GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")

print(GOOGLE_PLACES_API_KEY[:10])   