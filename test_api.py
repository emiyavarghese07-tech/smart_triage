import requests
import json
body = {"message": "I have a mild headache.", "history": []}
try:
    res = requests.post('http://127.0.0.1:5000/api/chatbot', json=body)
    print(res.status_code, res.text)
except Exception as e:
    print('Req error', e)
