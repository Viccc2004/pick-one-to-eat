from dotenv import load_dotenv
import os
import requests

load_dotenv()
GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")

#print(GOOGLE_PLACES_API_KEY[:10])   

url = "https://places.googleapis.com/v1/places:searchText"

headers = {
    "Content-Type": "application/json",             # Specify that the request body is in JSON format
    "X-Goog-Api-Key": GOOGLE_PLACES_API_KEY,        # Use the API key from the environment variable
    "X-Goog-FieldMask": "places.displayName,places.formattedAddress",          
}

body = {
    "textQuery": "chinese restaurants in Allston Boston",
}

response = requests.post(url, headers=headers, json=body)
print(response.status_code)
print(response.text)