import requests

try:
    res = requests.post('http://127.0.0.1:5000/api/auth/login', json={
        'username': 'admin',
        'password': 'admin123',
        'rememberMe': False
    })
    print(res.status_code)
    print(res.json())
except Exception as e:
    print("Error:", e)
