import requests
import json

response = requests.post(
    "http://localhost:5000/submit",
    json={
        "text": "The sun dipped below the horizon, painting the sky in hues of amber and rose. I sat on the porch, coffee in hand, watching the neighborhood slowly go quiet.",
        "creator_id": "test-user-1"
    }
)

print("Status code:", response.status_code)
print("Raw response:", response.text)
